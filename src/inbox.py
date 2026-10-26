"""
Lead Generation — Inbox checker
Reads the Zoho Mail inbox over IMAP and records replies, bounces and
"unsubscribe" replies against the companies we emailed.

Messages are fetched with BODY.PEEK so nothing is marked as read.
Zoho: enable IMAP in Mail Settings → Mail Accounts → IMAP Access; with 2FA,
use an app-specific password.
"""

import os
import re
import imaplib
import logging
from email import message_from_bytes
from email.header import decode_header, make_header
from email.utils import parseaddr
from datetime import datetime, timedelta

from src.database import (
    find_company_for_reply,
    find_sent_company_by_email,
    event_exists,
    record_email_event,
)

log = logging.getLogger("leadgen")

IMAP_HOST = os.getenv("IMAP_HOST", "imap.zoho.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER = os.getenv("IMAP_USER") or os.getenv("SMTP_USER", "")
IMAP_PASS = os.getenv("IMAP_PASS") or os.getenv("SMTP_PASS", "")
IMAP_FOLDER = os.getenv("IMAP_FOLDER", "INBOX")
IMAP_LOOKBACK_DAYS = int(os.getenv("IMAP_LOOKBACK_DAYS", "30"))

BOUNCE_SENDERS = ("mailer-daemon", "postmaster", "mail delivery")
BOUNCE_SUBJECTS = ("undeliver", "delivery status notification", "delivery failure",
                   "failure notice", "returned mail", "not delivered")
UNSUBSCRIBE_WORDS = ("unsubscribe", "stop", "remove me", "отписаться", "չուղարկել")
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,12}")
MSGID_RE = re.compile(r"<[^>]+>")


def _header(msg, name: str) -> str:
    value = msg.get(name, "")
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _text_body(msg) -> str:
    """Plain text of the message (falls back to HTML / attached bounce reports)."""
    parts = []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_type() in ("text/plain", "text/html", "message/delivery-status"):
            payload = part.get_payload(decode=True)
            if payload:
                parts.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
    return "\n".join(parts)


def _first_line(body: str) -> str:
    for line in body.splitlines():
        line = line.strip()
        if line and not line.startswith(">"):
            return line.lower()
    return ""


def _is_bounce(from_addr: str, subject: str) -> bool:
    return (any(s in from_addr.lower() for s in BOUNCE_SENDERS)
            or any(s in subject.lower() for s in BOUNCE_SUBJECTS))


def _handle(engine, msg, stats: dict):
    incoming_id = msg.get("Message-ID", "").strip()
    from_name, from_addr = parseaddr(_header(msg, "From"))
    subject = _header(msg, "Subject")
    body = _text_body(msg)

    if _is_bounce(f"{from_name} {from_addr}", subject):
        failed = [msg.get("X-Failed-Recipients", "")] + EMAIL_RE.findall(body)
        company = find_sent_company_by_email(engine, [e.strip() for e in failed if e.strip()])
        if company and not event_exists(engine, company["id"], "bounce", incoming_id):
            record_email_event(engine, company["id"], "bounce", incoming_id)
            stats["bounces"] += 1
            log.info(f"  [BOUNCE] {company['company_name']} <{company['email']}>")
        return

    refs = MSGID_RE.findall(f"{msg.get('In-Reply-To', '')} {msg.get('References', '')}")
    company = find_company_for_reply(engine, refs, from_addr)
    if not company or event_exists(engine, company["id"], "reply", incoming_id):
        return

    record_email_event(engine, company["id"], "reply", incoming_id)
    stats["replies"] += 1
    log.info(f"  [REPLY] {company['company_name']} <{from_addr}>: {subject}")

    first = _first_line(body)
    wants_out = subject.lower().strip() == "unsubscribe" or any(
        re.match(rf"{re.escape(w)}\b", first) for w in UNSUBSCRIBE_WORDS)
    if wants_out and company.get("email_status") != "unsubscribed":
        record_email_event(engine, company["id"], "unsubscribe", "reply")
        stats["unsubscribes"] += 1
        log.info(f"  [UNSUBSCRIBE] {company['company_name']}")


def check_inbox(engine) -> dict:
    """Scan recent inbox mail. Safe to run repeatedly — already-seen mail is skipped."""
    stats = {"scanned": 0, "replies": 0, "bounces": 0, "unsubscribes": 0}
    if not IMAP_USER or not IMAP_PASS:
        log.error("  IMAP credentials not configured. Set SMTP_USER/SMTP_PASS (or IMAP_USER/IMAP_PASS) in .env")
        return stats

    since = (datetime.now() - timedelta(days=IMAP_LOOKBACK_DAYS)).strftime("%d-%b-%Y")
    with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as imap:
        imap.login(IMAP_USER, IMAP_PASS)
        imap.select(IMAP_FOLDER, readonly=True)
        _, data = imap.search(None, "SINCE", since)
        ids = data[0].split()
        log.info(f"  {len(ids)} messages in {IMAP_FOLDER} since {since}")

        for num in ids:
            _, msg_data = imap.fetch(num, "(BODY.PEEK[])")
            if not msg_data or not isinstance(msg_data[0], tuple):
                continue
            stats["scanned"] += 1
            try:
                _handle(engine, message_from_bytes(msg_data[0][1]), stats)
            except Exception as e:
                log.warning(f"  Skipped message {num.decode()}: {e}")

    return stats
