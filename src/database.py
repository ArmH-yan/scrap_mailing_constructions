"""
Lead Generation — Database Module
PostgreSQL storage for companies and contacts
"""

import os
import re
import hashlib
import logging
import pandas as pd
import psycopg2
from sqlalchemy import create_engine, text
from datetime import datetime, timezone

log = logging.getLogger("leadgen")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "leadgen")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "postgres")

SQL_DIR = "sql/schema"


# Legal-form tokens stripped before comparing names (Armenian, Russian, English)
LEGAL_FORMS = {
    "սպը", "փբը", "բբը", "աձ", "ооо", "зао", "оао", "пао", "ип",
    "llc", "ltd", "cjsc", "ojsc", "jsc", "inc", "co", "company",
}


def normalize_name(name: str) -> str:
    name = (name or "").lower()
    name = re.sub(r"[^\w\s]", " ", name)
    tokens = [t for t in name.split() if t not in LEGAL_FORMS]
    return " ".join(tokens)


def phone_digits(phone: str) -> str:
    """Last 8 digits — ignores +374 / 0 prefixes and formatting."""
    digits = re.sub(r"\D", "", phone or "")
    return digits[-8:] if len(digits) >= 8 else ""


def compute_content_hash(name: str) -> str:
    """Source-independent identity: the same company on two sites hashes the same."""
    return hashlib.sha1(normalize_name(name).encode("utf-8")).hexdigest()


def get_engine():
    url = f"postgresql+psycopg2://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    return create_engine(url, echo=False, pool_pre_ping=True)


def create_database_if_needed():
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS, dbname="postgres"
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,))
    if not cur.fetchone():
        cur.execute(f'CREATE DATABASE "{DB_NAME}"')
        log.info(f"  [OK] Created database '{DB_NAME}'")
    else:
        log.info(f"  [OK] Database '{DB_NAME}' already exists")
    cur.close()
    conn.close()


def run_sql_file(engine, filepath: str):
    with open(filepath, encoding="utf-8") as f:
        sql = f.read()
    with engine.connect() as conn:
        conn.execute(text(sql))
        conn.commit()
    log.info(f"  [OK] Executed {filepath}")


def init_schema():
    create_database_if_needed()
    engine = get_engine()
    run_sql_file(engine, f"{SQL_DIR}/01_schema.sql")
    rehash_companies(engine)
    log.info("  [OK] Schema initialized")


def rehash_companies(engine):
    """Move rows saved with the old name|phone|url hash to the name-only hash."""
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, company_name, content_hash FROM companies")).fetchall()
        taken = {r[2] for r in rows}
        for company_id, name, old_hash in rows:
            new_hash = compute_content_hash(name)
            if new_hash != old_hash and new_hash not in taken:
                conn.execute(
                    text("UPDATE companies SET content_hash = :h WHERE id = :id"),
                    {"h": new_hash, "id": company_id},
                )
                taken.add(new_hash)
        conn.commit()


def upsert_company(engine, data: dict) -> int:
    """Insert or update company by content_hash. Returns company ID."""
    content_hash = compute_content_hash(data.get("company_name", ""))

    with engine.connect() as conn:
        existing = conn.execute(
            text("SELECT id FROM companies WHERE content_hash = :hash"),
            {"hash": content_hash}
        ).fetchone()

        # Fallback: same email or phone under a differently spelled name
        if not existing and data.get("email"):
            existing = conn.execute(
                text("SELECT id FROM companies WHERE lower(email) = lower(:email) LIMIT 1"),
                {"email": data["email"]}
            ).fetchone()
        digits = phone_digits(data.get("phone", ""))
        if not existing and digits:
            existing = conn.execute(
                text("""SELECT id FROM companies
                        WHERE right(regexp_replace(phone, '[^0-9]', '', 'g'), 8) = :digits
                        LIMIT 1"""),
                {"digits": digits}
            ).fetchone()

        if existing:
            company_id = existing[0]
            conn.execute(text("""
                UPDATE companies SET
                    email_source = CASE WHEN COALESCE(email, '') = '' AND COALESCE(:email, '') <> ''
                                        THEN :email_source ELSE email_source END,
                    email = COALESCE(NULLIF(:email, ''), email),
                    phone = COALESCE(NULLIF(:phone, ''), phone),
                    director = COALESCE(NULLIF(:director, ''), director),
                    city = COALESCE(NULLIF(:city, ''), city),
                    website = COALESCE(NULLIF(:website, ''), website),
                    social_url = COALESCE(NULLIF(:social_url, ''), social_url),
                    last_seen = NOW()
                WHERE id = :id
            """), {
                "id": company_id,
                "email": data.get("email"),
                "email_source": data.get("email_source", "directory"),
                "social_url": data.get("social_url"),
                "phone": data.get("phone"),
                "director": data.get("director"),
                "city": data.get("city"),
                "website": data.get("website"),
            })
            conn.commit()
            return company_id
        else:
            result = conn.execute(text("""
                INSERT INTO companies (
                    content_hash, company_name, email, email_source, source_url, phone,
                    director, city, website, social_url, source_site
                ) VALUES (
                    :hash, :name, :email, :email_source, :source, :phone,
                    :director, :city, :website, :social_url, :source_site
                ) RETURNING id
            """), {
                "hash": content_hash,
                "name": data["company_name"],
                "email": data.get("email"),
                "source": data.get("source_url", ""),
                "phone": data.get("phone"),
                "director": data.get("director"),
                "city": data.get("city"),
                "website": data.get("website"),
                "social_url": data.get("social_url"),
                "email_source": data.get("email_source", "directory") if data.get("email") else None,
                "source_site": data.get("source_site", ""),
            })
            conn.commit()
            return result.fetchone()[0]


def insert_contact(engine, company_id: int, contact_type: str, value: str, source: str = None):
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO contacts (company_id, contact_type, contact_value, source_url)
            VALUES (:company_id, :type, :value, :source)
            ON CONFLICT (company_id, contact_type, contact_value) DO NOTHING
        """), {"company_id": company_id, "type": contact_type, "value": value, "source": source})
        conn.commit()


def get_all_companies(engine) -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM companies ORDER BY company_name", engine)


def get_companies_without_email(engine, recheck_days: int = 30,
                                include_dead: bool = False) -> pd.DataFrame:
    """Companies with a website but no email, not checked in the last recheck_days.

    Sites found dead or blocked are skipped unless include_dead is set.
    """
    return pd.read_sql(text("""
        SELECT * FROM companies
        WHERE website IS NOT NULL AND website != ''
          AND (email IS NULL OR email = '')
          AND (website_checked_at IS NULL
               OR website_checked_at < NOW() - make_interval(days => :days))
          AND (:include_dead OR COALESCE(website_status, '') NOT IN ('dead', 'blocked'))
        ORDER BY company_name
    """), engine, params={"days": recheck_days, "include_dead": include_dead})


def save_website_check(engine, company_id: int, status: str, email: str = "",
                       all_emails: list[str] = (), guess: str = "", page_url: str = ""):
    """Store the result of a website email check; every found email also goes to contacts."""
    with engine.connect() as conn:
        conn.execute(text("""
            UPDATE companies SET
                website_status = :status,
                website_checked_at = NOW(),
                email_source = CASE WHEN COALESCE(email, '') = '' AND :email <> ''
                                    THEN 'website' ELSE email_source END,
                email = COALESCE(NULLIF(email, ''), NULLIF(:email, '')),
                email_guess = NULLIF(:guess, '')
            WHERE id = :id
        """), {"id": company_id, "status": status, "email": email or "", "guess": guess or ""})
        for value in list(all_emails)[:10]:   # best 10 — big corporate sites list dozens
            conn.execute(text("""
                INSERT INTO contacts (company_id, contact_type, contact_value, source_url)
                VALUES (:id, 'email', :value, :source)
                ON CONFLICT (company_id, contact_type, contact_value) DO NOTHING
            """), {"id": company_id, "value": value, "source": page_url})
        conn.commit()


def update_email_status(engine, company_id: int, status: str, error: str = None,
                        message_id: str = None):
    """Update email status for a company."""
    with engine.connect() as conn:
        if status == "sent":
            conn.execute(text("""
                UPDATE companies
                SET email_status = :status, email_sent_at = NOW(), email_error = NULL,
                    email_message_id = :message_id
                WHERE id = :id
            """), {"id": company_id, "status": status, "message_id": message_id})
        else:
            conn.execute(text("""
                UPDATE companies
                SET email_status = :status, email_error = :error
                WHERE id = :id
            """), {"id": company_id, "status": status, "error": error})
        conn.commit()


def get_summary(engine) -> dict:
    with engine.connect() as conn:
        row = conn.execute(text("SELECT * FROM v_lead_summary")).fetchone()
        return dict(row._mapping) if row else {}


# === Email engagement tracking ===

def find_company_by_token(engine, token: str) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM companies WHERE tracking_token = :t"), {"t": token}
        ).fetchone()
        return dict(row._mapping) if row else None


def find_company_for_reply(engine, ref_ids: list[str], from_email: str) -> dict | None:
    """Match an incoming mail to a company: by the Message-ID it replies to, then by sender."""
    with engine.connect() as conn:
        if ref_ids:
            row = conn.execute(
                text("SELECT * FROM companies WHERE email_message_id = ANY(:ids) LIMIT 1"),
                {"ids": ref_ids},
            ).fetchone()
            if row:
                return dict(row._mapping)
        if from_email:
            row = conn.execute(
                text("""SELECT * FROM companies
                        WHERE lower(email) = lower(:e) AND email_sent_at IS NOT NULL
                        LIMIT 1"""),
                {"e": from_email},
            ).fetchone()
            if row:
                return dict(row._mapping)
    return None


def find_sent_company_by_email(engine, emails: list[str]) -> dict | None:
    if not emails:
        return None
    with engine.connect() as conn:
        row = conn.execute(
            text("""SELECT * FROM companies
                    WHERE lower(email) = ANY(:emails) AND email_sent_at IS NOT NULL
                    LIMIT 1"""),
            {"emails": [e.lower() for e in emails]},
        ).fetchone()
        return dict(row._mapping) if row else None


def event_exists(engine, company_id: int, event_type: str, detail: str) -> bool:
    with engine.connect() as conn:
        return conn.execute(
            text("""SELECT 1 FROM email_events
                    WHERE company_id = :id AND event_type = :type AND detail = :detail"""),
            {"id": company_id, "type": event_type, "detail": detail},
        ).fetchone() is not None


def record_email_event(engine, company_id: int, event_type: str, detail: str = None):
    """Log an event and update the company's engagement columns.

    event_type: click | reply | bounce | unsubscribe
    """
    updates = {
        "click": "clicked_at = COALESCE(clicked_at, NOW())",
        "reply": "replied_at = COALESCE(replied_at, NOW())",
        "bounce": "email_status = 'bounced'",
        "unsubscribe": "email_status = 'unsubscribed', unsubscribed_at = COALESCE(unsubscribed_at, NOW())",
    }
    with engine.connect() as conn:
        conn.execute(
            text("""INSERT INTO email_events (company_id, event_type, detail)
                    VALUES (:id, :type, :detail)"""),
            {"id": company_id, "type": event_type, "detail": detail},
        )
        conn.execute(text(f"UPDATE companies SET {updates[event_type]} WHERE id = :id"),
                     {"id": company_id})
        conn.commit()
