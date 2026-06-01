"""Direct ATS source: pull jobs straight from Greenhouse / Lever / Ashby boards.

Each ATS exposes a public read-only API listing every open posting for a given
company token/slug. This is far higher signal than keyword-fuzzy LinkedIn
scrapes for our specific targets — every result is genuinely from a company we
care about, and every URL is auto-submittable (greenhouse/lever/ashby).

Output schema matches the JobSpy parquet (so score.py works unchanged):
    job_url, title, company, location, description, date_posted, category,
    search_term, site, is_remote, job_url_direct

Inputs:
    config/companies.yaml — map of company -> {greenhouse, lever, ashby} slugs +
                            category. Only companies listed are probed.

Usage:
    python -m src.sources.ats_direct --output data/raw/ats-{date}.parquet
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import click
import pandas as pd
import requests
import yaml
from rich.console import Console
from rich.progress import track

from .. import config

console = Console()

UA = "job-pipeline/0.1 (svennm@github)"
TIMEOUT = 20


def _normalize_record(
    *,
    job_url: str,
    title: str,
    company: str,
    location: str,
    description: str,
    date_posted: str | None,
    category: str,
    ats: str,
    is_remote: bool = False,
) -> dict[str, Any]:
    return {
        "job_url": job_url,
        "job_url_direct": job_url,
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "date_posted": date_posted,
        "category": category,
        "search_term": f"ats:{ats}",
        "site": f"ats_direct:{ats}",
        "is_remote": is_remote,
    }


# ============== Greenhouse ==============

def fetch_greenhouse(slug: str, company: str, category: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    if r.status_code != 200:
        return []
    data = r.json()
    jobs = data.get("jobs", [])
    rows: list[dict] = []
    for j in jobs:
        rows.append(_normalize_record(
            job_url=j.get("absolute_url", ""),
            title=j.get("title", ""),
            company=company,
            location=(j.get("location") or {}).get("name", ""),
            description=(j.get("content") or "")[:30000],
            date_posted=j.get("updated_at"),
            category=category,
            ats="greenhouse",
        ))
    return rows


# ============== Lever ==============

def fetch_lever(slug: str, company: str, category: str) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    if r.status_code != 200:
        return []
    try:
        data = r.json()
    except Exception:
        return []
    rows: list[dict] = []
    for j in data:
        cats = j.get("categories") or {}
        loc = cats.get("location") or cats.get("allLocations", [None])[0] or ""
        desc_parts = [j.get("descriptionPlain") or "", j.get("additionalPlain") or ""]
        rows.append(_normalize_record(
            job_url=j.get("hostedUrl") or j.get("applyUrl") or "",
            title=j.get("text", ""),
            company=company,
            location=loc,
            description="\n\n".join(p for p in desc_parts if p)[:30000],
            date_posted=(dt.datetime.utcfromtimestamp(j["createdAt"] / 1000).isoformat()
                         if j.get("createdAt") else None),
            category=category,
            ats="lever",
            is_remote=("remote" in (loc or "").lower()),
        ))
    return rows


# ============== Ashby ==============

ASHBY_QUERY = """
query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) {
  jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $organizationHostedJobsPageName) {
    jobPostings {
      id title teamId locationName workplaceType employmentType
      compensationTierSummary publishedDate
    }
  }
}
"""

def fetch_ashby(slug: str, company: str, category: str) -> list[dict]:
    url = "https://jobs.ashbyhq.com/api/non-user-graphql"
    r = requests.post(
        url,
        headers={"User-Agent": UA, "Content-Type": "application/json"},
        json={
            "operationName": "ApiJobBoardWithTeams",
            "query": ASHBY_QUERY,
            "variables": {"organizationHostedJobsPageName": slug},
        },
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        return []
    try:
        data = r.json()
    except Exception:
        return []
    board = ((data.get("data") or {}).get("jobBoard") or {})
    postings = board.get("jobPostings") or []
    rows: list[dict] = []
    for p in postings:
        job_id = p.get("id")
        job_url = f"https://jobs.ashbyhq.com/{slug}/{job_id}"
        rows.append(_normalize_record(
            job_url=job_url,
            title=p.get("title", ""),
            company=company,
            location=p.get("locationName", ""),
            description="",  # description fetch would need a second API call per posting
            date_posted=p.get("publishedDate"),
            category=category,
            ats="ashby",
            is_remote=(p.get("workplaceType") == "Remote"),
        ))
    return rows


# ============== Driver ==============

FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
}


def _load_companies() -> dict[str, dict]:
    """Merge auto-probed companies.yaml with hand-curated companies.manual.yaml.
    Manual overrides win — used for companies whose slug the probe couldn't guess.
    """
    auto = config.ROOT / "config" / "companies.yaml"
    manual = config.ROOT / "config" / "companies.manual.yaml"
    out: dict[str, dict] = {}
    if auto.exists():
        out.update(yaml.safe_load(auto.read_text()) or {})
    if manual.exists():
        out.update(yaml.safe_load(manual.read_text()) or {})
    if not out:
        raise FileNotFoundError(
            f"No companies in {auto} or {manual}. Run scripts/probe_company_slugs.py first."
        )
    return out


@click.command()
@click.option("--companies-file", default=None, help="Override path to companies.yaml")
@click.option("--limit", default=None, type=int, help="Stop after probing N companies.")
@click.option("--ats", default=None, help="Limit to one ATS: greenhouse/lever/ashby")
@click.option("--dry-run", is_flag=True, help="Show plan, no HTTP.")
def main(companies_file: str | None, limit: int | None, ats: str | None, dry_run: bool) -> None:
    p = Path(companies_file) if companies_file else None
    companies = (
        yaml.safe_load(open(p)) if p and p.exists() else _load_companies()
    )

    plan: list[tuple[str, str, str, str]] = []  # (company, category, ats, slug)
    for company, info in companies.items():
        cat = info.get("category", "fintech_generalist")
        for ats_name, slug in info.get("slugs", {}).items():
            if ats and ats_name != ats:
                continue
            if not slug:
                continue
            plan.append((company, cat, ats_name, slug))
    if limit:
        plan = plan[:limit]

    console.log(f"plan: {len(plan)} probes across {len(companies)} companies")
    if dry_run:
        for c, cat, a, s in plan[:30]:
            console.print(f"  {a:11} {s:30} -> {c} ({cat})")
        return

    all_rows: list[dict] = []
    ok = 0
    for company, cat, ats_name, slug in track(plan, description="probing"):
        fetcher = FETCHERS[ats_name]
        try:
            rows = fetcher(slug, company, cat)
        except Exception as e:
            console.log(f"[red]error[/red] {company}/{ats_name}: {e}")
            continue
        if rows:
            ok += 1
            console.log(f"[green]{len(rows):3d}[/green] {ats_name:11} {company}")
        all_rows.extend(rows)

    if not all_rows:
        console.print("[yellow]no postings found[/yellow]")
        return

    df = pd.DataFrame(all_rows).drop_duplicates(subset=["job_url"], keep="first")
    stamp = dt.date.today().strftime("%Y%m%d")
    out_path = config.ROOT / "data" / "raw" / f"ats-direct-{stamp}.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    console.print(f"[green]wrote[/green] {len(df)} rows from {ok} companies -> [bold]{out_path}[/bold]")


if __name__ == "__main__":
    main()
