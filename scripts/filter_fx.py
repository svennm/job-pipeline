"""Pre-filter the latest raw parquet to FX-relevant titles.

Cheaper than LLM-scoring everything. Drops non-FX titles + US-only postings
(US visa is hard for our candidate). Writes data/fx-filtered.parquet.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd
from rich.console import Console

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

console = Console()

FX_KW = re.compile(
    r"\b(fx|forex|currenc|trading|trader|quant|electronic|emarket|e-market|spot|broker|"
    r"dealer|dealing|exchange|venue|microstructure|liquidity|execution|matching|"
    r"order book|market[- ]?making|market maker|risk|treasury|metatrader|mt5|mql5|"
    r"ctrader|cfd|low latency|crypto|derivatives|derivative|perpetual|swap|"
    r"nautilus|backtest)\b",
    re.I,
)
NEG = re.compile(
    r"\b(intern|sales|customer support|talent acquisition|hr |human resources|graphic|"
    r"brand|content|copywriter|recruiter|administrative|legal counsel|compliance officer|"
    r"aml|kyc|accountant|tax )\b",
    re.I,
)

US_STATE_TAILS = [", NY", ", CA", ", TX", ", IL", ", MA", ", FL", ", WA", ", CO", ", NJ"]
GLOBAL_OK = [
    "remote", "london", "paris", "amsterdam", "berlin", "dublin", "dubai",
    "singapore", "tokyo", "hong kong", "prague", "frankfurt", "zurich",
    "geneva", "milan", "madrid", "brussels", "warsaw", "limassol", "cyprus",
    "malta", "taipei", "toronto", "sydney", "lagos", "lomé", "abidjan",
    "dakar", "casablanca", "cairo", "johannesburg", "europe", "asia",
    "americas",
]


def _loc_ok(loc: str) -> bool:
    if not isinstance(loc, str) or not loc:
        return True
    low = loc.lower()
    if any(c in low for c in GLOBAL_OK):
        return True
    if "united states" in low:
        return False
    if any(s in loc for s in US_STATE_TAILS):
        return False
    return True


def main() -> None:
    raw_dir = ROOT / "data" / "raw"
    ats_files = sorted(raw_dir.glob("ats-direct-*.parquet"))
    if not ats_files:
        console.print("[red]No data/raw/ats-direct-*.parquet — run `make fetch` first.[/red]")
        sys.exit(1)
    src = ats_files[-1]
    df = pd.read_parquet(src)
    console.log(f"loaded {len(df)} from {src.name}")

    df["_fx"] = df["title"].fillna("").apply(lambda t: bool(FX_KW.search(t)))
    df["_neg"] = df["title"].fillna("").apply(lambda t: bool(NEG.search(t)))
    df["_loc"] = df["location"].apply(_loc_ok)
    keep = df[df["_fx"] & ~df["_neg"] & df["_loc"]].copy()
    keep = keep.drop(columns=[c for c in keep.columns if c.startswith("_")])
    keep = keep.drop_duplicates(subset=["job_url"], keep="first")

    out = ROOT / "data" / "fx-filtered.parquet"
    keep.to_parquet(out, index=False)
    console.print(
        f"[green]wrote[/green] {len(keep)} of {len(df)} -> [bold]{out.relative_to(ROOT)}[/bold]"
    )
    # Quick summary
    by_co = keep["company"].value_counts()
    console.print("[bold]by company:[/bold]")
    for c, n in by_co.head(15).items():
        console.print(f"  {n:3d}  {c}")


if __name__ == "__main__":
    main()
