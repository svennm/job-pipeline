"""Submit applications via Playwright headed-mode prefill.

For each ready-to-send entry in queue/ (where resume.pdf + cover_letter.pdf exist):
  1. Detect ATS from posting URL
  2. If supported (Greenhouse/Lever/Ashby/Workable): open headed browser, fill
     fields, attach PDFs, scroll to submit button, leave browser open
  3. Wait for user to click submit (detect URL change to success page)
  4. Update tracker on success/failure

Routes:
  - greenhouse / lever / ashby / workable -> prefill
  - linkedin / indeed / glassdoor -> BANNED (skip, log)
  - workday / others / unknown -> manual_review/ note

DRY-RUN by default. Real submissions need --confirm.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from . import ats, config

console = Console()


def _queue_entries() -> list[Path]:
    queue = config.ROOT / "queue"
    if not queue.exists():
        return []
    return sorted(p for p in queue.iterdir() if p.is_dir())


def _load_entry(folder: Path) -> dict:
    posting_md = (folder / "posting.md").read_text() if (folder / "posting.md").exists() else ""
    score = json.loads((folder / "score.json").read_text()) if (folder / "score.json").exists() else {}
    url = ""
    location = ""
    company = ""
    category = ""
    title_line = ""
    lines = posting_md.splitlines()
    if lines:
        title_line = lines[0].lstrip("# ").strip()
        if " — " in title_line:
            title, company = title_line.split(" — ", 1)
        else:
            title, company = title_line, ""
    else:
        title = ""
    for line in lines:
        if line.startswith("- URL:"):
            url = line.split(":", 1)[1].strip()
        elif line.startswith("- Location:"):
            location = line.split(":", 1)[1].strip()
        elif line.startswith("- Category:"):
            category = line.split(":", 1)[1].strip()

    return {
        "folder": folder,
        "title": title,
        "company": company,
        "location": location,
        "category": category,
        "url": url,
        "score": score,
        "resume_pdf": folder / "resume.pdf",
        "cover_pdf": folder / "cover_letter.pdf",
        "cover_md": folder / "cover_letter.md",
    }


def _ready(entry: dict) -> bool:
    return entry["resume_pdf"].exists() and entry["cover_pdf"].exists() and bool(entry["url"])


# ============== ATS-specific handlers ==============

def _fill_greenhouse(page, entry: dict, resume) -> None:
    """Greenhouse hosted application form prefill."""
    page.goto(entry["url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1500)

    meta = resume["meta"]
    first, *rest = (meta.get("name") or "").split(" ", 1)
    last = rest[0] if rest else ""

    for sel, val in [
        ("input#first_name", first),
        ("input[name='job_application[first_name]']", first),
        ("input#last_name", last),
        ("input[name='job_application[last_name]']", last),
        ("input#email", meta.get("email", "")),
        ("input[name='job_application[email]']", meta.get("email", "")),
        ("input#phone", meta.get("phone", "")),
        ("input[name='job_application[phone]']", meta.get("phone", "")),
    ]:
        try:
            el = page.query_selector(sel)
            if el and val:
                el.fill(val)
        except Exception:
            pass

    # Resume file upload
    for sel in ["input[type='file'][name*='resume']", "input#resume", "input[type='file']"]:
        try:
            el = page.query_selector(sel)
            if el:
                el.set_input_files(str(entry["resume_pdf"]))
                break
        except Exception:
            continue

    # Cover letter — file OR textarea
    cover_text = entry["cover_md"].read_text() if entry["cover_md"].exists() else ""
    for sel in ["input[type='file'][name*='cover']", "input#cover_letter"]:
        try:
            el = page.query_selector(sel)
            if el:
                el.set_input_files(str(entry["cover_pdf"]))
                break
        except Exception:
            continue
    for sel in ["textarea[name*='cover']", "textarea#cover_letter_text"]:
        try:
            el = page.query_selector(sel)
            if el and cover_text:
                el.fill(cover_text)
                break
        except Exception:
            continue


def _fill_lever(page, entry: dict, resume) -> None:
    # Lever apply form lives at /apply suffix. Posting view URL won't have form.
    url = entry["url"]
    if not url.rstrip("/").endswith("/apply"):
        url = url.rstrip("/") + "/apply"
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)

    meta = resume["meta"]
    cover_text = entry["cover_md"].read_text() if entry["cover_md"].exists() else ""

    for sel, val in [
        ("input[name='name']", meta.get("name", "")),
        ("input[name='email']", meta.get("email", "")),
        ("input[name='phone']", meta.get("phone", "")),
        ("input[name='org']", "Independent / Self-directed"),
        ("input[name='location']", meta.get("location", "")),
        ("input[name='urls[LinkedIn]']", meta.get("linkedin") or ""),
        ("input[name='urls[GitHub]']", meta.get("github") or ""),
        ("input[name='urls[Portfolio]']", meta.get("github") or ""),
        ("textarea[name='comments']", cover_text),
    ]:
        try:
            el = page.query_selector(sel)
            if el and val:
                el.fill(val)
        except Exception:
            pass

    # Resume upload — Lever uses #resume-upload-input or name="resume"
    for sel in ["#resume-upload-input", "input[name='resume']", "input[type='file']"]:
        try:
            el = page.query_selector(sel)
            if el:
                el.set_input_files(str(entry["resume_pdf"]))
                break
        except Exception:
            continue
    # NOTE: Lever uses hCaptcha. User must solve it before submit.


def _fill_ashby(page, entry: dict, resume) -> None:
    """Ashby uses React + data-testid. Best-effort selectors."""
    page.goto(entry["url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)

    meta = resume["meta"]
    # Name (Ashby often uses a single full-name field)
    for sel in ["input[name='_systemfield_name']", "input[name='name']", "input[placeholder*='name' i]"]:
        try:
            el = page.query_selector(sel)
            if el:
                el.fill(meta.get("name", ""))
                break
        except Exception:
            continue
    for sel in ["input[name='_systemfield_email']", "input[type='email']"]:
        try:
            el = page.query_selector(sel)
            if el:
                el.fill(meta.get("email", ""))
                break
        except Exception:
            continue

    for sel in ["input[type='file']"]:
        try:
            el = page.query_selector(sel)
            if el:
                el.set_input_files(str(entry["resume_pdf"]))
                break
        except Exception:
            continue


def _fill_workable(page, entry: dict, resume) -> None:
    page.goto(entry["url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1500)

    meta = resume["meta"]
    first, *rest = (meta.get("name") or "").split(" ", 1)
    last = rest[0] if rest else ""

    for sel, val in [
        ("input[name='firstname']", first),
        ("input[name='lastname']", last),
        ("input[name='email']", meta.get("email", "")),
        ("input[name='phone']", meta.get("phone", "")),
        ("input[name='headline']", resume.get("headline", "")),
    ]:
        try:
            el = page.query_selector(sel)
            if el and val:
                el.fill(val)
        except Exception:
            pass

    for sel in ["input[type='file']"]:
        try:
            el = page.query_selector(sel)
            if el:
                el.set_input_files(str(entry["resume_pdf"]))
                break
        except Exception:
            continue


HANDLERS = {
    "greenhouse": _fill_greenhouse,
    "lever": _fill_lever,
    "ashby": _fill_ashby,
    "workable": _fill_workable,
}


def _prefill_one(entry: dict, resume, hold_seconds: int) -> str:
    """Open headed browser, fill form, wait for user to submit. Returns status."""
    from playwright.sync_api import sync_playwright

    hit = ats.detect(entry["url"])
    if hit.banned:
        return f"banned:{hit.name}"
    if not hit.supported:
        return f"manual:{hit.name}"

    handler = HANDLERS.get(hit.name)
    if not handler:
        return f"manual:no_handler({hit.name})"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        try:
            handler(page, entry, resume)
            console.print(
                f"[bold green]READY[/bold green] {entry['company']} :: {hit.name}\n"
                f"  -> Review form, click SUBMIT, then close browser.\n"
                f"  -> Browser will auto-close in {hold_seconds}s if not closed."
            )
            try:
                page.wait_for_event("close", timeout=hold_seconds * 1000)
            except Exception:
                pass
        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass

    return "submitted_or_skipped"  # we can't reliably distinguish without per-ATS success page


# ============== CLI ==============

@click.command()
@click.option("--confirm", is_flag=True, help="Actually open browsers + prefill. Default is dry-run.")
@click.option("--limit", default=None, type=int, help="Process only first N queue entries.")
@click.option("--folder", default=None, help="Process only this queue subfolder name.")
@click.option("--hold", default=300, type=int, help="Seconds to wait for user to submit in each browser.")
@click.option("--routes", default="prefill", type=click.Choice(["prefill", "all"]), help="Which routes to process.")
def main(confirm: bool, limit: int | None, folder: str | None, hold: int, routes: str) -> None:
    config.load_env()
    resume = config.load_resume()

    entries = _queue_entries()
    if folder:
        entries = [e for e in entries if e.name == folder]
    elif limit:
        entries = entries[:limit]

    if not entries:
        console.print("[yellow]queue/ is empty[/yellow]")
        return

    table = Table(title="Submission plan")
    table.add_column("folder")
    table.add_column("company")
    table.add_column("ats")
    table.add_column("route")
    table.add_column("ready")

    plan = []
    for f in entries:
        e = _load_entry(f)
        hit = ats.detect(e["url"])
        ready = _ready(e)
        plan.append((e, hit, ready))
        table.add_row(
            f.name,
            e["company"],
            hit.name,
            hit.route,
            "yes" if ready else "no",
        )
    console.print(table)

    actionable = [
        (e, h)
        for e, h, ready in plan
        if ready and not h.banned and (routes == "all" or h.route == "prefill")
    ]
    console.print(f"[bold]{len(actionable)}[/bold] actionable | {sum(1 for _,h,_ in plan if h.banned)} banned | {sum(1 for _,_,r in plan if not r)} not ready")

    if not confirm:
        console.print("[yellow]Dry-run.[/yellow] Re-run with --confirm to open browsers and prefill.")
        return

    results: list[tuple[str, str]] = []
    for e, h in actionable:
        if h.route != "prefill":
            results.append((e["folder"].name, f"skip:{h.route}"))
            continue
        try:
            status = _prefill_one(e, resume, hold)
        except Exception as exc:
            status = f"error:{type(exc).__name__}:{str(exc)[:120]}"
        console.log(f"{e['folder'].name}: [bold]{status}[/bold]")
        results.append((e["folder"].name, status))

    # Persist results into queue/_submit_log.json (append-only).
    log = config.ROOT / "queue" / "_submit_log.json"
    history = []
    if log.exists():
        try:
            history = json.loads(log.read_text())
        except Exception:
            history = []
    history.append({
        "ts": dt.datetime.utcnow().isoformat() + "Z",
        "results": results,
    })
    log.write_text(json.dumps(history, indent=2))

    console.print(f"[green]done[/green] — log {log.relative_to(config.ROOT)}")


if __name__ == "__main__":
    main()
