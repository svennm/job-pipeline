"""Render Markdown drafts to PDF for ATS submission.

For each queue/{slug}/ folder containing resume.md / cover_letter.md, emit
resume.pdf / cover_letter.pdf next to them. Uses WeasyPrint.

Idempotent — skips if PDF newer than source MD.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# WeasyPrint needs Homebrew pango/cairo on macOS Apple Silicon. Bake in the
# fallback path so users don't have to export DYLD_FALLBACK_LIBRARY_PATH
# manually. Harmless on Linux.
if sys.platform == "darwin":
    extra = "/opt/homebrew/lib"
    cur = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    if extra not in cur:
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = f"{extra}:{cur}".rstrip(":")

import click
import markdown
from rich.console import Console
from weasyprint import HTML, CSS

from . import config

console = Console()

CSS_BASE = """
@page { size: Letter; margin: 0.5in 0.6in 0.5in 0.6in; }
* { box-sizing: border-box; }
body {
  font-family: "Helvetica", "Arial", sans-serif;
  font-size: 10.5pt;
  line-height: 1.35;
  color: #111;
}
h1 { font-size: 18pt; margin: 0 0 2pt 0; letter-spacing: 0.5pt; }
h2 { font-size: 11pt; text-transform: uppercase; letter-spacing: 1pt; border-bottom: 1px solid #888; padding-bottom: 2pt; margin: 14pt 0 6pt 0; }
h3 { font-size: 11pt; margin: 8pt 0 2pt 0; }
p { margin: 4pt 0; }
ul { margin: 4pt 0 6pt 14pt; padding: 0; }
li { margin: 2pt 0; }
strong { font-weight: 600; }
em { font-style: italic; color: #555; }
hr { display: none; }
a { color: #1a4ec2; text-decoration: none; }
small { color: #555; }
"""


def md_to_pdf(md_path: Path, pdf_path: Path) -> None:
    text = md_path.read_text()
    html_body = markdown.markdown(text, extensions=["extra", "sane_lists"])
    html_doc = f"<!doctype html><html><head><meta charset='utf-8'></head><body>{html_body}</body></html>"
    HTML(string=html_doc).write_pdf(str(pdf_path), stylesheets=[CSS(string=CSS_BASE)])


@click.command()
@click.option("--force", is_flag=True, help="Re-render even if PDF newer than MD.")
@click.option("--folder", default=None, help="Render only this queue subfolder.")
def main(force: bool, folder: str | None) -> None:
    queue = config.ROOT / "queue"
    if not queue.exists():
        console.print("[yellow]queue/ is empty[/yellow]")
        return

    folders = [queue / folder] if folder else sorted(p for p in queue.iterdir() if p.is_dir())
    rendered = 0
    for f in folders:
        for stem in ("resume", "cover_letter"):
            md = f / f"{stem}.md"
            if not md.exists():
                continue
            pdf = f / f"{stem}.pdf"
            if pdf.exists() and not force and pdf.stat().st_mtime >= md.stat().st_mtime:
                continue
            md_to_pdf(md, pdf)
            console.log(f"[green]rendered[/green] {pdf.relative_to(config.ROOT)}")
            rendered += 1

    console.print(f"[bold]done[/bold] — {rendered} PDFs written")


if __name__ == "__main__":
    main()
