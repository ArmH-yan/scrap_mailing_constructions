"""
Lead Generation v2 — Email Sender
Gmail SMTP email sending with HTML templates.
"""

import os
import time
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone

log = logging.getLogger("leadgen")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", "")
SEND_DELAY_SECONDS = float(os.getenv("SEND_DELAY_SECONDS", "20"))
DAILY_SEND_LIMIT = int(os.getenv("DAILY_SEND_LIMIT", "400"))


def get_smtp_connection():
    """Create and return an SMTP connection."""
    if not SMTP_USER or not SMTP_PASS:
        log.error("  SMTP credentials not configured. Set SMTP_USER and SMTP_PASS in .env")
        return None

    try:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        return server
    except Exception as e:
        log.error(f"  SMTP connection failed: {e}")
        return None


def _build_message(to_email: str, subject: str, html_body: str) -> str:
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return msg.as_string()


class EmailSender:
    """One SMTP connection for many sends, with a delay between messages.

    Usage:
        with EmailSender() as sender:
            sender.send(to, subject, html)
    """

    def __init__(self, delay: float = SEND_DELAY_SECONDS):
        self.delay = delay
        self.server = None
        self._last_send = 0.0

    def __enter__(self):
        self.server = get_smtp_connection()
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self.server:
            try:
                self.server.quit()
            except Exception:
                pass
            self.server = None

    def send(self, to_email: str, subject: str, html_body: str) -> bool:
        if not to_email:
            log.warning("  No recipient email provided")
            return False

        wait = self.delay - (time.monotonic() - self._last_send)
        if wait > 0:
            time.sleep(wait)

        message = _build_message(to_email, subject, html_body)
        for attempt in range(2):
            if self.server is None:
                self.server = get_smtp_connection()
                if self.server is None:
                    return False
            try:
                self.server.sendmail(SMTP_FROM_EMAIL, to_email, message)
                self._last_send = time.monotonic()
                log.info(f"  [OK] Email sent to {to_email}")
                return True
            except smtplib.SMTPServerDisconnected:
                # Connection dropped — reconnect once and retry
                self.server = None
            except Exception as e:
                self._last_send = time.monotonic()
                log.error(f"  [FAIL] Failed to send to {to_email}: {e}")
                return False

        log.error(f"  [FAIL] Failed to send to {to_email}: connection lost")
        return False


def send_email(to_email: str, subject: str, html_body: str, to_name: str = None) -> bool:
    """Send a single HTML email (opens and closes its own connection)."""
    with EmailSender(delay=0) as sender:
        return sender.send(to_email, subject, html_body)
