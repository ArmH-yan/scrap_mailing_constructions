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
                    email = COALESCE(NULLIF(:email, ''), email),
                    phone = COALESCE(NULLIF(:phone, ''), phone),
                    director = COALESCE(NULLIF(:director, ''), director),
                    city = COALESCE(NULLIF(:city, ''), city),
                    website = COALESCE(NULLIF(:website, ''), website),
                    last_seen = NOW()
                WHERE id = :id
            """), {
                "id": company_id,
                "email": data.get("email"),
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
                    content_hash, company_name, email, source_url, phone,
                    director, city, website, source_site
                ) VALUES (
                    :hash, :name, :email, :source, :phone,
                    :director, :city, :website, :source_site
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


def get_companies_without_email(engine) -> pd.DataFrame:
    """Get companies that have a website but no email."""
    return pd.read_sql("""
        SELECT * FROM companies
        WHERE website IS NOT NULL AND website != ''
          AND (email IS NULL OR email = '')
        ORDER BY company_name
    """, engine)


def update_company_email(engine, company_id: int, email: str):
    """Update the email for a company."""
    with engine.connect() as conn:
        conn.execute(text("""
            UPDATE companies SET email = :email, last_seen = NOW()
            WHERE id = :id
        """), {"id": company_id, "email": email})
        conn.commit()


def update_email_status(engine, company_id: int, status: str, error: str = None):
    """Update email status for a company."""
    with engine.connect() as conn:
        if status == "sent":
            conn.execute(text("""
                UPDATE companies
                SET email_status = :status, email_sent_at = NOW(), email_error = NULL
                WHERE id = :id
            """), {"id": company_id, "status": status})
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
        if row:
            cols = ["total_companies", "with_website", "with_email", "with_phone",
                     "emails_sent", "emails_failed", "emails_pending"]
            return dict(zip(cols, row))
        return {}
