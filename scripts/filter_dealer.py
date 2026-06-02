"""Filter ATS-direct latest to FX/CFD Dealer + trader-relevant titles,
excluding already-applied URLs (tracker/live.csv status=applied).

Writes data/dealer-filtered.parquet for downstream scoring + drafting.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import pandas as pd
from rich.console import Console

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

console = Console()

# Dealer / trader / FX risk / liquidity / treasury titles
DEALER_KW = re.compile(
    r"\b(dealer|dealing|fx trader|cfd trader|otc trader|spot fx|"
    r"derivatives trader|delta one trader|institutional trader|"
    r"experienced trader|graduate trader|digital assets trader|"
    r"foreign exchange|fx counterparty|fx risk|liquidity management|"
    r"treasury (?:dealer|specialist|analyst)|credit risk|"
    r"quant(?:itative)? trader|systematic trader)\b",
    re.I,
)

NEG = re.compile(
    r"\b(intern|sales|customer support|talent acquisition|hr |human resources|"
    r"graphic|brand|content|copywriter|recruiter|administrative|legal counsel|"
    r"compliance officer|aml|kyc|accountant|tax )\b",
    re.I,
)

GLOBAL_OK = [
    "remote", "london", "paris", "amsterdam", "berlin", "dublin", "dubai",
    "singapore", "tokyo", "hong kong", "prague", "frankfurt", "zurich",
    "geneva", "milan", "madrid", "brussels", "warsaw", "limassol", "cyprus",
    "malta", "taipei", "toronto", "sydney", "lagos", "lomé", "abidjan",
    "dakar", "casablanca", "cairo", "johannesburg", "europe", "asia",
    "americas", "portugal", "lisbon", "luxembourg",
]


def _loc_ok(loc: str) -> bool:
    if not isinstance(loc, str) or not loc:
        return True
    low = loc.lower()
    if any(c in low for c in GLOBAL_OK):
        return True
    if "united states" in low:
        return False
    if re.search(r",\s*[A-Z]{2}\b", loc):
        return False
    return True


def _applied_urls() -> set[str]:
    out: set[str] = set()
    p = ROOT / "tracker" / "live.csv"
    if not p.exists():
        return out
    with open(p) as f:
        for r in csv.DictReader(f):
            if r.get("status") == "applied":
                u = (r.get("role_url") or "").strip()
                if u:
                    out.add(u)
    return out


def main() -> None:
    raw_dir = ROOT / "data" / "raw"
    ats_files = sorted(raw_dir.glob("ats-direct-*.parquet"))
    if not ats_files:
        console.print("[red]No ats-direct parquet. Run `make fetch` first.[/red]")
        sys.exit(1)
    src = ats_files[-1]
    df = pd.read_parquet(src)
    console.log(f"loaded {len(df)} from {src.name}")

    applied = _applied_urls()
    console.log(f"excluding {len(applied)} already-applied URLs")

    df["_dealer"] = df["title"].fillna("").apply(lambda t: bool(DEALER_KW.search(t)))
    df["_neg"] = df["title"].fillna("").apply(lambda t: bool(NEG.search(t)))
    df["_loc"] = df["location"].apply(_loc_ok)
    df["_applied"] = df["job_url"].apply(lambda u: u in applied)
    keep = df[df["_dealer"] & ~df["_neg"] & df["_loc"] & ~df["_applied"]].copy()
    keep = keep.drop(columns=[c for c in keep.columns if c.startswith("_")])
    keep = keep.drop_duplicates(subset=["job_url"], keep="first")

    out = ROOT / "data" / "dealer-filtered.parquet"
    keep.to_parquet(out, index=False)
    console.print(
        f"[green]wrote[/green] {len(keep)} of {len(df)} -> [bold]{out.relative_to(ROOT)}[/bold]"
    )
    for _, r in keep.iterrows():
        console.print(f"  {r['company'][:18]:18} | {r['title'][:60]:60} | {r['location'][:25]}")


if __name__ == "__main__":
    main()
