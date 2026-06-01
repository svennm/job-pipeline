# job-pipeline

AI-orchestrated job-search pipeline using Claude Code + MCP.

Discovers postings via JobSpy, scores fit against a YAML-source resume, drafts tailored resumes and cover letters into a review queue, and tracks applications in a CSV that mirrors a Google Sheets tracker.

**No auto-submit.** All sends are reviewed manually — automation only handles discovery and drafting.

## Why

Quant/crypto-MM/EU-prop/Francophone-Africa targets don't post on bulk-apply boards. Cuts per-application drafting from ~30min to ~5min while keeping submissions human-reviewed (no ToS violations, no LinkedIn ban risk).

## Architecture

```
src/sources/ats_direct.py  Pull jobs straight from Greenhouse/Lever/Ashby APIs
                           for known seed-company slugs. Highest signal.
src/scrape.py              JobSpy fallback: LinkedIn/Indeed/Glassdoor/Google
                           keyword search (lower signal, no auto-submit URLs).
scripts/filter_fx.py       Pre-filter raw -> FX-relevant titles only.
src/score.py               Haiku scoring vs resume.yaml + per-category targets.
src/draft.py               Sonnet drafts tailored resume + cover per top-N hit.
src/render.py              Markdown -> PDF (WeasyPrint, Helvetica, 1-page).
src/ats.py                 Detect ATS from URL (Greenhouse/Lever/Ashby/Workday/etc)
src/submit.py              Playwright headed prefill — opens browser, fills form,
                           USER clicks submit. No unattended submits.
src/tracker.py             Append-from-queue + status mark CSV ops.

config/companies.yaml       Auto-probed company -> ATS slug map (committed).
config/companies.manual.yaml Hand-verified slugs for companies the probe missed.
tracker/seed.csv            108-row target company list w/ priority/category.
queue/                      Per-application folders (gitignored).
resume/resume.template.yaml Public template; real resume in resume/private/.
data/                       Raw + cached + scored parquet (gitignored).
```

## Target categories

`crypto_mm` · `broker_tech` · `eu_prop` · `africa_treasury` · `fintech_generalist`

## Quickstart

```bash
# One-time setup
make install
cp resume/resume.template.yaml resume/private/resume.yaml
cp config/config.example.yaml config/config.yaml
# edit resume/private/resume.yaml — real PII goes here, gitignored

# Map your seed companies to their ATS slugs (one-time)
make probe-slugs

# Pipeline (run anytime you want new applications)
make full           # fetch + filter + score + draft + render + tracker-append

# Review queue/ folders. Then:
make submit-plan    # see what would be submitted
make submit-confirm # opens headed browsers, fills forms, YOU click submit
```

## LLM backend

Default backend is **Claude Code CLI** (`claude -p`) — uses your Claude Code
subscription, no separate API key needed. Score uses Haiku (fast), drafts use
Sonnet (quality). Set `llm.backend: anthropic_sdk` in `config/config.yaml` to
use the Anthropic SDK directly if you have an API key.

## Tracker

Schema mirrors `tracker/schema.md` (16 columns). Seed list of 108 target companies in `tracker/seed.csv`. Live state lives in a Google Sheet (gitignored locally).

## Status

Scaffolding only. Not yet running end-to-end. See open issues.

## Stack

- [JobSpy](https://github.com/speedyapply/JobSpy) — read-only multi-board scraper
- Anthropic Claude API for scoring + drafting
- Pandas / PyArrow for normalization

## Optional: React-PDF rendering via claude-code-job-tailor

Our `draft.py` outputs Markdown. For React-PDF rendering with company-specific tailoring, clone:

```bash
git clone https://github.com/javiera-vasquez/claude-code-job-tailor.git tools/claude-code-job-tailor
cd tools/claude-code-job-tailor && bun install
bun run save-to-pdf -C <company-name>
```

Heavier (Bun + React), but produces nicer PDFs than markdown+weasyprint. `tools/` is gitignored.
