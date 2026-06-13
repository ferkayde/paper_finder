#!/usr/bin/env python3
"""
arxiv_digest.py — weekly arXiv digest, weighted toward probability theory.

Pure standard library. No third-party dependencies.

Pulls recent papers from chosen arXiv categories, scores each one against a
keyword/author interest profile, then optionally asks Claude to select the
best papers based on your reading history and interest profile, and add
one-line notes. Falls back to score-based selection if no API key is set.

Usage:
    python arxiv_digest.py --dry-run            # print to stdout, no email
    python arxiv_digest.py --days 7             # look back 7 days (default)
    python arxiv_digest.py                      # fetch + email

Environment variables:
    SMTP_USER           full gmail address
    SMTP_PASS           gmail app password
    MAIL_TO             recipient(s), comma-separated for multiple
    ANTHROPIC_API_KEY   (optional) enables Claude-based selection and notes
"""

import argparse
import datetime as dt
import html
import json
import os
import random
import re
import smtplib
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ──────────────────────────────────────────────────────────────────────────
# CONFIG — tune this freely. This is your "interest profile".
# ──────────────────────────────────────────────────────────────────────────

CATEGORIES = {
    "math.PR": 1.0,   # Probability  ← core
    "math.ST": 0.6,   # Statistics theory
    "math.SP": 0.5,   # Spectral theory
    "math.CO": 0.5,   # Combinatorics (spectral graph theory lives partly here)
    "q-fin.ST": 0.5,  # Statistical finance
    "q-fin.TR": 0.5,  # Trading & market microstructure
    "q-fin.MF": 0.45, # Mathematical finance
    "stat.ML": 0.4,   # Machine learning (diffusion / score-based models)
    "cs.LG": 0.35,    # Learning
}

PROB_CATEGORIES = {"math.PR", "math.ST"}

KEYWORDS = {
    # probability core
    "martingale": 3.0, "stochastic": 1.5, "brownian": 2.5, "sde": 2.0,
    "stochastic differential": 2.5, "ito": 2.0, "fokker-planck": 2.5,
    "stein's method": 4.0, "stein method": 4.0, "concentration inequalit": 3.0,
    "large deviation": 3.0, "extreme value": 3.0, "heavy tail": 2.0,
    "central limit": 2.0, "berry-esseen": 3.5, "coupling": 1.5,
    "markov chain": 2.0, "mixing time": 2.5, "random walk": 2.0,
    "random matrix": 2.0, "poisson approximation": 3.0,
    # spectral graph theory
    "graph energy": 4.0, "laplacian energy": 4.5, "laplacian eigenvalue": 3.5,
    "spectral graph": 3.0, "graph theoery": 3.0, "random graph": 3.0,
    # diffusion / generative
    "diffusion model": 3.0, "score-based": 3.0, "score matching": 3.0,
    "flow matching": 2.5, "generative model": 1.5, "stochastic interpolant": 3.0,
    # quant finance
    "pairs trading": 4.0, "statistical arbitrage": 3.5, "cointegration": 3.0,
    "mean reversion": 2.5, "regime-switching": 3.0, "regime switching": 3.0,
    "market microstructure": 2.0, "illiquidity": 2.5, "limit order book": 2.0,
}

AUTHORS = {
    "islak", "işlak",
    "haggstrom", "häggström",
    "gutman", "embrechts", "goldstein",
    "song",  # Yang Song (score-based models) — note: common surname
}

CATEGORY_BIAS = 2.0
TITLE_WEIGHT = 3.0
ABSTRACT_WEIGHT = 1.0
AUTHOR_BONUS = 8.0

# Final selection quotas.
N_PROB = 3               # new papers from PROB_CATEGORIES
N_OTHER = 2              # new papers from other categories
N_CLASSIC = 3            # curated older papers per week
MIN_SCORE = 0.0

# Candidate pool sizes fed to Claude (must be >= N_PROB / N_OTHER).
CANDIDATE_PROB = 20
CANDIDATE_OTHER = 10

# Seen-papers cache — prevents cross-week repeats.
SEEN_CACHE = "seen_papers.json"
SEEN_MAX_WEEKS = 12      # keep IDs this long
SEEN_RECENT_WEEKS = 4    # titles within this window are sent to Claude as context

# Curated list of important older arXiv IDs to rotate through.
# N_CLASSIC are chosen per ISO week. Add IDs here to grow the pool.
CLASSIC_PAPERS = [
    "2011.13456",  # Song et al. (2021) — Score-Based Generative Modeling through SDEs
    "2210.02747",  # Lipman et al. (2022) — Flow Matching for Generative Modeling
    "1608.04471",  # Liu & Wang (2016) — Stein Variational Gradient Descent
    "2306.07956",  # Vito et al. (2023) — AMCS: refuted 6 open conjectures
    "2406.17763",  # Huang et al. (2024) — DiffusionPDE
]

PAGE_SIZE = 100
MAX_PAGES = 5
REQUEST_PAUSE = 3.0
USER_AGENT = "weekly-arxiv-digest/1.0 (personal research digest)"

ANTHROPIC_MODEL = "claude-sonnet-4-6"

ARXIV_API = "http://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


# ──────────────────────────────────────────────────────────────────────────
# Fetch + parse
# ──────────────────────────────────────────────────────────────────────────

def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def fetch_category(cat, cutoff):
    """Page through a category newest-first until we pass the cutoff date."""
    papers = []
    for page in range(MAX_PAGES):
        params = urllib.parse.urlencode({
            "search_query": f"cat:{cat}",
            "start": page * PAGE_SIZE,
            "max_results": PAGE_SIZE,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        })
        url = f"{ARXIV_API}?{params}"
        try:
            raw = _get(url)
        except Exception as e:                       # noqa: BLE001
            print(f"  ! fetch failed for {cat} page {page}: {e}", file=sys.stderr)
            time.sleep(REQUEST_PAUSE)
            continue

        batch = parse_atom(raw, cat)
        if not batch:
            break
        papers.extend(batch)
        oldest = min(p["published"] for p in batch)
        time.sleep(REQUEST_PAUSE)
        if oldest < cutoff:
            break

    return [p for p in papers if p["published"] >= cutoff]


def _fetch_by_id(arxiv_id):
    """Fetch a single paper by arXiv ID. Returns a paper dict or None."""
    params = urllib.parse.urlencode({"id_list": arxiv_id, "max_results": 1})
    url = f"{ARXIV_API}?{params}"
    try:
        raw = _get(url)
        batch = parse_atom(raw, "classic")
        time.sleep(REQUEST_PAUSE)
        if batch:
            p = batch[0]
            p["score"] = score_paper(p)
            p["is_classic"] = True
            return p
    except Exception as e:                           # noqa: BLE001
        print(f"  ! fetch_by_id failed for {arxiv_id}: {e}", file=sys.stderr)
    return None


def parse_atom(raw, queried_cat):
    root = ET.fromstring(raw)
    out = []
    for entry in root.findall(f"{ATOM}entry"):
        id_url = entry.findtext(f"{ATOM}id", default="").strip()
        m = re.search(r"abs/([^v]+)(v\d+)?$", id_url)
        arxiv_id = m.group(1) if m else id_url

        title = _collapse(entry.findtext(f"{ATOM}title", default=""))
        summary = _collapse(entry.findtext(f"{ATOM}summary", default=""))

        published_str = entry.findtext(f"{ATOM}published", default="")
        try:
            published = dt.datetime.strptime(
                published_str, "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue

        authors = [
            a.findtext(f"{ATOM}name", default="").strip()
            for a in entry.findall(f"{ATOM}author")
        ]
        cats = [
            c.get("term")
            for c in entry.findall(f"{ATOM}category")
            if c.get("term")
        ]

        abs_url = id_url
        pdf_url = id_url.replace("/abs/", "/pdf/")

        out.append({
            "id": arxiv_id,
            "title": title,
            "summary": summary,
            "authors": authors,
            "published": published,
            "categories": cats,
            "queried_cat": queried_cat,
            "abs_url": abs_url,
            "pdf_url": pdf_url,
        })
    return out


def _collapse(text):
    return re.sub(r"\s+", " ", text or "").strip()


# ──────────────────────────────────────────────────────────────────────────
# Seen-papers cache
# ──────────────────────────────────────────────────────────────────────────

def load_seen():
    """Return (set of seen IDs within retention window, list of recent titles)."""
    try:
        with open(SEEN_CACHE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return set(), []

    cutoff = dt.date.today() - dt.timedelta(weeks=SEEN_MAX_WEEKS)
    recent_cutoff = dt.date.today() - dt.timedelta(weeks=SEEN_RECENT_WEEKS)
    seen_ids = set()
    recent_titles = []
    for e in data.get("seen", []):
        try:
            d = dt.date.fromisoformat(e["date"])
        except (KeyError, ValueError):
            continue
        if d >= cutoff:
            seen_ids.add(e["id"])
        if d >= recent_cutoff and e.get("title"):
            recent_titles.append(e["title"])
    return seen_ids, recent_titles


def save_seen(papers):
    """Append newly selected paper IDs and titles to the cache file."""
    try:
        with open(SEEN_CACHE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {"seen": []}

    today = dt.date.today().isoformat()
    existing_ids = {e["id"] for e in data["seen"]}
    for p in papers:
        if p["id"] not in existing_ids:
            data["seen"].append({
                "id": p["id"],
                "date": today,
                "title": p.get("title", ""),
            })

    cutoff = dt.date.today() - dt.timedelta(weeks=SEEN_MAX_WEEKS)
    data["seen"] = [
        e for e in data["seen"]
        if dt.date.fromisoformat(e.get("date", "1900-01-01")) >= cutoff
    ]

    with open(SEEN_CACHE, "w") as f:
        json.dump(data, f, indent=2)


# ──────────────────────────────────────────────────────────────────────────
# Score + candidate pool
# ──────────────────────────────────────────────────────────────────────────

def category_weight(paper):
    return max((CATEGORIES.get(c, 0.0) for c in paper["categories"]), default=0.0)


def score_paper(paper):
    title = paper["title"].lower()
    summary = paper["summary"].lower()

    score = CATEGORY_BIAS * category_weight(paper)

    for kw, w in KEYWORDS.items():
        if kw in title:
            score += TITLE_WEIGHT * w
        elif kw in summary:
            score += ABSTRACT_WEIGHT * w

    names = " ".join(paper["authors"]).lower()
    if any(a in names for a in AUTHORS):
        score += AUTHOR_BONUS

    return score


def is_probability(paper):
    return any(c in PROB_CATEGORIES for c in paper["categories"])


def build_candidate_pool(papers, seen_ids):
    """Score, dedup, exclude already-seen papers, return sorted candidate pools."""
    best = {}
    for p in papers:
        p["score"] = score_paper(p)
        if p["id"] not in best or p["score"] > best[p["id"]]["score"]:
            best[p["id"]] = p

    unique = [p for p in best.values()
              if p["score"] > MIN_SCORE and p["id"] not in seen_ids]

    prob = sorted(
        (p for p in unique if is_probability(p)),
        key=lambda p: p["score"], reverse=True,
    )
    other = sorted(
        (p for p in unique if not is_probability(p)),
        key=lambda p: p["score"], reverse=True,
    )
    return prob[:CANDIDATE_PROB], other[:CANDIDATE_OTHER]


def select_by_score(prob_pool, other_pool):
    """Fallback: pick top papers by score when Claude is unavailable."""
    chosen = prob_pool[:N_PROB] + other_pool[:N_OTHER]
    chosen.sort(key=lambda p: p["score"], reverse=True)
    return chosen


def select_classics():
    """Pick N_CLASSIC papers from CLASSIC_PAPERS, rotating by ISO week."""
    if not CLASSIC_PAPERS:
        return []
    week = dt.date.today().isocalendar()[1]
    rng = random.Random(week)
    ids = rng.sample(CLASSIC_PAPERS, min(N_CLASSIC, len(CLASSIC_PAPERS)))
    papers = []
    for arxiv_id in ids:
        print(f"  fetching classic {arxiv_id}…")
        p = _fetch_by_id(arxiv_id)
        if p:
            papers.append(p)
    return papers


# ──────────────────────────────────────────────────────────────────────────
# Claude selection + enrichment
# ──────────────────────────────────────────────────────────────────────────

def claude_select_and_enrich(prob_pool, other_pool, classic_papers, recent_titles):
    """
    Ask Claude to select the best papers from the candidate pools and write
    one-line notes for all selected + classic papers.
    Falls back to score-based selection if ANTHROPIC_API_KEY is not set.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return select_by_score(prob_pool, other_pool)

    all_new = prob_pool + other_pool
    if not all_new:
        return []

    n_prob = len(prob_pool)

    new_listing = "\n\n".join(
        f"[{i}] {p['title']}\n{p['summary'][:400]}"
        for i, p in enumerate(all_new)
    )

    classic_listing = ""
    if classic_papers:
        classic_listing = "\n\nCLASSIC PAPERS TO ANNOTATE:\n\n" + "\n\n".join(
            f"[C{i}] {p['title']}\n{p['summary'][:300]}"
            for i, p in enumerate(classic_papers)
        )

    recent_ctx = ""
    if recent_titles:
        recent_ctx = (
            "\n\nPAPERS THE USER RECEIVED IN THE LAST FEW WEEKS "
            "(avoid exact topic repeats):\n"
            + "\n".join(f"- {t}" for t in recent_titles[:20])
        )

    prompt = (
        "You are curating a weekly research digest for a final-year mathematics student.\n\n"
        "INTEREST PROFILE: probability theory (martingales, SDEs, Brownian motion, "
        "Stein's method, concentration inequalities, extreme value theory, large deviations), "
        "spectral graph theory (graph energy, Laplacian eigenvalues), "
        "diffusion/score-based generative models (score matching, flow matching), "
        "quantitative finance (pairs trading, statistical arbitrage, cointegration, "
        "regime-switching, market microstructure).\n"
        f"{recent_ctx}\n\n"
        f"CANDIDATE NEW PAPERS — indices 0–{n_prob - 1} are probability categories "
        f"(select exactly {N_PROB}), indices {n_prob}–{len(all_new) - 1} are adjacent "
        f"(select exactly {N_OTHER}). Choose based on fit with the interest profile, "
        f"not just recency:\n\n{new_listing}"
        f"{classic_listing}\n\n"
        "Return ONLY this JSON, no other text:\n"
        '{"prob": [<exactly ' + str(N_PROB) + ' indices from 0–' + str(n_prob - 1) + '>], '
        '"other": [<exactly ' + str(N_OTHER) + ' indices from ' + str(n_prob) + '–' + str(len(all_new) - 1) + '>], '
        '"notes_new": {"<index>": "<one sentence, max 20 words>", ...}, '
        '"notes_classic": ["<note for C0>", "<note for C1>", ...]}'
    )

    body = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read())
        text = "".join(
            b.get("text", "") for b in data.get("content", [])
            if b.get("type") == "text"
        )
        text = re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE)
        result = json.loads(text.strip())

        prob_indices = result.get("prob", [])[:N_PROB]
        other_indices = result.get("other", [])[:N_OTHER]
        notes_new = result.get("notes_new", {})
        notes_classic = result.get("notes_classic", [])

        chosen = []
        for idx in prob_indices:
            if isinstance(idx, int) and 0 <= idx < n_prob:
                p = all_new[idx]
                p["note"] = notes_new.get(str(idx), "")
                chosen.append(p)
        for idx in other_indices:
            if isinstance(idx, int) and n_prob <= idx < len(all_new):
                p = all_new[idx]
                p["note"] = notes_new.get(str(idx), "")
                chosen.append(p)

        for i, p in enumerate(classic_papers):
            if i < len(notes_classic):
                p["note"] = notes_classic[i]

        # pad with score-based picks if Claude returned too few
        if len(chosen) < N_PROB + N_OTHER:
            used = {p["id"] for p in chosen}
            for p in select_by_score(prob_pool, other_pool):
                if p["id"] not in used:
                    chosen.append(p)
                    used.add(p["id"])
                if len(chosen) >= N_PROB + N_OTHER:
                    break

        return chosen

    except Exception as e:                           # noqa: BLE001
        print(f"  ! Claude selection skipped: {e}", file=sys.stderr)
        return select_by_score(prob_pool, other_pool)


# ──────────────────────────────────────────────────────────────────────────
# Render + send
# ──────────────────────────────────────────────────────────────────────────

def _paper_text_block(i, p):
    tag = "PR" if is_probability(p) else p["queried_cat"]
    lines = [f"{i}. [{tag}] {p['title']}"]
    lines.append(f"   {', '.join(p['authors'][:4])}"
                 + (" et al." if len(p["authors"]) > 4 else ""))
    if p.get("note"):
        lines.append(f"   → {p['note']}")
    lines.append(f"   {p['abs_url']}")
    lines.append("")
    return lines


def render_text(new_papers, classic_papers, cutoff):
    lines = [f"Weekly arXiv digest — since {cutoff.date()}", ""]
    for i, p in enumerate(classic_papers, 1):
        lines.extend(_paper_text_block(i, p))
    if new_papers:
        lines.append("── Last week " + "─" * 47)
        lines.append("")
        for i, p in enumerate(new_papers, 1):
            lines.extend(_paper_text_block(i, p))
    return "\n".join(lines)


def _paper_html_row(i, p):
    tag = "math.PR" if is_probability(p) else p["queried_cat"]
    authors = html.escape(", ".join(p["authors"][:4]))
    if len(p["authors"]) > 4:
        authors += " et al."
    note = ""
    if p.get("note"):
        note = (f'<div style="color:#444;font-size:13px;margin-top:4px">'
                f'→ {html.escape(p["note"])}</div>')
    return f"""
        <div style="margin:0 0 22px 0;padding:0 0 18px 0;border-bottom:1px solid #eee">
          <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.05em">
            {i} · {html.escape(tag)}
          </div>
          <div style="font-size:16px;font-weight:600;margin:3px 0">
            <a href="{html.escape(p['abs_url'])}" style="color:#1a1a1a;text-decoration:none">
              {html.escape(p['title'])}</a>
          </div>
          <div style="font-size:13px;color:#666">{authors}</div>
          {note}
          <div style="font-size:12px;margin-top:6px">
            <a href="{html.escape(p['abs_url'])}" style="color:#3355cc">abstract</a>
            &nbsp;·&nbsp;
            <a href="{html.escape(p['pdf_url'])}" style="color:#3355cc">pdf</a>
          </div>
        </div>"""


def render_html(new_papers, classic_papers, cutoff):
    classic_rows = "".join(_paper_html_row(i, p) for i, p in enumerate(classic_papers, 1))

    new_section = ""
    if new_papers:
        new_rows = "".join(_paper_html_row(i, p) for i, p in enumerate(new_papers, 1))
        new_section = f"""
      <div style="font-size:15px;font-weight:700;margin:32px 0 16px;
                  padding-top:24px;border-top:2px solid #e0e0e0;color:#555">
        Last week
      </div>
      {new_rows}"""

    return f"""<!DOCTYPE html><html><body style="margin:0;background:#fafafa">
    <div style="max-width:680px;margin:0 auto;padding:28px 24px;
                font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
      <div style="font-size:20px;font-weight:700;margin-bottom:4px">
        Weekly arXiv digest</div>
      <div style="font-size:13px;color:#888;margin-bottom:24px">
        {len(classic_papers)} curated · {len(new_papers)} last week · since {cutoff.date()}</div>
      {classic_rows}
      {new_section}
      <div style="font-size:11px;color:#aaa;margin-top:18px">
        Generated by arxiv_digest.py · tune your profile in CONFIG</div>
    </div></body></html>"""


def send_email(subject, text_body, html_body):
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASS"]
    to_addrs = [a.strip() for a in os.environ.get("MAIL_TO", user).split(",") if a.strip()]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(to_addrs)
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(user, password)
        server.sendmail(user, to_addrs, msg.as_string())
    print(f"  ✓ emailed {len(html_body)} bytes to {', '.join(to_addrs)}")


# ──────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Weekly arXiv digest.")
    ap.add_argument("--days", type=int, default=7,
                    help="look-back window in days (default 7)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print to stdout instead of emailing")
    args = ap.parse_args()

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.days)
    print(f"Fetching since {cutoff.date()} across {len(CATEGORIES)} categories…")

    all_papers = []
    for cat in CATEGORIES:
        got = fetch_category(cat, cutoff)
        print(f"  {cat}: {len(got)} in window")
        all_papers.extend(got)

    seen_ids, seen_titles = load_seen()
    print(f"Loaded {len(seen_ids)} seen IDs from cache.")

    prob_pool, other_pool = build_candidate_pool(all_papers, seen_ids)
    print(f"Candidate pool: {len(prob_pool)} prob, {len(other_pool)} other.")

    print(f"Fetching {N_CLASSIC} classic papers…")
    chosen_classics = select_classics()
    print(f"Fetched {len(chosen_classics)} classics.")

    chosen_new = claude_select_and_enrich(prob_pool, other_pool, chosen_classics, seen_titles)
    print(f"Selected {len(chosen_new)} new papers.")

    if not args.dry_run:
        save_seen(chosen_new + chosen_classics)
        print("Updated seen-papers cache.")

    subject = (f"arXiv digest — {len(chosen_classics)} curated + {len(chosen_new)} new"
               f" ({dt.date.today():%b %d})")
    text_body = render_text(chosen_new, chosen_classics, cutoff)
    html_body = render_html(chosen_new, chosen_classics, cutoff)

    if args.dry_run or not os.environ.get("SMTP_USER"):
        print("\n" + "=" * 60 + "\n")
        print(text_body)
        if not args.dry_run:
            print("(no SMTP_USER set — printed instead of emailing)",
                  file=sys.stderr)
    else:
        send_email(subject, text_body, html_body)


if __name__ == "__main__":
    main()
