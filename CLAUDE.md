# arXiv Weekly Digest — Project Context

Handoff document for Claude Code. This describes the current, working state of
the project so you can pick it up and extend it. Drop this file in the repo root
(Claude Code reads `CLAUDE.md` automatically).

## What this project is

A small tool that emails a weekly shortlist of new arXiv papers, weighted toward
probability theory with room for adjacent interests (spectral graph theory,
quantitative finance, diffusion / score-based generative models) — plus a
rotating curated "classic paper" pick each week. It runs on a schedule via
GitHub Actions, so it fires without a local machine being on.

The owner is a mathematics student; the interest profile is encoded in the
script's `CONFIG` block and should stay probability-heavy.

## Hard constraints (do not break these)

- **Standard library only.** No third-party packages, no `pip install`, no
  `requirements.txt`. Network, XML parsing, email, and the optional Claude call
  are all done with stdlib (`urllib`, `xml.etree.ElementTree`, `smtplib`,
  `email`, `json`). Keep it that way unless explicitly asked.
- **Secrets come from environment variables only.** Never hardcode an API key,
  email, or password in the source, and never commit one. The code reads
  `SMTP_USER`, `SMTP_PASS`, `MAIL_TO`, and optional `ANTHROPIC_API_KEY` from
  `os.environ`.
- **Be polite to arXiv.** Keep the descriptive `User-Agent` and the
  `REQUEST_PAUSE` (3s) between requests. Don't parallelize the fetch.

## File layout

```
paper_finder/
├── arxiv_digest.py                  # the whole tool (single file)
├── seen_papers.json                 # cross-week dedup cache, committed by the Action
├── README.md                        # human setup instructions
├── CLAUDE.md                        # this file
├── papers_we_have_covered.md        # reading-history notes (books/topics discussed);
│                                     # not read by the script — background for tuning KEYWORDS
├── classics_from_arxiv.md           # research notes behind the arXiv-ID entries in
│                                     # CLASSIC_PAPERS; not read by the script
├── classic_math_papers_non_arxiv.csv # research notes behind the pre-arXiv dict entries in
│                                     # CLASSIC_PAPERS; not read by the script
└── .github/
    └── workflows/
        └── weekly-digest.yml        # scheduled GitHub Action
```

## How it works (pipeline)

All logic lives in `arxiv_digest.py`, run top to bottom by `main()`:

1. `fetch_category(cat, cutoff)` — queries the arXiv API
   (`http://export.arxiv.org/api/query`) per category, newest-first, paging in
   blocks of `PAGE_SIZE` (100) up to `MAX_PAGES` (5) until it passes the date
   cutoff. Returns only papers with `published >= cutoff`.
2. `parse_atom(raw, queried_cat)` — parses the Atom XML with ElementTree into
   paper dicts: `id`, `title`, `summary`, `authors`, `published` (UTC
   datetime), `categories`, `abs_url`, `pdf_url`.
3. `load_seen()` — reads `seen_papers.json`: returns arXiv ids seen within the
   last `SEEN_MAX_WEEKS` (12) as an exclusion set, plus titles from the last
   `SEEN_RECENT_WEEKS` (4) as recent-topic context for Claude.
4. `score_paper(paper)` — baseline `CATEGORY_BIAS × category_weight`, plus
   keyword hits (title 3×, abstract 1×, per `KEYWORDS` weights), plus an
   `AUTHOR_BONUS` if any tracked surname appears.
5. `build_candidate_pool(papers, seen_ids)` — dedups by arXiv id (keeps
   highest score), drops anything at or below `MIN_SCORE` or already in
   `seen_ids`, splits into probability vs. other, and caps each pool at
   `CANDIDATE_PROB` (20) / `CANDIDATE_OTHER` (10) candidates.
6. `select_classics()` — samples `N_CLASSIC` (1) entries from `CLASSIC_PAPERS`,
   seeded by ISO week number (so the pick is stable within a week, rotates
   next week). Entries are either an arXiv ID (fetched live via
   `_fetch_by_id`) or a hand-filled dict for pre-arXiv papers
   (`_make_classic_from_dict`).
7. `claude_select_and_enrich(prob_pool, other_pool, classic_papers, recent_titles)`
   — if `ANTHROPIC_API_KEY` is set, one batched call to the Messages API asks
   Claude to *pick* exactly `N_PROB` + `N_OTHER` papers from the candidate
   pools (not just rank them) — given the interest profile and recent-titles
   context to avoid topic repeats — and to write a one-line relevance note for
   every selected + classic paper. Falls back to `select_by_score()` (pure
   score-ranking, no notes) if the key is unset or the call/parse fails.
8. `save_seen(papers)` — appends the selected new + classic paper ids/titles
   to `seen_papers.json`, then prunes entries older than `SEEN_MAX_WEEKS`.
   Skipped on `--dry-run`.
9. `render_text` / `render_html` — build the digest body (classics section
   first, then "Last week" new papers).
10. `send_email` — Gmail over `SMTP_SSL` on port 465.

`main()` flags: `--days N` (look-back window, default 7) and `--dry-run` (print
to stdout instead of emailing, and skip updating the seen-papers cache). It
also prints instead of emailing if `SMTP_USER` is unset.

## Configuration

Everything tunable is in the `CONFIG` section at the top of `arxiv_digest.py`:

- `CATEGORIES` — arXiv category → base weight in [0,1]. `math.PR` is 1.0; the
  rest are lower. The selection quota's "probability" set is `PROB_CATEGORIES`
  (`math.PR`, `math.ST`).
- `KEYWORDS` — substring-matched (case-insensitive) terms → weight. This is the
  main tuning surface.
- `AUTHORS` — surnames to always surface.
- Scoring knobs: `CATEGORY_BIAS`, `TITLE_WEIGHT`, `ABSTRACT_WEIGHT`,
  `AUTHOR_BONUS`.
- Selection: `N_PROB` (3), `N_OTHER` (2), `N_CLASSIC` (1), `MIN_SCORE`.
- Candidate pool sizes fed to Claude: `CANDIDATE_PROB` (20), `CANDIDATE_OTHER` (10).
- `CLASSIC_PAPERS` — the curated rotation pool. Mix of plain arXiv-id strings
  and dicts (`title`, `authors`, `year`, `summary`, `abs_url`, `pdf_url`,
  `categories`) for papers that predate arXiv or were never posted there.
  Grow this list freely; `select_classics()` handles both forms.
- Seen-cache: `SEEN_CACHE` (`seen_papers.json`), `SEEN_MAX_WEEKS` (12),
  `SEEN_RECENT_WEEKS` (4).
- Fetch: `PAGE_SIZE`, `MAX_PAGES`, `REQUEST_PAUSE`, `USER_AGENT`.
- `ANTHROPIC_MODEL` — model string for Claude-based selection + enrichment.

## Current state

Live and running weekly in production (not just offline-tested). The Action
has been firing on schedule and committing the seen-papers cache back to the
repo (see recent commit history: `chore: update seen papers cache`).

### Deployment status
- Scheduled for Tuesdays 06:00 UTC (09:00 Istanbul) via `weekly-digest.yml`;
  also has `workflow_dispatch` for manual runs.
- Requires repo secrets: `SMTP_USER`, `SMTP_PASS` (Gmail app password),
  `MAIL_TO`, and optional `ANTHROPIC_API_KEY`.
- Workflow has `contents: write` and commits `seen_papers.json` back to the
  repo after each run (`[skip ci]` to avoid retriggering).

### Known limitations / behavior notes
- Filters on `published` (original submission date), not `updated`. Papers
  revised this week but first posted earlier will not appear. This is
  intentional for now.
- Keyword matching is plain substring matching, so it can over- or under-match
  (e.g. short tokens). The `"song"` author entry catches Yang Song but also any
  unrelated author named Song.
- Claude selection is a single best-effort call with no retry; any failure
  (network, bad JSON, quota) silently falls back to score-based selection with
  no notes. Check Action logs for `! Claude selection skipped: …` if notes
  stop appearing.
- `select_classics()` reuses the same `random.Random(week)` seed across runs
  within a week, so re-running the Action manually mid-week reproduces the
  same classic pick (not a new random one) unless `CLASSIC_PAPERS` changed.

## Roadmap (in priority order)

1. **Profile-based relevance instead of keyword counting.** Optionally score by
   similarity to the owner's interest profile (`papers_we_have_covered.md`)
   rather than hand-weighted keywords. Must remain optional and must not add a
   hard third-party dependency.
2. **Multiple digests.** Allow a second category set / schedule (e.g. a separate
   quant-finance digest on a different day).
3. **Per-category fairness.** Optionally cap how many papers come from any single
   category so one busy category can't dominate the "other" slots.

## Conventions

- Single-file tool; keep `arxiv_digest.py` self-contained unless a feature
  genuinely warrants a second module.
- Prefer clear, small functions over cleverness.
- When changing scoring or selection, verify with `--dry-run` and a synthetic
  feed before relying on a live run.
- When adding to `CLASSIC_PAPERS`, verify arXiv IDs actually resolve (or, for
  pre-arXiv entries, that `abs_url`/`pdf_url` are real working links) before
  committing — a bad ID fails silently via `_fetch_by_id`'s try/except and
  just quietly yields one fewer classic that week.
