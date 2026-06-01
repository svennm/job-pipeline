"""Score scraped postings vs resume YAML.

Input:  data/raw/postings-{YYYYMMDD}.parquet OR data/raw/ats-direct-{YYYYMMDD}.parquet (latest)
Output: data/scored-{YYYYMMDD}.parquet

For each posting, asks Claude:
  - fit_score 0..100
  - top 5 JD requirements + which resume bullet covers each
  - 3-line reason (gaps, strengths, risks)

Batched + parallel. Caches by job_url hash so reruns are cheap.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import click
import pandas as pd
from rich.console import Console
from rich.progress import track

from . import config, llm

console = Console()

SCORE_PROMPT = """You are scoring job-posting fit for a candidate.

CANDIDATE RESUME (YAML):
{resume_yaml}

TARGET CATEGORY: {category}

JOB POSTING:
Title: {title}
Company: {company}
Location: {location}
Description (truncated to 4000 chars):
{description}

Score this posting for fit. Be honest — if the candidate is underqualified or the role is geographically/legally out of reach, say so.

Return ONLY valid JSON with this shape:
{{
  "fit_score": <int 0-100>,
  "verdict": "<one of: strong_fit, fit, stretch, weak, no_fit>",
  "top_requirements": [
    {{"req": "<requirement>", "covered_by": "<resume bullet or 'gap'>"}}
  ],
  "strengths": "<one sentence>",
  "gaps": "<one sentence>",
  "risks": "<one sentence — visa/location/seniority/etc>",
  "recommended_template": "<one of: crypto_mm, broker_tech, eu_prop, africa_treasury, fintech_generalist, custom>"
}}

No prose outside the JSON. No markdown fences."""


def _hash_url(url: str) -> str:
    return hashlib.sha256((url or "").encode()).hexdigest()[:16]


def _latest_raw() -> Path:
    """Prefer ats-direct (high-signal) over JobSpy (low-signal). Latest of either."""
    raw_dir = config.ROOT / "data" / "raw"
    ats_files = sorted(raw_dir.glob("ats-direct-*.parquet"))
    jobspy_files = sorted(raw_dir.glob("postings-*.parquet"))
    # Prefer ats-direct
    if ats_files:
        return ats_files[-1]
    if jobspy_files:
        return jobspy_files[-1]
    raise FileNotFoundError("No data/raw/*.parquet — run ats_direct or scrape first.")


def _cache_dir() -> Path:
    d = config.ROOT / "data" / "cache" / "score"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _build_prompt(resume_yaml: str, row: pd.Series) -> str:
    desc = (row.get("description") or "")[:4000]
    return SCORE_PROMPT.format(
        resume_yaml=resume_yaml,
        category=row.get("category", "unknown"),
        title=row.get("title", ""),
        company=row.get("company", ""),
        location=row.get("location", ""),
        description=desc,
    )


def _parse_response(text: str) -> dict:
    """Extract JSON from response, tolerating fenced code blocks."""
    text = text.strip()
    if text.startswith("```"):
        # strip triple-backtick fences
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:]).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # find first { ... last }
        s, e = text.find("{"), text.rfind("}")
        if s >= 0 and e > s:
            try:
                return json.loads(text[s : e + 1])
            except json.JSONDecodeError:
                pass
        return {"fit_score": 0, "verdict": "parse_error", "raw": text[:500]}


def score_one_cached(resume_yaml: str, row: pd.Series) -> dict:
    """Cached score lookup; populates cache on miss via LLM call."""
    cache_path = _cache_dir() / f"{_hash_url(row.get('job_url', ''))}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    prompt = _build_prompt(resume_yaml, row)
    text = llm.call(prompt)
    data = _parse_response(text)
    cache_path.write_text(json.dumps(data))
    return data


@click.command()
@click.option("--input", "input_path", default=None, help="Override raw parquet path.")
@click.option("--limit", default=None, type=int, help="Score only first N postings.")
@click.option("--dry-run", is_flag=True, help="Print plan, don't call LLM.")
@click.option("--prefilter", "prefilter_path", default=None,
              help="Optional pre-filtered parquet (e.g. data/fx-filtered.parquet).")
def main(input_path: str | None, limit: int | None, dry_run: bool, prefilter_path: str | None) -> None:
    config.load_env()
    cfg = config.load_config()
    scfg = cfg["score"]

    if prefilter_path:
        raw_path = Path(prefilter_path)
    elif input_path:
        raw_path = Path(input_path)
    else:
        raw_path = _latest_raw()
    df = pd.read_parquet(raw_path)
    if limit:
        df = df.head(limit)

    console.log(f"loaded [bold]{len(df)}[/bold] postings from {raw_path}")

    if dry_run:
        console.print(f"would score {len(df)} via backend {llm._load().backend}")
        return

    resume = config.load_resume()
    import yaml as _yaml
    resume_yaml = _yaml.safe_dump(resume, sort_keys=False)

    scores: list[dict] = [None] * len(df)  # type: ignore[list-item]

    # Cache lookup pass (free)
    pending: list[tuple[int, pd.Series]] = []
    for i, (_, row) in enumerate(df.iterrows()):
        cache_path = _cache_dir() / f"{_hash_url(row.get('job_url', ''))}.json"
        if cache_path.exists():
            scores[i] = json.loads(cache_path.read_text())
        else:
            pending.append((i, row))

    console.log(f"cached hits: {len(df) - len(pending)} | new calls: {len(pending)}")

    if pending:
        prompts = [(i, _build_prompt(resume_yaml, row)) for i, row in pending]
        # Use Haiku for bulk scoring — ~5x faster + plenty accurate for fit-rank
        score_model = scfg.get("model_haiku", "claude-haiku-4-5-20251001")

        def _progress(done: int, total: int) -> None:
            if done % 5 == 0 or done == total:
                console.log(f"  [dim]progress[/dim] {done}/{total}")

        results = llm.call_many(prompts, model=score_model, progress=_progress)
        for (i, row), text in zip(pending, [results.get(p[0], "") for p in prompts]):
            if text.startswith("__ERROR__:"):
                console.log(f"[red]error[/red] {row.get('company')}: {text[:200]}")
                scores[i] = {"fit_score": 0, "verdict": "error", "raw": text[:500]}
                continue
            data = _parse_response(text)
            scores[i] = data
            cache_path = _cache_dir() / f"{_hash_url(row.get('job_url', ''))}.json"
            cache_path.write_text(json.dumps(data))

    sdf = pd.DataFrame(scores)
    # avoid pandas concat type warnings on object columns
    out = pd.concat([df.reset_index(drop=True), sdf.reset_index(drop=True)], axis=1)

    min_score = scfg.get("min_score", 60)
    out = out.sort_values("fit_score", ascending=False, na_position="last")

    out_dir = config.ROOT / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().strftime("%Y%m%d")
    out_path = out_dir / f"scored-{stamp}.parquet"
    # ensure JSON-y fields are stringified for parquet
    for col in ("top_requirements",):
        if col in out.columns:
            out[col] = out[col].apply(lambda v: json.dumps(v) if not isinstance(v, (str, type(None))) else v)
    out.to_parquet(out_path, index=False)

    kept = (out["fit_score"] >= min_score).sum()
    console.print(
        f"[green]scored[/green] {len(out)} | [green]passing[/green] (>= {min_score}) {kept} -> [bold]{out_path}[/bold]"
    )


if __name__ == "__main__":
    main()
