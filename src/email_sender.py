"""
Lead Generation v2 — Email Sender
SMTP email sending (Zoho Mail / Gmail) with HTML templates and tracked links.
"""

import os
import time
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import make_msgid
from urllib.parse import urlencode

log = logging.getLogger("leadgen")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.zoho.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", "")
SEND_DELAY_SECONDS = float(os.getenv("SEND_DELAY_SECONDS", "20"))
DAILY_SEND_LIMIT = int(os.getenv("DAILY_SEND_LIMIT", "400"))

# Tracking: TRACK_BASE_URL is where tracker/app.py runs (e.g. https://go.thenest.am).
# Leave it empty until the site is live — the template then hides the website button.
TRACK_BASE_URL = os.getenv("TRACK_BASE_URL", "").rstrip("/")
EMAIL_CAMPAIGN = os.getenv("EMAIL_CAMPAIGN", "outreach")


def tracking_links(token: str) -> dict:
    """Per-company URLs for the template: website_url, unsubscribe_url."""
    if not TRACK_BASE_URL or not token:
        return {"website_url": "", "unsubscribe_url": ""}
    return {
        "website_url": f"{TRACK_BASE_URL}/r/{token}",
        "unsubscribe_url": f"{TRACK_BASE_URL}/u/{token}",
    }


def utm_url(base_url: str, token: str = "") -> str:
    """Final website URL the tracker redirects to, tagged so analytics see source=email."""
    params = {"utm_source": "email", "utm_medium": "email", "utm_campaign": EMAIL_CAMPAIGN}
    if token:
        params["c"] = token
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}{urlencode(params)}"


def get_smtp_connection():
    """Create and return an SMTP connection."""
    if not SMTP_USER or not SMTP_PASS:
        log.error("  SMTP credentials not configured. Set SMTP_USER and SMTP_PASS in .env")
        return None

    try:
        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
            server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        return server
    except Exception as e:
        log.error(f"  SMTP connection failed: {e}")
        return None


def _build_message(to_email: str, subject: str, html_body: str,
                   message_id: str, unsubscribe_url: str = "") -> str:
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    if unsubscribe_url:
        # Lets Gmail/Outlook show their own "Unsubscribe" button (one-click, RFC 8058)
        msg["List-Unsubscribe"] = f"<{unsubscribe_url}>, <mailto:{SMTP_FROM_EMAIL}?subject=unsubscribe>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
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

    def send(self, to_email: str, subject: str, html_body: str,
             unsubscribe_url: str = "") -> str | None:
        """Send one email. Returns its Message-ID (used to match replies), or None."""
        if not to_email:
            log.warning("  No recipient email provided")
            return None

        wait = self.delay - (time.monotonic() - self._last_send)
        if wait > 0:
            time.sleep(wait)

        domain = SMTP_FROM_EMAIL.split("@")[-1] or None
        message_id = make_msgid(domain=domain)
        message = _build_message(to_email, subject, html_body, message_id, unsubscribe_url)
        for attempt in range(2):
            if self.server is None:
                self.server = get_smtp_connection()
                if self.server is None:
                    return None
            try:
                self.server.sendmail(SMTP_FROM_EMAIL, to_email, message)
                self._last_send = time.monotonic()
                log.info(f"  [OK] Email sent to {to_email}")
                return message_id
            except smtplib.SMTPServerDisconnected:
                # Connection dropped — reconnect once and retry
                self.server = None
            except Exception as e:
                self._last_send = time.monotonic()
                log.error(f"  [FAIL] Failed to send to {to_email}: {e}")
                return None

        log.error(f"  [FAIL] Failed to send to {to_email}: connection lost")
        return None


def send_email(to_email: str, subject: str, html_body: str, to_name: str = None) -> bool:
    """Send a single HTML email (opens and closes its own connection)."""
    with EmailSender(delay=0) as sender:
        return sender.send(to_email, subject, html_body) is not None
