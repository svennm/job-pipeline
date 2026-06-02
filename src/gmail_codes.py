"""Read recent verification codes from Gmail via IMAP.

Some ATS apply forms send a 6-digit code to the candidate's email to verify
ownership before letting them submit. Fetch the most recent unread email
from common sender domains, extract the code, return it.

Setup (one-time):
  1. Enable 2-Step Verification on your Google account.
  2. Go to https://myaccount.google.com/apppasswords
  3. Generate an App Password (name: "job-pipeline").
  4. Add to .env:
        GMAIL_USER=svennmivedor@gmail.com
        GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx     (16-char, spaces optional)

This module never stores credentials in source.
"""
from __future__ import annotations

import email
import imaplib
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.header import decode_header

from . import config

CODE_PATTERNS = [
    re.compile(r"\b(\d{6})\b"),                            # 123456
    re.compile(r"\b(\d{4})[- ](\d{2})\b"),                 # 1234-56 / 1234 56
    re.compile(r"\bcode[:\s]+([A-Z0-9]{4,8})\b", re.I),     # "code: ABC123"
    re.compile(r"\bverification code[:\s]+([A-Z0-9]{4,8})\b", re.I),
    re.compile(r"\bone[- ]time (?:passcode|code|pin)[:\s]+([A-Z0-9]{4,8})\b", re.I),
]

ATS_SENDERS = [
    "@greenhouse.io",
    "@lever.co",
    "@ashbyhq.com",
    "@workable.com",
    "@workday.com",
    "@smartrecruiters.com",
    "noreply",
]


@dataclass
class Code:
    code: str
    sender: str
    subject: str
    received: datetime


def _decode_header(h: str | None) -> str:
    if not h:
        return ""
    parts = decode_header(h)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            try:
                out.append(text.decode(enc or "utf-8", errors="ignore"))
            except Exception:
                out.append(text.decode("utf-8", errors="ignore"))
        else:
            out.append(text)
    return "".join(out)


def _extract_code(body: str) -> str | None:
    """First matching code pattern wins. Returns None if no code found."""
    for pat in CODE_PATTERNS:
        m = pat.search(body)
        if not m:
            continue
        groups = [g for g in m.groups() if g]
        if not groups:
            continue
        # If two groups (e.g. 1234-56), concatenate
        code = "".join(groups).strip()
        if 4 <= len(code) <= 10:
            return code
    return None


def _connect() -> imaplib.IMAP4_SSL:
    user = config.require_env("GMAIL_USER")
    pwd = config.require_env("GMAIL_APP_PASSWORD").replace(" ", "")
    m = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    m.login(user, pwd)
    m.select("INBOX")
    return m


def _body_text(msg) -> str:
    """Extract text/plain body, falling back to text/html stripped of tags."""
    parts = []
    if msg.is_multipart():
        for p in msg.walk():
            ct = p.get_content_type()
            if ct == "text/plain":
                try:
                    parts.append(p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8", errors="ignore"))
                except Exception:
                    continue
        if not parts:
            for p in msg.walk():
                if p.get_content_type() == "text/html":
                    try:
                        html = p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8", errors="ignore")
                        parts.append(re.sub(r"<[^>]+>", " ", html))
                    except Exception:
                        continue
    else:
        try:
            parts.append(msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", errors="ignore"))
        except Exception:
            pass
    return "\n".join(parts)


def latest_code(*, max_age_minutes: int = 10, sender_hint: str | None = None) -> Code | None:
    """Search the last `max_age_minutes` of inbox for a verification code.

    `sender_hint` lets the caller scope to a specific ATS (e.g. "greenhouse.io").
    """
    config.load_env()
    m = _connect()
    try:
        since = (datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)).strftime("%d-%b-%Y")
        typ, data = m.search(None, f'SINCE {since}')
        if typ != "OK" or not data or not data[0]:
            return None
        ids = data[0].split()
        # Walk from newest to oldest
        for mid in reversed(ids):
            typ, msg_data = m.fetch(mid, "(RFC822)")
            if typ != "OK" or not msg_data:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            sender = _decode_header(msg.get("From"))
            subject = _decode_header(msg.get("Subject"))
            # Sender filter
            if sender_hint:
                if sender_hint.lower() not in sender.lower():
                    continue
            else:
                if not any(s.lower() in sender.lower() for s in ATS_SENDERS):
                    continue
            body = _body_text(msg)
            code = _extract_code(subject + "\n" + body)
            if not code:
                continue
            date_hdr = msg.get("Date")
            try:
                received = email.utils.parsedate_to_datetime(date_hdr)
            except Exception:
                received = datetime.now(timezone.utc)
            return Code(code=code, sender=sender, subject=subject, received=received)
        return None
    finally:
        try:
            m.close()
            m.logout()
        except Exception:
            pass


def wait_for_code(*, timeout_seconds: int = 60, poll_seconds: int = 5, sender_hint: str | None = None) -> Code | None:
    """Block until a fresh code arrives or timeout. Polls Gmail every N seconds."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        code = latest_code(max_age_minutes=2, sender_hint=sender_hint)
        if code:
            return code
        time.sleep(poll_seconds)
    return None


if __name__ == "__main__":
    import click

    @click.command()
    @click.option("--wait", default=60, help="Seconds to wait for fresh code.")
    @click.option("--sender", default=None, help="Scope to a sender domain.")
    def main(wait: int, sender: str | None) -> None:
        c = wait_for_code(timeout_seconds=wait, sender_hint=sender)
        if not c:
            print("no code found")
            return
        print(f"code:    {c.code}")
        print(f"sender:  {c.sender}")
        print(f"subject: {c.subject}")
        print(f"when:    {c.received}")

    main()
