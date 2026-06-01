"""Score scraped postings vs resume YAML.

Input:  data/raw/postings-{YYYYMMDD}.parquet (latest)
Output: data/scored-{YYYYMMDD}.parquet

For each posting, asks Claude:
  - fit_score 0..100
  - top 5 JD requirements + which resume bullet covers each
  - 3-line reason (gaps, strengths, risks)

Batched. Caches by job_url hash so reruns are cheap.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path

import click
import pandas as pd
from anthropic import Anthropic
from rich.console import Console
from rich.progress import track

from . import config

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
    raw_dir = config.ROOT / "data" / "raw"
    files = sorted(raw_dir.glob("postings-*.parquet"))
    if not files:
        raise FileNotFoundError("No data/raw/postings-*.parquet — run scrape first.")
    return files[-1]


def _cache_dir() -> Path:
    d = config.ROOT / "data" / "cache" / "score"
    d.mkdir(parents=True, exist_ok=True)
    return d


def score_one(
    client: Anthropic,
    model: str,
    resume_yaml: str,
    row: pd.Series,
) -> dict:
    """One Claude call per posting. Cached on disk by job_url hash."""
    cache_path = _cache_dir() / f"{_hash_url(row.get('job_url', ''))}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text())

    desc = (row.get("description") or "")[:4000]
    prompt = SCORE_PROMPT.format(
        resume_yaml=resume_yaml,
        category=row.get("category", "unknown"),
        title=row.get("title", ""),
        company=row.get("company", ""),
        location=row.get("location", ""),
        description=desc,
    )

    resp = client.messages.create(
        model=model,
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {"fit_score": 0, "verdict": "parse_error", "raw": text[:500]}

    cache_path.write_text(json.dumps(data))
    return data


@click.command()
@click.option("--input", "input_path", default=None, help="Override raw parquet path.")
@click.option("--limit", default=None, type=int, help="Score only first N postings.")
@click.option("--dry-run", is_flag=True, help="Print plan, don't call API.")
def main(input_path: str | None, limit: int | None, dry_run: bool) -> None:
    config.load_env()
    cfg = config.load_config()
    scfg = cfg["score"]

    raw_path = Path(input_path) if input_path else _latest_raw()
    df = pd.read_parquet(raw_path)
    if limit:
        df = df.head(limit)

    console.log(f"loaded [bold]{len(df)}[/bold] postings from {raw_path}")

    if dry_run:
        console.print(f"would score {len(df)} with model {scfg['model']}")
        return

    api_key = config.require_env("ANTHROPIC_API_KEY")
    model = os.environ.get("CLAUDE_MODEL") or scfg["model"]
    client = Anthropic(api_key=api_key)
    resume = config.load_resume()
    import yaml as _yaml
    resume_yaml = _yaml.safe_dump(resume, sort_keys=False)

    scores: list[dict] = []
    for _, row in track(df.iterrows(), total=len(df), description="scoring"):
        try:
            s = score_one(client, model, resume_yaml, row)
        except Exception as e:
            console.log(f"[red]error[/red] {row.get('company')}: {e}")
            s = {"fit_score": 0, "verdict": "error", "error": str(e)[:200]}
        scores.append(s)

    sdf = pd.DataFrame(scores)
    out = pd.concat([df.reset_index(drop=True), sdf.reset_index(drop=True)], axis=1)

    min_score = scfg.get("min_score", 60)
    out = out.sort_values("fit_score", ascending=False, na_position="last")

    out_dir = config.ROOT / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().strftime("%Y%m%d")
    out_path = out_dir / f"scored-{stamp}.parquet"
    out.to_parquet(out_path, index=False)

    kept = (out["fit_score"] >= min_score).sum()
    console.print(
        f"[green]scored[/green] {len(out)} | [green]passing[/green] (>= {min_score}) {kept} -> [bold]{out_path}[/bold]"
    )


if __name__ == "__main__":
    main()
