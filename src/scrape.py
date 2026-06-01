"""Scrape job postings via JobSpy across configured boards + searches.

Output: data/raw/postings-{YYYYMMDD}.parquet

Filters applied AFTER scrape (location allow/deny, age, keyword bans).
No auto-apply — read-only discovery.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import click
import pandas as pd
from jobspy import scrape_jobs
from rich.console import Console
from rich.table import Table

from . import config

console = Console()


def _location_passes(loc: str, allow: list[str], deny: list[str]) -> bool:
    """Substring filter — case-insensitive. Empty allow list = pass."""
    if not isinstance(loc, str):
        loc = ""
    low = loc.lower()
    if any(d in low for d in deny):
        return False
    if not allow:
        return True
    return any(a in low for a in allow)


def _jd_passes(description: str, deny_kw: list[str]) -> bool:
    if not isinstance(description, str):
        return True
    low = description.lower()
    return not any(k in low for k in deny_kw)


def scrape_one(
    search_term: str,
    sites: list[str],
    results_per_search: int,
    hours_old: int,
) -> pd.DataFrame:
    """One JobSpy call. Returns normalized DataFrame."""
    df = scrape_jobs(
        site_name=sites,
        search_term=search_term,
        location="",
        results_wanted=results_per_search,
        hours_old=hours_old,
        country_indeed="United Kingdom",
        linkedin_fetch_description=True,
        verbose=0,
    )
    if df is None or df.empty:
        return pd.DataFrame()
    df["search_term"] = search_term
    return df


@click.command()
@click.option("--category", default=None, help="Limit to one target category.")
@click.option("--dry-run", is_flag=True, help="Print plan, don't scrape.")
def main(category: str | None, dry_run: bool) -> None:
    config.load_env()
    cfg = config.load_config()
    scfg = cfg["scrape"]

    searches: dict[str, list[str]] = scfg["searches"]
    if category:
        if category not in searches:
            raise click.BadParameter(f"Unknown category {category}. Have: {list(searches)}")
        searches = {category: searches[category]}

    sites = scfg["sites"]
    n = scfg["results_per_search"]
    hrs = scfg["hours_old"]
    filt = scfg["filters"]

    if dry_run:
        table = Table(title="Scrape plan (dry-run)")
        table.add_column("category")
        table.add_column("term")
        for cat, terms in searches.items():
            for t in terms:
                table.add_row(cat, t)
        console.print(table)
        console.print(f"sites={sites} per-search={n} hours_old={hrs}")
        return

    frames: list[pd.DataFrame] = []
    for cat, terms in searches.items():
        for term in terms:
            console.log(f"[cyan]scraping[/cyan] {cat} :: {term}")
            try:
                df = scrape_one(term, sites, n, hrs)
            except Exception as e:
                console.log(f"[red]error[/red] {cat}/{term}: {e}")
                continue
            if df.empty:
                console.log(f"  -> 0 hits")
                continue
            df["category"] = cat
            frames.append(df)
            console.log(f"  -> {len(df)} hits")

    if not frames:
        console.print("[yellow]No results.[/yellow]")
        return

    raw = pd.concat(frames, ignore_index=True)
    before = len(raw)

    # Soft-filter: JD-keyword bans only (catches "US citizens only" etc).
    # Location is NOT hard-filtered here — LLM scorer judges geo/visa fit per posting
    # via the `risks` field. Hard pre-filtering kills too much (LinkedIn geo-biases
    # to US, miscategorizes remote-global roles, etc).
    raw["_jd_ok"] = raw["description"].apply(
        lambda x: _jd_passes(x, filt.get("deny_keywords_in_jd", []))
    )
    out = raw[raw["_jd_ok"]].copy()
    out = out.drop(columns=["_jd_ok"])
    out = out.drop_duplicates(subset=["job_url"], keep="first")

    after = len(out)

    out_dir = config.ROOT / "data" / "raw"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().strftime("%Y%m%d")
    out_path = out_dir / f"postings-{stamp}.parquet"
    out.to_parquet(out_path, index=False)

    console.print(
        f"[green]scraped[/green] {before} -> [green]kept[/green] {after} "
        f"({before - after} filtered) -> [bold]{out_path}[/bold]"
    )


if __name__ == "__main__":
    main()
