#!/usr/bin/env python3
"""
arxiv_digest.py — weekly arXiv digest, weighted toward probability theory.

Pure standard library. No third-party dependencies.

Pulls recent papers from chosen arXiv categories, scores each one against a
keyword/author interest profile, selects a probability-heavy shortlist, and
emails an HTML digest. Optionally asks the Claude API for a one-line "why this
is relevant to you" per paper (only if ANTHROPIC_API_KEY is set).

Usage:
    python arxiv_digest.py --dry-run            # print to stdout, no email
    python arxiv_digest.py --days 7             # look back 7 days (default)
    python arxiv_digest.py                      # fetch + email

Environment variables (for emailing):
    SMTP_USER   full gmail address, e.g. you@gmail.com
    SMTP_PASS   gmail *app password* (not your normal password)
    MAIL_TO     recipient address (can be the same as SMTP_USER)
    ANTHROPIC_API_KEY   (optional) enables one-line relevance notes
"""

import argparse
import datetime as dt
import html
import json
import os
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

# arXiv categories with a base weight in [0, 1]. Higher = more central to you.
# Probability sits at the top; the rest are the "interesting adjacent things".
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

# Categories considered "probability proper" for the selection quota.
PROB_CATEGORIES = {"math.PR", "math.ST"}

# Keywords → weight. Matched case-insensitively in title (3x) and abstract (1x).
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
    "spectral graph": 3.0, "adjacency spectrum": 2.5, "brouwer": 3.0,
    "graph spectra": 2.5,
    # diffusion / generative
    "diffusion model": 3.0, "score-based": 3.0, "score matching": 3.0,
    "flow matching": 2.5, "generative model": 1.5, "stochastic interpolant": 3.0,
    # quant finance
    "pairs trading": 4.0, "statistical arbitrage": 3.5, "cointegration": 3.0,
    "mean reversion": 2.5, "regime-switching": 3.0, "regime switching": 3.0,
    "market microstructure": 2.0, "illiquidity": 2.5, "limit order book": 2.0,
}

# Authors to always surface (surname match, case-insensitive). Big bonus.
AUTHORS = {
    "islak", "işlak",
    "haggstrom", "häggström",
    "gutman", "embrechts", "goldstein",
    "song",  # Yang Song (score-based models) — note: common surname, see note below
}

# Scoring knobs.
CATEGORY_BIAS = 2.0      # multiplies the category weight into the baseline score
TITLE_WEIGHT = 3.0       # keyword hit in the title is worth this much × kw weight
ABSTRACT_WEIGHT = 1.0    # keyword hit in the abstract
AUTHOR_BONUS = 8.0       # any tracked author present

# Selection quotas (the "mostly probability + some others" mix).
N_PROB = 8               # papers drawn from PROB_CATEGORIES
N_OTHER = 4              # papers drawn from everything else
MIN_SCORE = 0.0          # drop anything at or below this score

# Fetch knobs.
PAGE_SIZE = 100
MAX_PAGES = 5            # safety cap per category (covers a busy week of math.PR)
REQUEST_PAUSE = 3.0      # seconds between arXiv requests (be polite)
USER_AGENT = "weekly-arxiv-digest/1.0 (personal research digest)"

ANTHROPIC_MODEL = "claude-sonnet-4-6"  # used only if ANTHROPIC_API_KEY is set

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
        if oldest < cutoff:                          # we've gone back far enough
            break

    # keep only papers inside the window
    return [p for p in papers if p["published"] >= cutoff]


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
# Score + select
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


def select(papers):
    # dedup by arxiv id, keeping the highest-scoring instance
    best = {}
    for p in papers:
        p["score"] = score_paper(p)
        if p["id"] not in best or p["score"] > best[p["id"]]["score"]:
            best[p["id"]] = p
    unique = [p for p in best.values() if p["score"] > MIN_SCORE]

    prob = sorted(
        (p for p in unique if is_probability(p)),
        key=lambda p: p["score"], reverse=True,
    )
    other = sorted(
        (p for p in unique if not is_probability(p)),
        key=lambda p: p["score"], reverse=True,
    )
    chosen = prob[:N_PROB] + other[:N_OTHER]
    chosen.sort(key=lambda p: p["score"], reverse=True)
    return chosen


# ──────────────────────────────────────────────────────────────────────────
# Optional Claude enrichment
# ──────────────────────────────────────────────────────────────────────────

def enrich_with_claude(papers):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not papers:
        return papers

    listing = "\n\n".join(
        f"[{i}] {p['title']}\n{p['summary'][:600]}"
        for i, p in enumerate(papers)
    )
    prompt = (
        "You are curating a weekly research digest for a final-year mathematics "
        "student focused on probability theory, with side interests in spectral "
        "graph theory, quantitative finance (pairs trading, stat-arb), and "
        "diffusion/score-based generative models.\n\n"
        "For each paper below, write ONE short sentence (max 25 words) on why it "
        "might interest them, or why they can skip it. Return ONLY a JSON array "
        "of strings, in the same order, no other text.\n\n"
        f"{listing}"
    )
    body = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": 1500,
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
        notes = json.loads(text.strip())
        for p, note in zip(papers, notes):
            p["note"] = note
    except Exception as e:                           # noqa: BLE001
        print(f"  ! Claude enrichment skipped: {e}", file=sys.stderr)
    return papers


# ──────────────────────────────────────────────────────────────────────────
# Render + send
# ──────────────────────────────────────────────────────────────────────────

def render_text(papers, cutoff):
    lines = [f"Weekly arXiv digest — since {cutoff.date()}", ""]
    for i, p in enumerate(papers, 1):
        tag = "PR" if is_probability(p) else p["queried_cat"]
        lines.append(f"{i}. [{tag}] {p['title']}")
        lines.append(f"   {', '.join(p['authors'][:4])}"
                     + (" et al." if len(p["authors"]) > 4 else ""))
        if p.get("note"):
            lines.append(f"   → {p['note']}")
        lines.append(f"   {p['abs_url']}")
        lines.append("")
    return "\n".join(lines)


def render_html(papers, cutoff):
    rows = []
    for i, p in enumerate(papers, 1):
        tag = "math.PR" if is_probability(p) else p["queried_cat"]
        authors = html.escape(", ".join(p["authors"][:4]))
        if len(p["authors"]) > 4:
            authors += " et al."
        note = ""
        if p.get("note"):
            note = (f'<div style="color:#444;font-size:13px;margin-top:4px">'
                    f'→ {html.escape(p["note"])}</div>')
        rows.append(f"""
        <div style="margin:0 0 22px 0;padding:0 0 18px 0;border-bottom:1px solid #eee">
          <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.05em">
            {i} · {html.escape(tag)} · score {p['score']:.1f}
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
        </div>""")

    return f"""<!DOCTYPE html><html><body style="margin:0;background:#fafafa">
    <div style="max-width:680px;margin:0 auto;padding:28px 24px;
                font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
      <div style="font-size:20px;font-weight:700;margin-bottom:4px">
        Weekly arXiv digest</div>
      <div style="font-size:13px;color:#888;margin-bottom:24px">
        {len(papers)} papers · since {cutoff.date()}</div>
      {''.join(rows)}
      <div style="font-size:11px;color:#aaa;margin-top:18px">
        Generated by arxiv_digest.py · tune your profile in CONFIG</div>
    </div></body></html>"""


def send_email(subject, text_body, html_body):
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASS"]
    to_addr = os.environ.get("MAIL_TO", user)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(user, password)
        server.sendmail(user, [to_addr], msg.as_string())
    print(f"  ✓ emailed {len(html_body)} bytes to {to_addr}")


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

    chosen = select(all_papers)
    print(f"Selected {len(chosen)} papers "
          f"({sum(is_probability(p) for p in chosen)} probability).")

    chosen = enrich_with_claude(chosen)

    subject = f"arXiv digest — {len(chosen)} papers ({dt.date.today():%b %d})"
    text_body = render_text(chosen, cutoff)
    html_body = render_html(chosen, cutoff)

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
