"""ATS (Applicant Tracking System) detection from a job URL.

We classify each posting's apply URL so the submitter knows which strategy
to use. Order matters — first match wins.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

# (name, host-regex)
ATS_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("greenhouse", re.compile(r"(?:^|\.)greenhouse\.io$|^boards\.greenhouse\.io$|^job-boards\.greenhouse\.io$")),
    ("lever",      re.compile(r"(?:^|\.)lever\.co$|^jobs\.lever\.co$")),
    ("ashby",      re.compile(r"^jobs\.ashbyhq\.com$|^[\w-]+\.ashbyhq\.com$")),
    ("workable",   re.compile(r"(?:^|\.)workable\.com$|^apply\.workable\.com$")),
    ("workday",    re.compile(r"^[\w.-]+\.myworkdayjobs\.com$|^[\w.-]+\.wd\d+\.myworkdayjobs\.com$")),
    ("smartrecruiters", re.compile(r"^jobs\.smartrecruiters\.com$|^careers\.smartrecruiters\.com$")),
    ("bamboohr",   re.compile(r"^[\w-]+\.bamboohr\.com$")),
    ("teamtailor", re.compile(r"(?:^|\.)teamtailor\.com$")),
    ("recruitee",  re.compile(r"(?:^|\.)recruitee\.com$")),
    ("personio",   re.compile(r"^[\w-]+\.jobs\.personio\.(?:de|com)$|^[\w-]+\.personio\.(?:de|com)$")),
    ("rippling",   re.compile(r"^ats\.rippling\.com$")),
    ("paylocity",  re.compile(r"(?:^|\.)paylocity\.com$")),
    ("icims",      re.compile(r"(?:^|\.)icims\.com$")),
    ("breezy",     re.compile(r"(?:^|\.)breezy\.hr$")),
    ("jobvite",    re.compile(r"(?:^|\.)jobvite\.com$")),
    ("linkedin",   re.compile(r"^(?:www\.)?linkedin\.com$")),
    ("indeed",     re.compile(r"^(?:www\.)?indeed\.com$|^[a-z]+\.indeed\.com$")),
    ("glassdoor",  re.compile(r"^(?:www\.)?glassdoor\.com$|^[a-z]+\.glassdoor\.com$")),
]

# ATSes we can prefill with Playwright. Others -> manual_review.
SUPPORTED = {"greenhouse", "lever", "ashby", "workable"}

# ATSes that are ToS-risky for any automation (LinkedIn/Indeed/Glassdoor Easy Apply).
BANNED = {"linkedin", "indeed", "glassdoor"}


@dataclass
class AtsHit:
    name: str            # "greenhouse" / "lever" / ... / "unknown"
    supported: bool      # has a Playwright prefill module
    banned: bool         # auto-submit prohibited by our policy
    url: str             # the URL we classified
    host: str            # parsed host

    @property
    def route(self) -> str:
        if self.banned:
            return "banned"
        if self.supported:
            return "prefill"
        return "manual"


def detect(url: str | None) -> AtsHit:
    if not url:
        return AtsHit(name="unknown", supported=False, banned=False, url="", host="")
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        host = ""
    host = host.lower()
    for name, pat in ATS_PATTERNS:
        if pat.search(host):
            return AtsHit(
                name=name,
                supported=name in SUPPORTED,
                banned=name in BANNED,
                url=url,
                host=host,
            )
    return AtsHit(name="unknown", supported=False, banned=False, url=url, host=host)


def classify_batch(urls: list[str]) -> list[AtsHit]:
    return [detect(u) for u in urls]
