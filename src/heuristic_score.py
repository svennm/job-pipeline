"""Heuristic fit-scoring (no LLM).

LLM scoring via `claude -p` is too slow at 94+ postings/run (~60s/call incl.
subprocess startup). Solution: deterministic heuristic scorer that ranks every
posting cheaply, then reserve LLM (Sonnet) for the top-N tailored drafts.

Score 0..100:
  - 30 pts: company priority (A=30, B=20, C=10) — from seed.csv lookup
  - 25 pts: title category match (FX/Quant/Trading Systems/MT5 etc)
  - 20 pts: location fit (remote/EU/Africa/SG/Dubai = full; US-only = 0)
  - 15 pts: stack overlap (MT5/MQL5/Nautilus/Python/FastAPI in JD)
  - 10 pts: relevance keywords in JD body (microstructure, prop, execution)
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import re
from pathlib import Path

import click
import pandas as pd
from rich.console import Console

from . import config

console = Console()


# ============== Component scorers ==============

def _priority_for_company(company: str, seed_map: dict[str, str]) -> int:
    if not company:
        return 10
    p = seed_map.get(company.strip().lower())
    return {"A": 30, "B": 20, "C": 10}.get(p or "C", 10)


TITLE_BUCKETS = [
    # (regex, points)
    # Dealer roles — directly relevant to candidate's Africa Lease FX execution
    (re.compile(r"\b(fx|cfd|spot fx)[- ]?dealer\b", re.I), 28),
    (re.compile(r"\b(dealer|dealing desk)\b", re.I), 22),
    (re.compile(r"\b(treasury (?:dealer|analyst|specialist))\b", re.I), 24),
    (re.compile(r"\botc (?:trader|dealer)\b", re.I), 22),
    (re.compile(r"\b(trading systems? engineer|execution engineer)\b", re.I), 25),
    (re.compile(r"\b(quant(?:itative)? (?:developer|engineer|trader|researcher))\b", re.I), 22),
    (re.compile(r"\b(mt5|metatrader|mql5|ctrader)\b", re.I), 25),
    (re.compile(r"\b(fx|forex|currenc(?:y|ies)) (?:engineer|trader|developer|analyst)\b", re.I), 24),
    (re.compile(r"\b(low.?latency|hft|high.frequency)\b", re.I), 22),
    (re.compile(r"\b(market.?making|market maker)\b", re.I), 22),
    (re.compile(r"\b(derivat\w+) (?:trader|developer)\b", re.I), 20),
    (re.compile(r"\b(systematic|algorithmic) (?:trader|engineer)\b", re.I), 20),
    (re.compile(r"\b(trading|trader|treasury)\b", re.I), 14),
    (re.compile(r"\b(software engineer|backend engineer|platform engineer)\b", re.I), 10),
    (re.compile(r"\b(developer|engineer)\b", re.I), 6),
]


def _title_score(title: str) -> int:
    if not title:
        return 0
    for pat, pts in TITLE_BUCKETS:
        if pat.search(title):
            return pts
    return 0


# Visa-friendly tier (Togolese passport): full credit + bonus.
# See memory: project_job_search_frame.md.
GEO_TIER_TOP = re.compile(
    r"\b(dubai|united arab emirates|uae|mauritius|kigali|rwanda|lisbon|portugal|"
    r"remote.{0,40}(?:crypto|defi|global)|crypto.{0,40}remote|defi.{0,40}remote)\b",
    re.I,
)
# Generally OK — EU / SG / HK / Africa / general remote
GEO_TIER_OK = re.compile(
    r"\b(remote|london|paris|amsterdam|berlin|dublin|singapore|tokyo|hong kong|"
    r"prague|frankfurt|zurich|geneva|milan|madrid|brussels|warsaw|limassol|"
    r"cyprus|malta|taipei|toronto|sydney|lagos|lomé|abidjan|dakar|casablanca|"
    r"cairo|johannesburg|europe|asia|americas|emea|apac|globally)\b",
    re.I,
)
US_TAIL = re.compile(
    r",\s*(?:NY|CA|TX|IL|MA|FL|WA|CO|NJ|GA|VA|MD|OH|PA|NC|TN|IN|MI|MO|AZ|NV|OR|"
    r"MN|WI|CT|DC|UT|KY|SC|LA|AL|AR|KS|NE|IA|OK|MS|HI|ID|MT|ME|NH|RI|VT|WV|WY|"
    r"SD|ND|AK|DE|NM)\b"
)
US_FULL = re.compile(r"united states\b", re.I)


def _location_score(location: str, is_remote: bool = False) -> int:
    loc = location or ""
    # Top-tier visa-friendly hits get a boost (over the 20 max)
    if GEO_TIER_TOP.search(loc):
        return 25
    if is_remote:
        return 20
    if not loc:
        return 10
    if GEO_TIER_OK.search(loc):
        return 18
    if US_FULL.search(loc) and not GEO_TIER_OK.search(loc):
        return 3   # US-only: harsh penalty
    if US_TAIL.search(loc):
        return 3
    return 10


# Companies known to be credential-gated (Citadel/JS/Optiver/etc) — discount
# heavily even if title matches. Per user frame: don't waste cycles on walls.
TIER1_GATED = re.compile(
    r"\b(citadel|jane street|optiver|imc trading|jump trading|hudson river|"
    r"two sigma|d\.?e\.? shaw|de shaw|aqr|bridgewater|susquehanna|sig\b|"
    r"jpmorgan|goldman sachs|morgan stanley|bofa|bank of america)\b",
    re.I,
)


def _credential_gated_penalty(company: str) -> int:
    if not company:
        return 0
    if TIER1_GATED.search(company):
        return -20
    return 0


STACK_KW = re.compile(
    r"\b(metatrader|mt5|mql5|nautilus|python|fastapi|fastify|docker|"
    r"low.?latency|c\+\+|rust|go(?:lang)?|typescript|kdb|onyx|wing|kafka|nats|"
    r"prometheus|grafana|aws|kubernetes|sql|postgres|parquet)\b",
    re.I,
)


def _stack_score(description: str) -> int:
    if not description:
        return 0
    hits = len(set(m.group(0).lower() for m in STACK_KW.finditer(description)))
    return min(hits * 3, 15)


REL_KW = re.compile(
    r"\b(microstructure|order book|matching engine|venue|prop|execution|"
    r"smart order routing|risk management|var|monte carlo|backtest|"
    r"walk.?forward|kelly|drawdown|sharpe|sortino|liquidity provision|"
    r"otc|spot fx|derivatives|perpetual|stablecoin)\b",
    re.I,
)


def _relevance_score(description: str) -> int:
    if not description:
        return 0
    hits = len(set(m.group(0).lower() for m in REL_KW.finditer(description)))
    return min(hits * 2, 10)


# ============== Driver ==============

def _seed_priority_map() -> dict[str, str]:
    seed = config.ROOT / "tracker" / "seed.csv"
    if not seed.exists():
        return {}
    out: dict[str, str] = {}
    with open(seed) as f:
        for r in csv.DictReader(f):
            out[r["company"].strip().lower()] = r["priority"]
    return out


def score_row(row: pd.Series, seed_map: dict[str, str]) -> dict:
    title = row.get("title", "") or ""
    desc = row.get("description", "") or ""
    company = row.get("company", "") or ""
    loc = row.get("location", "") or ""
    is_remote = bool(row.get("is_remote", False))

    comp = _priority_for_company(company, seed_map)
    title_s = _title_score(title)
    loc_s = _location_score(loc, is_remote)
    stack_s = _stack_score(desc)
    rel_s = _relevance_score(desc)
    penalty = _credential_gated_penalty(company)
    total = max(0, comp + title_s + loc_s + stack_s + rel_s + penalty)

    if total >= 70:
        verdict = "strong_fit"
    elif total >= 55:
        verdict = "fit"
    elif total >= 40:
        verdict = "stretch"
    else:
        verdict = "weak"

    return {
        "fit_score": int(total),
        "verdict": verdict,
        "components": {
            "company_priority": comp,
            "title": title_s,
            "location": loc_s,
            "stack": stack_s,
            "relevance": rel_s,
            "credential_gate_penalty": penalty,
        },
    }


@click.command()
@click.option("--input", "input_path", default=None, help="Override parquet input.")
@click.option("--limit", default=None, type=int)
def main(input_path: str | None, limit: int | None) -> None:
    if input_path:
        raw = Path(input_path)
    else:
        # Prefer fx-filtered, else latest ats-direct
        candidates = [config.ROOT / "data" / "fx-filtered.parquet"]
        candidates += sorted((config.ROOT / "data" / "raw").glob("ats-direct-*.parquet"))[-1:]
        raw = next((p for p in candidates if p.exists()), None)
        if raw is None:
            console.print("[red]No input parquet found.[/red]")
            return

    df = pd.read_parquet(raw)
    if limit:
        df = df.head(limit)
    console.log(f"scoring {len(df)} from {raw.name}")

    seed_map = _seed_priority_map()

    scores = [score_row(r, seed_map) for _, r in df.iterrows()]
    sdf = pd.DataFrame([{**s, "components": json.dumps(s["components"])} for s in scores])
    out = pd.concat([df.reset_index(drop=True), sdf.reset_index(drop=True)], axis=1)
    out = out.sort_values("fit_score", ascending=False)

    stamp = dt.date.today().strftime("%Y%m%d")
    out_path = config.ROOT / "data" / f"scored-{stamp}.parquet"
    out.to_parquet(out_path, index=False)

    console.print(f"[green]wrote[/green] {len(out)} -> [bold]{out_path.relative_to(config.ROOT)}[/bold]")
    console.print("[bold]top 20:[/bold]")
    for _, r in out.head(20).iterrows():
        console.print(f"  [cyan]{r['fit_score']:3d}[/cyan] {r['verdict']:11} | {r['company'][:18]:18} | {r['title'][:55]:55} | {r['location'][:25]}")


if __name__ == "__main__":
    main()
