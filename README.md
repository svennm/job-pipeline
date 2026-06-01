# job-pipeline

AI-orchestrated job-search pipeline using Claude Code + MCP.

Discovers postings via JobSpy, scores fit against a YAML-source resume, drafts tailored resumes and cover letters into a review queue, and tracks applications in a CSV that mirrors a Google Sheets tracker.

**No auto-submit.** All sends are reviewed manually — automation only handles discovery and drafting.

## Why

Quant/crypto-MM/EU-prop/Francophone-Africa targets don't post on bulk-apply boards. Cuts per-application drafting from ~30min to ~5min while keeping submissions human-reviewed (no ToS violations, no LinkedIn ban risk).

## Architecture

```
src/scrape/  JobSpy → filter (remote, EU/UK/SG/Dubai, exclude US-only) → normalized parquet
src/score/   Claude API: rank JD vs resume.yaml against 5 target categories
src/draft/   Top-N hits → tailored resume.pdf + cover_letter.md → queue/{company}-{date}/
queue/       Ready-to-review drafts (gitignored)
tracker/     application_tracker.csv mirrors the Google Sheet (gitignored when live)
resume/      resume.template.yaml (public structure), resume/private/ (real PII, gitignored)
```

## Target categories

`crypto_mm` · `broker_tech` · `eu_prop` · `africa_treasury` · `fintech_generalist`

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1. Copy template and fill in your data
cp resume/resume.template.yaml resume/private/resume.yaml
# (edit resume/private/resume.yaml with your real info)

# 2. Configure
cp config/config.example.yaml config/config.yaml
# (set ANTHROPIC_API_KEY in environment)

# 3. Run pipeline
python -m src.scrape   # scrape postings → data/raw/
python -m src.score    # score against resume → data/scored.parquet
python -m src.draft    # draft top-N → queue/

# 4. Review queue/ manually, submit, then update tracker
```

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
