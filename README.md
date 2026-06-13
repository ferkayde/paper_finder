# Weekly arXiv digest

A small, dependency-free Python tool that emails you a weekly shortlist of new
arXiv papers, weighted toward probability theory with room for spectral graph
theory, quant finance, and diffusion models. Runs on a schedule via GitHub
Actions, so your laptop doesn't need to be on.

## What it does

1. Pulls the past week's papers from your chosen arXiv categories (`math.PR`
   first, plus the adjacent ones).
2. Scores each paper against a keyword + author **interest profile** you control.
3. Selects a probability-heavy shortlist (default: 8 probability + 4 other).
4. Emails an HTML digest with abstract/pdf links.
5. *(Optional)* asks the Claude API for a one-line "why this is relevant"
   per paper — only if `ANTHROPIC_API_KEY` is set.

No third-party packages. Standard library only.

## Quick start (local test)

```bash
python arxiv_digest.py --dry-run
```

This prints the digest to your terminal without sending email. Use it to tune
the keyword weights in the `CONFIG` block at the top of `arxiv_digest.py`.

To test the email path locally, set the three env vars first (see below) and
run without `--dry-run`.

## Email setup (Gmail)

You need a Gmail **app password**, not your normal password:

1. Enable 2-Step Verification on your Google account.
2. Go to Google Account → Security → App passwords, create one for "Mail".
3. Use that 16-character value as `SMTP_PASS`.

Env vars:

| Variable | Meaning |
|---|---|
| `SMTP_USER` | your full Gmail address |
| `SMTP_PASS` | the app password |
| `MAIL_TO` | recipient (can be the same address) |
| `ANTHROPIC_API_KEY` | optional — enables one-line relevance notes |

(If you'd rather not use Gmail, swap the host in `send_email()` — the SMTP
block is four lines.)

## Scheduling on GitHub Actions

1. Create a new GitHub repo and push these files (keep the
   `.github/workflows/weekly-digest.yml` path).
2. In the repo: Settings → Secrets and variables → Actions → New repository
   secret. Add `SMTP_USER`, `SMTP_PASS`, `MAIL_TO`, and optionally
   `ANTHROPIC_API_KEY`.
3. The workflow runs every Monday at 06:00 UTC (09:00 Istanbul). You can also
   trigger it manually from the Actions tab ("Run workflow") to test.

That's it — it's free for this kind of light scheduled job.

## Tuning your profile

Everything lives in the `CONFIG` section at the top of `arxiv_digest.py`:

- **`CATEGORIES`** — add/remove arXiv categories and set each one's base weight.
- **`KEYWORDS`** — the heart of it. Add terms you care about with a weight;
  title matches count 3×, abstract matches 1×.
- **`AUTHORS`** — surnames to always surface (matched case-insensitively).
- **`N_PROB` / `N_OTHER`** — the size and probability/other split of the digest.
- **`PROB_CATEGORIES`** — which categories count as "probability proper" for the
  quota.

### A note on the `song` author filter
`AUTHORS` matches surnames, so `"song"` will surface Yang Song (score-based
models) but also unrelated authors named Song. If that's noisy, remove it and
rely on the diffusion keywords instead.

## Ideas for later

- Add a "seen papers" cache (a small JSON file committed back by the Action) so
  you never get the same paper twice across weeks.
- Replace keyword scoring entirely with an embedding similarity against your
  `papers_we_have_covered.md` profile.
- Add a second digest for a different topic on a different day.
