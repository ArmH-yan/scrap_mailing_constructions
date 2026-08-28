"""
Lead Generation v2 — Email Sender
Gmail SMTP email sending with HTML templates.
"""

import os
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


def send_email(to_email: str, subject: str, html_body: str, to_name: str = None) -> bool:
    """Send a single HTML email."""
    if not to_email:
        log.warning("  No recipient email provided")
        return False

    server = get_smtp_connection()
    if not server:
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
        msg["To"] = to_email
        msg["Subject"] = subject

        # Add HTML body
        html_part = MIMEText(html_body, "html", "utf-8")
        msg.attach(html_part)

        # Send
        server.sendmail(SMTP_FROM_EMAIL, to_email, msg.as_string())
        log.info(f"  [OK] Email sent to {to_email}")
        return True

    except Exception as e:
        log.error(f"  [FAIL] Failed to send to {to_email}: {e}")
        return False

    finally:
        try:
            server.quit()
        except:
            pass


def send_batch_email(recipients: list[dict], subject: str, html_body: str) -> dict:
    """Send the same email to multiple recipients. Returns stats."""
    stats = {"sent": 0, "failed": 0, "skipped": 0}

    server = get_smtp_connection()
    if not server:
        stats["skipped"] = len(recipients)
        return stats

    for recipient in recipients:
        to_email = recipient.get("email")
        if not to_email:
            stats["skipped"] += 1
            continue

        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
            msg["To"] = to_email
            msg["Subject"] = subject

            html_part = MIMEText(html_body, "html", "utf-8")
            msg.attach(html_part)

            server.sendmail(SMTP_FROM_EMAIL, to_email, msg.as_string())
            stats["sent"] += 1
            log.info(f"  [OK] Email sent to {to_email}")

        except Exception as e:
            stats["failed"] += 1
            log.error(f"  [FAIL] Failed to send to {to_email}: {e}")

    try:
        server.quit()
    except:
        pass

    return stats
