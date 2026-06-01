"""Fetch per-posting question schemas from ATS APIs.

For Greenhouse: GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{id}?questions=true
returns full question list with field types and option values.

For Lever / Ashby / Workable: their public APIs don't expose per-posting question
schemas, so we'll discover questions at runtime via Playwright DOM inspection.

Output: queue/{slug}/questions.json
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import click
import requests
from rich.console import Console

from . import config, ats

console = Console()
UA = "job-pipeline/0.1 (questions-fetch)"


def _greenhouse_slug_and_id(url: str) -> tuple[str | None, str | None]:
    """Parse boards.greenhouse.io/{slug}/jobs/{id} or job-boards{.eu}.greenhouse.io variants."""
    try:
        u = urlparse(url)
        # path: /{slug}/jobs/{id}
        m = re.match(r"/([^/]+)/jobs/(\d+)", u.path)
        if m:
            return m.group(1), m.group(2)
    except Exception:
        pass
    return None, None


def fetch_greenhouse_questions(url: str) -> dict | None:
    slug, job_id = _greenhouse_slug_and_id(url)
    if not slug or not job_id:
        return None
    api = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}?questions=true"
    r = requests.get(api, headers={"User-Agent": UA}, timeout=15)
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None


def fetch_for_url(url: str) -> dict | None:
    hit = ats.detect(url)
    if hit.name == "greenhouse":
        return fetch_greenhouse_questions(url)
    return None


@click.command()
@click.option("--folder", default=None, help="Single queue folder to fetch.")
@click.option("--force", is_flag=True, help="Re-fetch even if questions.json exists.")
def main(folder: str | None, force: bool) -> None:
    queue = config.ROOT / "queue"
    folders = [queue / folder] if folder else sorted(p for p in queue.iterdir() if p.is_dir())
    ok = miss = 0
    for f in folders:
        posting = f / "posting.md"
        out = f / "questions.json"
        if out.exists() and not force:
            ok += 1
            continue
        if not posting.exists():
            continue
        url = ""
        for line in posting.read_text().splitlines():
            if line.startswith("- URL:"):
                url = line.split(":", 1)[1].strip()
                break
        if not url:
            continue
        data = fetch_for_url(url)
        if data is None:
            miss += 1
            console.log(f"[yellow]miss[/yellow] {f.name} ({ats.detect(url).name})")
            continue
        out.write_text(json.dumps(data, indent=2))
        ok += 1
        n = len((data.get("questions") or []))
        console.log(f"[green]fetched[/green] {f.name} ({n} questions)")
    console.print(f"[bold]done[/bold] fetched:{ok} miss:{miss}")


if __name__ == "__main__":
    main()
