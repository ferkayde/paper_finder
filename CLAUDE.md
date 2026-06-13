# arXiv Weekly Digest — Project Context

Handoff document for Claude Code. This describes the current, working state of
the project so you can pick it up and extend it. Drop this file in the repo root
(Claude Code reads `CLAUDE.md` automatically).

## What this project is

A small tool that emails a weekly shortlist of new arXiv papers, weighted toward
probability theory with room for adjacent interests (spectral graph theory,
quantitative finance, diffusion / score-based generative models). It runs on a
schedule via GitHub Actions, so it fires without a local machine being on.

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
arxiv-digest/
├── arxiv_digest.py                  # the whole tool (single file)
├── README.md                        # human setup instructions
├── CLAUDE.md                        # this file
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
3. `score_paper(paper)` — baseline `CATEGORY_BIAS × category_weight`, plus
   keyword hits (title 3×, abstract 1×, per `KEYWORDS` weights), plus an
   `AUTHOR_BONUS` if any tracked surname appears.
4. `select(papers)` — dedups by arXiv id (keeps highest score), drops anything
   at or below `MIN_SCORE`, then fills a quota: top `N_PROB` (8) from
   `PROB_CATEGORIES`, top `N_OTHER` (4) from everything else.
5. `enrich_with_claude(papers)` — **optional**; only runs if `ANTHROPIC_API_KEY`
   is set. One batched call to the Messages API returns a JSON array of one-line
   relevance notes, attached as `paper["note"]`. Fails open (skips silently) on
   any error.
6. `render_text` / `render_html` — build the digest body.
7. `send_email` — Gmail over `SMTP_SSL` on port 465.

`main()` flags: `--days N` (look-back window, default 7) and `--dry-run` (print
to stdout instead of emailing). It also prints instead of emailing if
`SMTP_USER` is unset.

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
- Selection: `N_PROB`, `N_OTHER`, `MIN_SCORE`.
- Fetch: `PAGE_SIZE`, `MAX_PAGES`, `REQUEST_PAUSE`, `USER_AGENT`.
- `ANTHROPIC_MODEL` — model string for the optional enrichment.

## Current state

Working and tested offline (parse → score → select → render verified on a
synthetic feed). Not yet validated against a live arXiv pull or a real email
send — those happen in the deployed environment.

### Deployment status
- Scheduled for Mondays 06:00 UTC (09:00 Istanbul) via `weekly-digest.yml`;
  also has `workflow_dispatch` for manual runs.
- Requires repo secrets: `SMTP_USER`, `SMTP_PASS` (Gmail app password),
  `MAIL_TO`, and optional `ANTHROPIC_API_KEY`.

### Known limitations / behavior notes
- **No cross-week dedup cache.** The only thing preventing repeats is the 7-day
  window. If the Action runs late or twice, a paper can appear in two digests.
  Closing this is the top-priority next task.
- Filters on `published` (original submission date), not `updated`. Papers
  revised this week but first posted earlier will not appear. This is
  intentional for now.
- Keyword matching is plain substring matching, so it can over- or under-match
  (e.g. short tokens). The `"song"` author entry catches Yang Song but also any
  unrelated author named Song.

## Roadmap (in priority order)

1. **Seen-papers cache.** Persist shortlisted arXiv ids in a small JSON file
   that the Action commits back to the repo, and exclude already-seen ids in
   `select()`. This fully removes cross-week repeats. Keep it stdlib (`json`),
   handle the first-run empty case, and bound the file size (e.g. keep ~12
   weeks). The Action will need `contents: write` permission and a commit step.
2. **Profile-based relevance instead of keyword counting.** Optionally score by
   similarity to the owner's interest profile (`papers_we_have_covered.md`)
   rather than hand-weighted keywords. Must remain optional and must not add a
   hard third-party dependency.
3. **Multiple digests.** Allow a second category set / schedule (e.g. a separate
   quant-finance digest on a different day).
4. **Per-category fairness.** Optionally cap how many papers come from any single
   category so one busy category can't dominate the "other" slots.

## Conventions

- Single-file tool; keep `arxiv_digest.py` self-contained unless a feature
  genuinely warrants a second module.
- Prefer clear, small functions over cleverness.
- When changing scoring or selection, verify with `--dry-run` and a synthetic
  feed before relying on a live run.
