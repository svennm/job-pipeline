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

def _safe_fill(page, sel: str, val: str, log: list[str]) -> bool:
    """Type into a selector with React-friendly key events. Returns True on success.

    Uses .type() (per-keystroke) rather than .fill() because Greenhouse's React
    controlled inputs sometimes reject .fill() (the value attribute updates but
    React state doesn't, and React reverts on next render). .type() works because
    it dispatches real keydown/keyup events that React listens for.
    """
    if not val:
        return False
    try:
        el = page.query_selector(sel)
        if el is None:
            return False
        tag = (el.evaluate("e => e.tagName") or "").lower()
        if tag not in ("input", "textarea"):
            log.append(f"  skipped {sel} (tag={tag})")
            return False
        el.scroll_into_view_if_needed(timeout=2000)
        el.click()
        page.wait_for_timeout(50)
        # Clear any existing value first
        try:
            el.evaluate("e => { e.value = ''; e.dispatchEvent(new Event('input', {bubbles:true})); }")
        except Exception:
            pass
        el.type(val, delay=8)
        # Dispatch blur to commit React state
        try:
            el.evaluate("e => { e.dispatchEvent(new Event('change', {bubbles:true})); e.blur(); }")
        except Exception:
            pass
        log.append(f"  typed {sel}")
        return True
    except Exception as e:
        log.append(f"  err {sel}: {str(e)[:80]}")
        return False


def _safe_upload(page, sel: str, path: str, log: list[str]) -> bool:
    try:
        el = page.query_selector(sel)
        if el is None:
            return False
        el.set_input_files(path)
        # Dispatch change event manually — Greenhouse React component sometimes
        # needs this to update its visible drop-zone state.
        try:
            el.evaluate("e => e.dispatchEvent(new Event('change', {bubbles: true}))")
        except Exception:
            pass
        log.append(f"  uploaded {sel} <- {path}")
        return True
    except Exception as e:
        log.append(f"  upload err {sel}: {str(e)[:80]}")
        return False


def _selector_field(sel: str) -> str:
    """Pull the bare field id from an input selector like input#foo or input[id=foo]."""
    import re as _re
    m = _re.search(r"#([\w\-]+)", sel)
    if m:
        return m.group(1)
    m = _re.search(r"id=['\"]?([\w\-]+)", sel)
    if m:
        return m.group(1)
    return ""


def _click_react_select(page, field_name: str, answer: str, log: list[str]) -> bool:
    """Pick an option in a Greenhouse React-Select combobox by typing the answer.

    React-Select supports type-to-filter then Enter to confirm. We:
      1. Click the combobox input to focus it
      2. Type the answer (filters options)
      3. Press Enter (picks the filtered top option)
    This is more robust than searching the DOM for option items, which live in a
    portal outside the combobox subtree.
    """
    sel = f"input#{field_name}"
    try:
        el = page.query_selector(sel)
        if el is None:
            log.append(f"  no combobox {field_name}")
            return False
        el.scroll_into_view_if_needed(timeout=3000)
        el.click()
        page.wait_for_timeout(150)
        # Clear any prior text
        el.fill("")
        page.wait_for_timeout(50)
        # Type the answer slowly enough for React-Select to filter
        el.type(answer, delay=15)
        page.wait_for_timeout(300)
        # Pick the top filtered option
        page.keyboard.press("Enter")
        page.wait_for_timeout(150)
        log.append(f"  picked {field_name}: {answer[:60]}")
        return True
    except Exception as e:
        log.append(f"  combobox err {field_name}: {str(e)[:100]}")
        return False


def _apply_answers(page, entry: dict, log: list[str]) -> int:
    """Apply queue/{slug}/answers.json to React-Select fields. Returns count picked."""
    answers_path = entry["folder"] / "answers.json"
    if not answers_path.exists():
        log.append("  no answers.json")
        return 0
    try:
        import json as _json
        answers = _json.loads(answers_path.read_text())
    except Exception:
        return 0
    picked = 0
    for field_name, info in answers.items():
        ans = (info.get("answer") or "").strip()
        if not ans:
            continue
        ftype = info.get("type", "")
        if ftype == "multi_value_single_select":
            if _click_react_select(page, field_name, ans, log):
                picked += 1
        elif ftype in ("input_text", "textarea"):
            sel = f"input#{field_name}, textarea#{field_name}"
            if _safe_fill(page, sel, ans, log):
                picked += 1
    return picked


def _fill_greenhouse(page, entry: dict, resume) -> None:
    """Greenhouse hosted application form prefill."""
    log: list[str] = []
    page.goto(entry["url"], wait_until="domcontentloaded", timeout=60000)
    # Wait for the form to actually appear — Greenhouse posting pages render the
    # form below the JD. If first_name input isn't there after 20s, the page
    # uses a different layout (e.g. requires clicking "Apply" first).
    try:
        page.wait_for_selector("input#first_name", timeout=20000)
    except Exception:
        # Try clicking an "Apply" button to expand the form
        log.append("first_name not visible; looking for Apply button")
        for sel in [
            "a:has-text('Apply')",
            "button:has-text('Apply')",
            "a[href*='#app']",
            "a[href*='apply']",
        ]:
            try:
                btn = page.query_selector(sel)
                if btn:
                    btn.click()
                    log.append(f"  clicked {sel}")
                    page.wait_for_selector("input#first_name", timeout=15000)
                    break
            except Exception:
                continue

    page.wait_for_timeout(800)
    meta = resume["meta"]
    # Prefer explicit first_name/last_name; fall back to splitting `name`.
    first = meta.get("first_name") or ""
    last = meta.get("last_name") or ""
    if not first or not last:
        parts = (meta.get("name") or "").split(" ", 1)
        first = first or (parts[0] if parts else "")
        last = last or (parts[1] if len(parts) > 1 else "")

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
        _safe_fill(page, sel, val, log)

    # Phone country code — Greenhouse renders this as a separate React-Select
    # combobox right before the phone input. Common IDs/aria-labels include
    # variants like "phone_country", "country_code", "phone-country-code". Try
    # them all, falling back to a flag-search by country name.
    country_code = meta.get("phone_country_code", "")
    country_name = meta.get("phone_country", "")
    if country_code or country_name:
        cc_targets = [
            ("input#phone_country", country_name or country_code),
            ("input#phone_country_code", country_name or country_code),
            ("input[aria-labelledby*='phone_country']", country_name or country_code),
            ("input[id^='phone_country_']", country_name or country_code),
            # Wrapper aria-label often includes 'Country' near phone field
            ("input[role='combobox'][aria-label*='Country' i]", country_name or country_code),
        ]
        for sel, value in cc_targets:
            if _click_react_select(page, _selector_field(sel), value, log):
                break

    # Resume file upload — explicit IDs first, then catch-all
    for sel in ["input#resume", "input[type='file'][name*='resume']", "input[type='file']"]:
        if _safe_upload(page, sel, str(entry["resume_pdf"]), log):
            break

    cover_text = entry["cover_md"].read_text() if entry["cover_md"].exists() else ""
    # Cover letter — try file upload first, then textarea fallback
    uploaded_cover = False
    for sel in ["input#cover_letter", "input[type='file'][name*='cover']"]:
        if _safe_upload(page, sel, str(entry["cover_pdf"]), log):
            uploaded_cover = True
            break
    if not uploaded_cover:
        for sel in ["textarea#cover_letter-text", "textarea[name*='cover']"]:
            if _safe_fill(page, sel, cover_text, log):
                break

    # Custom Greenhouse questions: React-Select comboboxes + free-text fields.
    # Source of truth = queue/{slug}/answers.json, drafted by answer_drafter.py.
    picked = _apply_answers(page, entry, log)
    log.append(f"  custom questions picked: {picked}")

    console.print("[dim]" + "\n".join(log) + "[/dim]")


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
    first = meta.get("first_name") or (meta.get("name") or "").split(" ", 1)[0]
    last = meta.get("last_name") or ((meta.get("name") or "").split(" ", 1)[1] if " " in (meta.get("name") or "") else "")

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


def _fill_workday(page, entry: dict, resume) -> None:
    """Workday V1 — open posting, click Apply, stop for user to do account creation
    and wizard manually. Workday's multi-page wizard varies per employer and
    requires per-employer account; the wins are: no separate URL hunt, browser
    already navigated to the right place, resume/cover PDFs already prepared.

    TODO V2: handle the wizard pages (My Info / My Experience / Application
    Questions / Voluntary Disclosures / Review).
    """
    log: list[str] = []
    page.goto(entry["url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)

    # Workday's "Apply" button is usually a button with data-automation-id or
    # button text "Apply" / "Apply Now". Try several patterns.
    for sel in [
        "button[data-automation-id='applyManually']",
        "a[data-automation-id='adventureButton']",
        "button:has-text('Apply')",
        "a:has-text('Apply Now')",
        "a:has-text('Apply')",
    ]:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                log.append(f"  clicked {sel}")
                page.wait_for_timeout(2500)
                break
        except Exception as e:
            log.append(f"  err {sel}: {str(e)[:80]}")
    else:
        log.append("  no Apply button found")

    # After Apply, Workday often shows a "Sign In or Create Account" page.
    # The user needs to create an account (first time per employer) — we
    # cannot automate that securely. So we stop here and let them continue.
    log.append("  V1: stopping at sign-in / wizard for user")
    console.print("[dim]" + "\n".join(log) + "[/dim]")


HANDLERS = {
    "greenhouse": _fill_greenhouse,
    "lever": _fill_lever,
    "ashby": _fill_ashby,
    "workable": _fill_workable,
    "workday": _fill_workday,
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

    console.print(f"[yellow]opening[/yellow] {entry['url']}")

    with sync_playwright() as p:
        # Headed + larger viewport + slow_mo so user can see what's happening
        browser = p.chromium.launch(headless=False, slow_mo=200)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()
        try:
            handler(page, entry, resume)
            # Scroll to the resume upload area so user lands oriented to the form
            try:
                el = page.query_selector("input#resume, input[name*='resume']")
                if el:
                    el.scroll_into_view_if_needed(timeout=5000)
            except Exception:
                pass
            console.print(
                f"[bold green]READY[/bold green] {entry['company']} :: {hit.name}\n"
                f"  -> Review form in browser, FIX anything wrong, click SUBMIT, then close tab.\n"
                f"  -> Script waits up to {hold_seconds}s before auto-closing."
            )
            # Save screenshot for diagnostic
            try:
                shot = entry["folder"] / "form-state.png"
                page.screenshot(path=str(shot), full_page=False)
                console.print(f"  -> screenshot: {shot.relative_to(config.ROOT)}")
            except Exception as e:
                console.log(f"  screenshot failed: {e}")
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
