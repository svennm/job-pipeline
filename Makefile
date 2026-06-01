.DEFAULT_GOAL := help

# Bake in Homebrew lib path so weasyprint finds pango/cairo on Apple Silicon
export DYLD_FALLBACK_LIBRARY_PATH := /opt/homebrew/lib:$(DYLD_FALLBACK_LIBRARY_PATH)

PY := .venv/bin/python

help:
	@echo "Targets:"
	@echo "  install         - set up venv + install deps + playwright browsers"
	@echo "  probe-slugs     - probe seed companies for Greenhouse/Lever/Ashby slugs"
	@echo "  fetch           - pull jobs from all configured ATS-direct sources"
	@echo "  scrape          - fallback JobSpy scrape (low signal vs ATS-direct)"
	@echo "  filter-fx       - filter latest raw -> data/fx-filtered.parquet"
	@echo "  score           - LLM-score the latest filtered postings"
	@echo "  draft           - draft tailored resume+cover for top-N scored"
	@echo "  render          - render queue/*/resume.md + cover_letter.md -> PDFs"
	@echo "  submit-plan     - show submission plan (dry-run, no browser)"
	@echo "  submit-confirm  - open headed browsers, prefill forms, user clicks submit"
	@echo "  tracker-append  - sync queue/ -> tracker/live.csv as 'to_apply' rows"
	@echo "  full            - fetch + filter + score + draft + render + tracker-append"
	@echo "  clean-cache     - rm data/cache (forces re-score)"

install:
	python3 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.txt
	$(PY) -m playwright install chromium
	@echo "Done. Copy config/config.example.yaml -> config/config.yaml + edit resume/private/resume.yaml"

probe-slugs:
	$(PY) scripts/probe_company_slugs.py --priority A
	$(PY) scripts/probe_company_slugs.py --priority B

fetch:
	$(PY) -m src.sources.ats_direct

scrape:
	$(PY) -m src.scrape

filter-fx:
	$(PY) scripts/filter_fx.py

score:
	$(PY) -m src.heuristic_score

score-llm:
	$(PY) -m src.score --prefilter data/fx-filtered.parquet

draft:
	$(PY) -m src.draft

render:
	$(PY) -m src.render

submit-plan:
	$(PY) -m src.submit

submit-confirm:
	$(PY) -m src.submit --confirm

tracker-append:
	$(PY) -m src.tracker append

full: fetch filter-fx score draft render tracker-append
	@echo "Done. Review queue/ then 'make submit-confirm' to open browsers."

clean-cache:
	rm -rf data/cache
	@echo "Cache cleared."

.PHONY: help install probe-slugs fetch scrape filter-fx score draft render submit-plan submit-confirm tracker-append full clean-cache
