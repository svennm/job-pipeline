"""Per-posting question-answer drafting.

For each queue/{slug}/questions.json (fetched by question_fetcher), produces
queue/{slug}/answers.json with one entry per question:

    {
      "question_8398934101": {
        "field_name": "question_8398934101",
        "label": "What is your professional experience with options market making?",
        "type": "multi_value_single_select",
        "answer": "I have direct experience trading or researching options strategies, but not in a market-making capacity.",
        "rationale": "candidate runs FX/crypto systematic trading, not options MM, but has researched options"
      },
      ...
    }

For dropdowns, answer is the EXACT option label (must match for Playwright clicker).
For text/textarea, answer is the draft text.

Defaults (no LLM needed):
- compensation questions -> "Prefer not to disclose"
- notice / non-compete -> "Immediate"
- GDPR / privacy consent -> "Confirmed" or first option
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
import yaml
from rich.console import Console

from . import config, llm

console = Console()


# Heuristic defaults: regex on question label -> exact option label to pick
HEURISTIC_PICKS = [
    (r"(?i)current.*(?:base.*salary|comp)|prior.*comp|salary range|expected.*salary",
     ["Prefer not to disclose"]),
    (r"(?i)notice.*period|non[- ]compete",
     ["Immediate"]),
    (r"(?i)(privacy notice|gdpr|data protection|policy|terms|conditions|consent|confirm).*",
     ["Confirmed", "Yes", "I agree", "Agree", "I confirm", "Accept"]),
]


def _heuristic(question_label: str, options: list[str]) -> str | None:
    """Try regex-based default picks. Returns matching option label or None."""
    import re
    for pat, candidates in HEURISTIC_PICKS:
        if re.search(pat, question_label):
            # Match any candidate against options (case-insensitive contains)
            for cand in candidates:
                for opt in options:
                    if cand.lower() == opt.lower().strip():
                        return opt
            for cand in candidates:
                for opt in options:
                    if cand.lower() in opt.lower():
                        return opt
            # Last resort: pick first option (often for single-option GDPR confirms)
            if len(options) == 1:
                return options[0]
    return None


BULK_PROMPT = """You are drafting application answers for one specific job posting.

CANDIDATE RESUME (YAML):
{resume_yaml}

JOB POSTING:
Title: {title}
Company: {company}
Location: {location}
JD excerpt:
{description}

QUESTIONS TO ANSWER:
{questions_block}

Rules:
1. Be TRUTHFUL — use only facts from the candidate's resume. Never invent experience.
2. For multi_value_single_select questions: your answer MUST be one of the listed options, EXACTLY as written (copy verbatim).
3. For text / textarea questions: 1-3 sentences max. Numbers and artifacts, no fluff.
4. The candidate's location is Togo (West Africa). They would need visa sponsorship for most countries. Pick the option that matches this honestly even if it weakens the application.
5. For experience-bucket questions, pick the option that best fits the candidate's REAL experience — overstating is worse than honest.

Return ONLY valid JSON, one entry per question, keyed by the question's "field" name:
{{
  "<field_name>": "<exact answer text>",
  ...
}}
No markdown, no preamble, no fenced code blocks."""


def _build_bulk_prompt(resume: dict, posting_md: str, questions: list[dict]) -> str:
    # Posting metadata from posting.md
    title = company = location = ""
    desc = []
    in_desc = False
    for line in posting_md.splitlines():
        if line.startswith("# "):
            head = line[2:].strip()
            if " — " in head:
                title, company = head.split(" — ", 1)
            else:
                title = head
        elif line.startswith("- Location:"):
            location = line.split(":", 1)[1].strip()
        elif line.startswith("## Description"):
            in_desc = True
        elif in_desc:
            desc.append(line)
    description = "\n".join(desc)[:3500]

    # Build questions block
    qblock_parts = []
    for q in questions:
        f = q["fields"][0]
        name = f["name"]
        ftype = f["type"]
        label = q["label"]
        options = [v["label"] for v in f.get("values") or []]
        # Skip basic fields (handled separately)
        if name in {"first_name", "last_name", "email", "phone", "resume", "resume_text", "cover_letter", "cover_letter_text"}:
            continue
        opt_block = ""
        if options:
            opt_block = "\n        Options (pick exactly one):\n" + "\n".join(f"          - {o}" for o in options)
        qblock_parts.append(f"- field: {name}\n  type: {ftype}\n  label: {label}{opt_block}")

    questions_block = "\n".join(qblock_parts) if qblock_parts else "(no custom questions)"
    return BULK_PROMPT.format(
        resume_yaml=yaml.safe_dump(resume, sort_keys=False),
        title=title,
        company=company,
        location=location,
        description=description,
        questions_block=questions_block,
    )


def _parse_response(text: str) -> dict[str, str]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:]).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        s, e = text.find("{"), text.rfind("}")
        if s >= 0 and e > s:
            try:
                return json.loads(text[s : e + 1])
            except json.JSONDecodeError:
                pass
        return {}


def draft_one(folder: Path, resume: dict) -> dict[str, dict[str, Any]]:
    """Build the answers map for one queue folder. Returns dict by field name."""
    q_path = folder / "questions.json"
    p_path = folder / "posting.md"
    if not q_path.exists() or not p_path.exists():
        return {}
    schema = json.loads(q_path.read_text())
    questions = schema.get("questions") or []
    if not questions:
        return {}

    # Pass 1: heuristic-pick what we can without LLM
    answers: dict[str, dict[str, Any]] = {}
    needs_llm: list[dict] = []
    for q in questions:
        fields = q.get("fields") or []
        if not fields:
            continue
        f = fields[0]
        name = f["name"]
        if name in {"first_name", "last_name", "email", "phone", "resume",
                    "resume_text", "cover_letter", "cover_letter_text"}:
            continue
        label = q["label"]
        ftype = f["type"]
        options = [v["label"] for v in f.get("values") or []]
        h = _heuristic(label, options) if options else None
        if h is not None:
            answers[name] = {
                "field_name": name,
                "label": label,
                "type": ftype,
                "answer": h,
                "rationale": "heuristic_default",
            }
        else:
            needs_llm.append(q)

    # Pass 2: LLM for everything else (bulk call)
    if needs_llm:
        posting_md = p_path.read_text()
        prompt = _build_bulk_prompt(resume, posting_md, needs_llm)
        text = llm.call(prompt)
        parsed = _parse_response(text)
        for q in needs_llm:
            f = q["fields"][0]
            name = f["name"]
            raw = parsed.get(name) or ""
            # LLM sometimes returns lists (multi-pick) or dicts. Coerce to str.
            if isinstance(raw, list):
                raw = raw[0] if raw else ""
            if not isinstance(raw, str):
                raw = str(raw)
            answers[name] = {
                "field_name": name,
                "label": q["label"],
                "type": f["type"],
                "answer": raw.strip(),
                "rationale": "llm_drafted",
            }

    return answers


@click.command()
@click.option("--folder", default=None, help="Single queue folder.")
@click.option("--force", is_flag=True, help="Re-draft even if answers.json exists.")
def main(folder: str | None, force: bool) -> None:
    config.load_env()
    resume = config.load_resume()
    queue = config.ROOT / "queue"
    folders = [queue / folder] if folder else sorted(p for p in queue.iterdir() if p.is_dir())

    for f in folders:
        out = f / "answers.json"
        if out.exists() and not force:
            console.log(f"[dim]exists[/dim] {f.name}")
            continue
        if not (f / "questions.json").exists():
            console.log(f"[yellow]no questions.json[/yellow] {f.name}")
            continue
        console.log(f"drafting {f.name}")
        try:
            answers = draft_one(f, resume)
        except Exception as e:
            console.log(f"[red]error[/red] {f.name}: {e}")
            continue
        out.write_text(json.dumps(answers, indent=2))
        console.log(f"[green]wrote[/green] {f.name} ({len(answers)} answers)")


if __name__ == "__main__":
    main()
