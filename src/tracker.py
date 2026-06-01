"""Append queued drafts into tracker/live.csv (16-column schema, see tracker/schema.md).

Usage:
  python -m src.tracker append   # scan queue/, add new rows with status=to_apply
  python -m src.tracker mark <slug> --status applied   # update one row
"""
from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

import click
from rich.console import Console

from . import config

console = Console()

COLS = [
    "company", "category", "hq", "role_focus", "role_url", "priority",
    "recruiter_name", "recruiter_contact", "engineer_name", "engineer_linkedin",
    "template_used", "applied_date", "status", "follow_up_date", "notes", "last_contact",
]


def _tracker_path() -> Path:
    cfg = config.load_config()
    p = config.ROOT / cfg["tracker"]["csv_path"]
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _ensure_header(p: Path) -> None:
    if p.exists() and p.stat().st_size > 0:
        return
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)


def _read_rows(p: Path) -> list[dict]:
    if not p.exists():
        return []
    with open(p, newline="") as f:
        return list(csv.DictReader(f))


def _write_rows(p: Path, rows: list[dict]) -> None:
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in COLS})


def _priority_for(fit_score: int) -> str:
    if fit_score >= 80:
        return "A"
    if fit_score >= 65:
        return "B"
    return "C"


@click.group()
def cli() -> None:
    pass


@cli.command()
def append() -> None:
    """Scan queue/ for drafts not yet in tracker; add as status=to_apply."""
    p = _tracker_path()
    _ensure_header(p)
    rows = _read_rows(p)
    existing_keys = {(r["company"].lower(), r["role_focus"].lower()) for r in rows}

    queue = config.ROOT / "queue"
    added = 0
    for folder in sorted(queue.glob("*")):
        if not folder.is_dir():
            continue
        score_path = folder / "score.json"
        posting_path = folder / "posting.md"
        if not score_path.exists() or not posting_path.exists():
            continue
        score = json.loads(score_path.read_text())
        head = posting_path.read_text().splitlines()
        title = head[0].lstrip("# ").strip() if head else folder.name
        company = title.split(" — ")[-1] if " — " in title else "unknown"
        role = title.split(" — ")[0] if " — " in title else title

        key = (company.lower(), role.lower())
        if key in existing_keys:
            continue

        loc = ""
        url = ""
        category = ""
        for line in head:
            if line.startswith("- Location:"):
                loc = line.split(":", 1)[1].strip()
            elif line.startswith("- URL:"):
                url = line.split(":", 1)[1].strip()
            elif line.startswith("- Category:"):
                category = line.split(":", 1)[1].strip()

        fit = int(score.get("fit_score") or 0)
        rows.append({
            "company": company,
            "category": category,
            "hq": loc,
            "role_focus": role,
            "role_url": url,
            "priority": _priority_for(fit),
            "recruiter_name": "",
            "recruiter_contact": "",
            "engineer_name": "",
            "engineer_linkedin": "",
            "template_used": score.get("recommended_template") or category or "custom",
            "applied_date": "",
            "status": "to_apply",
            "follow_up_date": "",
            "notes": f"queue:{folder.name} score:{fit} verdict:{score.get('verdict')}",
            "last_contact": "",
        })
        added += 1
        existing_keys.add(key)

    _write_rows(p, rows)
    console.print(f"[green]appended[/green] {added} new rows -> {p}")


@cli.command()
@click.argument("company")
@click.option("--status", required=True, help="applied / followed_up / replied / rejected / ghosted etc")
@click.option("--note", default=None, help="Append to notes column")
def mark(company: str, status: str, note: str | None) -> None:
    """Update status for a company row. Case-insensitive substring match."""
    p = _tracker_path()
    rows = _read_rows(p)
    today = dt.date.today().isoformat()
    hits = 0
    for r in rows:
        if company.lower() in r["company"].lower():
            r["status"] = status
            if status == "applied":
                r["applied_date"] = today
                follow = dt.date.today() + dt.timedelta(days=7)
                r["follow_up_date"] = follow.isoformat()
            r["last_contact"] = today
            if note:
                sep = " | " if r["notes"] else ""
                r["notes"] = f"{r['notes']}{sep}{note}"
            hits += 1
    _write_rows(p, rows)
    console.print(f"[green]updated[/green] {hits} row(s) for '{company}' -> status={status}")


if __name__ == "__main__":
    cli()
