"""Probe ATS endpoints to find each seed company's Greenhouse/Lever/Ashby slug.

Run once to bootstrap config/companies.yaml. For each company in seed.csv,
generates slug candidates and probes each ATS endpoint. Outputs a yaml mapping
that the ats_direct fetcher consumes.

Usage:
  python scripts/probe_company_slugs.py
  python scripts/probe_company_slugs.py --priority A --output config/companies.yaml
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import click
import requests
import yaml
from rich.console import Console
from rich.progress import track

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

console = Console()
UA = "job-pipeline/0.1 (slug-probe)"
TIMEOUT = 8


def _slug_candidates(name: str) -> list[str]:
    """Generate slug candidates for a company name. Order = try-first ranking."""
    n = name.lower().strip()
    for suf in [", inc.", " inc.", " inc", " llc", " ltd", " limited", " group", " (sig)", " plc",
                " holdings", " corp", " corporation", " ag", " sa", " gmbh", " bv", " technologies"]:
        if n.endswith(suf):
            n = n[: -len(suf)].strip()
    # Strip ".io" / ".com" suffix in name (Polygon.io -> polygon, polygonio)
    if "." in n:
        prefix = n.split(".")[0]
        n_alt = n.replace(".", "")
    else:
        prefix = None
        n_alt = n
    # Strip parenthetical aliases
    n = re.sub(r"\s*\([^)]+\)\s*", " ", n).strip()
    n_alt = re.sub(r"\s*\([^)]+\)\s*", " ", n_alt).strip()

    base = re.sub(r"[^a-z0-9 ]+", "", n).strip()
    base_alt = re.sub(r"[^a-z0-9 ]+", "", n_alt).strip()

    # Ordered list (most-likely first)
    out: list[str] = []
    def add(s: str) -> None:
        if s and s not in out:
            out.append(s)

    add(base.replace(" ", ""))                                  # b2c2
    add(base.replace(" ", "-"))                                 # flow-traders
    add(base_alt.replace(" ", ""))                              # polygonio
    add(base_alt.replace(" ", "-"))                             # polygon-io
    if prefix:
        add(prefix)                                             # polygon (from Polygon.io)
    if " " in base:
        add(base.split()[0])                                    # bank from Bank of Africa
    # Lowercase no-space versions of common acronym/short forms
    add(re.sub(r"\s+", "", base))
    # Strip "the"
    if base.startswith("the "):
        add(base[4:].replace(" ", ""))
        add(base[4:].replace(" ", "-"))
    # Acronyms like "GSR" stay as is
    add(re.sub(r"[^a-z0-9]", "", base))
    # First two words joined
    words = base.split()
    if len(words) >= 2:
        add("".join(words[:2]))
        add("-".join(words[:2]))
    return out


def probe_greenhouse(slug: str) -> bool:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false"
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
        return r.status_code == 200 and isinstance(r.json().get("jobs"), list)
    except Exception:
        return False


def probe_lever(slug: str) -> bool:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=1"
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
        return r.status_code == 200 and isinstance(r.json(), list)
    except Exception:
        return False


def probe_ashby(slug: str) -> bool:
    url = "https://jobs.ashbyhq.com/api/non-user-graphql"
    q = """query Q($n: String!) { jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $n) { jobPostings { id } } }"""
    try:
        r = requests.post(
            url,
            headers={"User-Agent": UA, "Content-Type": "application/json"},
            json={"query": q, "variables": {"n": slug}},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return False
        d = r.json()
        return bool((d.get("data") or {}).get("jobBoard"))
    except Exception:
        return False


PROBES = {"greenhouse": probe_greenhouse, "lever": probe_lever, "ashby": probe_ashby}


@click.command()
@click.option("--seed", default="tracker/seed.csv", help="Tracker seed CSV path.")
@click.option("--output", default="config/companies.yaml", help="Output YAML path.")
@click.option("--priority", default=None, help="Filter to priority (A/B/C).")
@click.option("--limit", default=None, type=int)
def main(seed: str, output: str, priority: str | None, limit: int | None) -> None:
    seed_path = ROOT / seed
    out_path = ROOT / output

    companies: list[dict] = []
    with open(seed_path) as f:
        for row in csv.DictReader(f):
            if priority and row["priority"] != priority:
                continue
            companies.append(row)
    if limit:
        companies = companies[:limit]

    console.log(f"probing {len(companies)} companies")

    found: dict[str, dict] = {}
    for row in track(companies, description="probing"):
        company = row["company"]
        cat = row["category"]
        cands = _slug_candidates(company)
        slugs: dict[str, str] = {}
        for ats_name, probe in PROBES.items():
            for c in cands:
                if probe(c):
                    slugs[ats_name] = c
                    break
        if slugs:
            found[company] = {
                "category": cat,
                "hq": row.get("hq", ""),
                "slugs": slugs,
            }
            console.log(f"[green]found[/green] {company} -> {slugs}")
        else:
            console.log(f"[dim]miss [/dim] {company} (tried {cands})")

    # Merge with existing if file exists
    existing = {}
    if out_path.exists():
        existing = yaml.safe_load(out_path.read_text()) or {}
    merged = {**existing, **found}

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml.safe_dump(merged, sort_keys=True, allow_unicode=True))
    console.print(
        f"[bold green]done[/bold green] — {len(found)}/{len(companies)} probed, "
        f"{len(merged)} total in {out_path.relative_to(ROOT)}"
    )


if __name__ == "__main__":
    main()
