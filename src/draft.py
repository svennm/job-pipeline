"""Draft tailored resume.md + cover_letter.md per top-N scored posting.

Input:  data/scored-{YYYYMMDD}.parquet (latest)
Output: queue/{YYYYMMDD}-{score}-{company-slug}-{url_hash}/
          ├── posting.md         (JD + metadata)
          ├── score.json         (fit analysis from score step)
          ├── resume.md          (tailored)
          └── cover_letter.md    (tailored)

The url_hash suffix (first 6 chars of sha256(job_url)) prevents folder collisions
when one company has multiple postings with the same fit_score.

Markdown only — render.py converts to PDF in a separate step.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

import click
import pandas as pd
import yaml
from rich.console import Console
from rich.progress import track

from . import config, llm

console = Console()

RESUME_PROMPT = """You are tailoring a resume for one specific job posting.

CANDIDATE RESUME (YAML source — full inventory of bullets, projects, skills):
{resume_yaml}

JOB POSTING:
Title: {title}
Company: {company}
Location: {location}
Category: {category}
Description:
{description}

SCORE ANALYSIS:
{score_json}

TASK:
Produce a tailored resume in Markdown that:
1. Uses the candidate's REAL bullets — never fabricate experience, projects, or numbers
2. Picks the 3-5 most relevant bullets per role for THIS posting (the YAML may have more)
3. Reorders sections so the strongest fit appears first
4. Uses the headline from resume.headline_by_category[{category}] if set, else resume.headline
5. Lists 6-10 most relevant skills inline (trim irrelevant ones)
6. Keeps to ~1 page when rendered (target ~600 words total)
7. Output FORMAT: clean Markdown, no fenced code blocks around it, no preamble

Begin output with the candidate's name as # H1."""

COVER_PROMPT = """You are drafting a cover letter for one specific job posting.

CANDIDATE RESUME (for tone, voice, and factual basis):
{resume_yaml}

JOB POSTING:
Title: {title}
Company: {company}
Location: {location}
Category: {category}
Description:
{description}

SCORE ANALYSIS:
{score_json}

TASK:
Write a cover letter that:
1. Opens with a SPECIFIC hook tied to this company / posting — never generic
2. Names 2-3 concrete things from the candidate's resume that map to top JD requirements
3. Uses the category pitch from resume.narrative.category_pitches[{category}] as raw material — adapt it, don't paste it
4. Mentions language fit if relevant (French for African / French companies)
5. Closes with a clear next step
6. Target {words} words. NO em-dashes. NO "I'm thrilled / I'm excited" openings.
7. Output FORMAT: clean Markdown — name and contact at top, then "Dear Hiring Team," then body, then sign-off. No preamble outside the letter."""


def _slug(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", s or "").strip("-").lower()
    return s[:40] or "unknown"


def _latest_scored() -> Path:
    files = sorted((config.ROOT / "data").glob("scored-*.parquet"))
    if not files:
        raise FileNotFoundError("No data/scored-*.parquet — run score first.")
    return files[-1]


def _format_jd(row: pd.Series, max_chars: int = 4000) -> str:
    return (row.get("description") or "")[:max_chars]


def _build_kwargs(resume_yaml: str, row: pd.Series) -> dict:
    score_summary = {
        "fit_score": int(row.get("fit_score") or 0),
        "verdict": row.get("verdict"),
        "strengths": row.get("strengths"),
        "gaps": row.get("gaps"),
        "risks": row.get("risks"),
        "top_requirements": row.get("top_requirements"),
    }
    return dict(
        resume_yaml=resume_yaml,
        title=row.get("title", ""),
        company=row.get("company", ""),
        location=row.get("location", ""),
        category=row.get("category", "fintech_generalist"),
        description=_format_jd(row),
        score_json=json.dumps(score_summary, indent=2, default=str),
    )


def draft_one(resume_yaml: str, row: pd.Series, cover_words: int) -> tuple[str, str]:
    base = _build_kwargs(resume_yaml, row)
    resume_md = llm.call(RESUME_PROMPT.format(**base)).strip()
    cover_md = llm.call(COVER_PROMPT.format(words=cover_words, **base)).strip()
    return resume_md, cover_md


@click.command()
@click.option("--input", "input_path", default=None, help="Override scored parquet path.")
@click.option("--top", default=None, type=int, help="Override draft_top_n.")
@click.option("--dry-run", is_flag=True, help="Print plan, don't call API.")
def main(input_path: str | None, top: int | None, dry_run: bool) -> None:
    config.load_env()
    cfg = config.load_config()
    dcfg = cfg["draft"]
    scfg = cfg["score"]

    scored_path = Path(input_path) if input_path else _latest_scored()
    df = pd.read_parquet(scored_path)

    min_score = scfg.get("min_score", 60)
    df = df[df["fit_score"] >= min_score].copy()

    n = top or scfg.get("draft_top_n", 15)
    df = df.head(n)

    console.log(f"drafting top {len(df)} from {scored_path}")

    if dry_run:
        for _, r in df.iterrows():
            console.print(
                f"  {r.get('fit_score')} | {r.get('company')} | {r.get('title')} | {r.get('location')}"
            )
        return

    resume = config.load_resume()
    resume_yaml = yaml.safe_dump(resume, sort_keys=False)
    cover_words = dcfg.get("cover_letter_words", 250)

    out_root = config.ROOT / dcfg.get("output_dir", "queue")
    out_root.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().strftime("%Y%m%d")

    # First pass: write posting.md + score.json, identify which folders need drafting.
    pending: list[tuple[str, Path, pd.Series]] = []
    for _, row in df.iterrows():
        fit = int(row.get("fit_score") or 0)
        company = row.get("company") or "unknown"
        url_hash = hashlib.sha256((row.get("job_url") or "").encode()).hexdigest()[:6]
        slug = f"{stamp}-{fit:03d}-{_slug(company)}-{url_hash}"
        folder = out_root / slug
        folder.mkdir(parents=True, exist_ok=True)

        (folder / "posting.md").write_text(
            f"# {row.get('title')} — {company}\n\n"
            f"- Location: {row.get('location')}\n"
            f"- URL: {row.get('job_url')}\n"
            f"- Category: {row.get('category')}\n"
            f"- Site: {row.get('site')}\n"
            f"- Date posted: {row.get('date_posted')}\n\n"
            f"## Description\n\n{row.get('description') or ''}\n"
        )
        (folder / "score.json").write_text(json.dumps({
            "fit_score": fit,
            "verdict": row.get("verdict"),
            "strengths": row.get("strengths"),
            "gaps": row.get("gaps"),
            "risks": row.get("risks"),
            "top_requirements": row.get("top_requirements"),
            "recommended_template": row.get("recommended_template"),
        }, indent=2, default=str))

        if (folder / "resume.md").exists() and (folder / "cover_letter.md").exists():
            console.log(f"[dim]exists [/dim] {slug}")
            continue
        pending.append((slug, folder, row))

    if not pending:
        console.print(f"[bold green]all drafted[/bold green] -> {out_root}")
        return

    # Build parallel prompt batches: resumes + cover letters.
    resume_prompts: list[tuple[str, str]] = []
    cover_prompts: list[tuple[str, str]] = []
    for slug, _, row in pending:
        base = _build_kwargs(resume_yaml, row)
        resume_prompts.append((slug, RESUME_PROMPT.format(**base)))
        cover_prompts.append((slug, COVER_PROMPT.format(words=cover_words, **base)))

    console.log(f"drafting {len(pending)} (parallel resume + cover passes)")

    def _prog(label: str):
        def cb(done: int, total: int) -> None:
            if done % 3 == 0 or done == total:
                console.log(f"  [dim]{label}[/dim] {done}/{total}")
        return cb

    resume_results = llm.call_many(resume_prompts, progress=_prog("resume"))
    cover_results = llm.call_many(cover_prompts, progress=_prog("cover"))

    for slug, folder, _ in pending:
        rm = resume_results.get(slug, "")
        cv = cover_results.get(slug, "")
        if rm.startswith("__ERROR__") or cv.startswith("__ERROR__"):
            console.log(f"[red]error[/red] {slug}: resume={rm[:80]} cover={cv[:80]}")
            continue
        (folder / "resume.md").write_text(rm.strip())
        (folder / "cover_letter.md").write_text(cv.strip())
        console.log(f"[green]drafted[/green] {slug}")

    console.print(f"[bold green]done[/bold green] -> {out_root}")


if __name__ == "__main__":
    main()
