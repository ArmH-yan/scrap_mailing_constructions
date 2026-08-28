"""
Lead Generation v2 — Database Module
PostgreSQL storage for companies and contacts
"""

import os
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


def compute_content_hash(name: str, phone: str, url: str) -> str:
    raw = f"{name}|{phone}|{url}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


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
    # Drop old tables to apply new schema
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS contacts CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS projects CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS companies CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS crawl_runs CASCADE"))
        conn.execute(text("DROP VIEW IF EXISTS v_lead_summary CASCADE"))
        conn.commit()
    run_sql_file(engine, f"{SQL_DIR}/01_schema.sql")
    log.info("  [OK] Schema initialized")


def upsert_company(engine, data: dict) -> int:
    """Insert or update company by content_hash. Returns company ID."""
    content_hash = data.get("content_hash") or compute_content_hash(
        data.get("company_name", ""),
        data.get("phone", ""),
        data.get("source_url", "")
    )

    with engine.connect() as conn:
        existing = conn.execute(
            text("SELECT id FROM companies WHERE content_hash = :hash"),
            {"hash": content_hash}
        ).fetchone()

        if existing:
            company_id = existing[0]
            conn.execute(text("""
                UPDATE companies SET
                    website = COALESCE(:website, website),
                    phone = COALESCE(:phone, phone),
                    email = COALESCE(:email, email),
                    address = COALESCE(:address, address),
                    city = COALESCE(:city, city),
                    district = COALESCE(:district, district),
                    company_category = COALESCE(:category, company_category),
                    company_description = COALESCE(:description, company_description),
                    services = COALESCE(:services, services),
                    contact_page_url = COALESCE(:contact_url, contact_page_url),
                    director = COALESCE(:director, director),
                    founded_year = COALESCE(:founded_year, founded_year),
                    employee_count = COALESCE(:employee_count, employee_count),
                    ownership_type = COALESCE(:ownership_type, ownership_type),
                    gps_lat = COALESCE(:gps_lat, gps_lat),
                    gps_lon = COALESCE(:gps_lon, gps_lon),
                    facebook_url = COALESCE(:facebook_url, facebook_url),
                    instagram_url = COALESCE(:instagram_url, instagram_url),
                    linkedin_url = COALESCE(:linkedin_url, linkedin_url),
                    has_active_projects = :has_projects,
                    project_count = :project_count,
                    project_names = COALESCE(:project_names, project_names),
                    last_seen = NOW()
                WHERE id = :id
            """), {
                "id": company_id,
                "website": data.get("website"),
                "phone": data.get("phone"),
                "email": data.get("email"),
                "address": data.get("address"),
                "city": data.get("city"),
                "district": data.get("district"),
                "category": data.get("company_category"),
                "description": data.get("company_description"),
                "services": data.get("services"),
                "contact_url": data.get("contact_page_url"),
                "director": data.get("director"),
                "founded_year": data.get("founded_year"),
                "employee_count": data.get("employee_count"),
                "ownership_type": data.get("ownership_type"),
                "gps_lat": data.get("gps_lat"),
                "gps_lon": data.get("gps_lon"),
                "facebook_url": data.get("facebook_url"),
                "instagram_url": data.get("instagram_url"),
                "linkedin_url": data.get("linkedin_url"),
                "has_projects": data.get("has_active_projects", False),
                "project_count": data.get("project_count", 0),
                "project_names": data.get("project_names"),
            })
            conn.commit()
            return company_id
        else:
            result = conn.execute(text("""
                INSERT INTO companies (
                    content_hash, company_name, website, phone, email, address, city,
                    district, company_category, company_description, services,
                    contact_page_url, source_url, source_site,
                    director, founded_year, employee_count, ownership_type,
                    gps_lat, gps_lon, facebook_url, instagram_url, linkedin_url,
                    has_active_projects, project_count, project_names
                ) VALUES (
                    :hash, :name, :website, :phone, :email, :address, :city,
                    :district, :category, :description, :services,
                    :contact_url, :source, :source_site,
                    :director, :founded_year, :employee_count, :ownership_type,
                    :gps_lat, :gps_lon, :facebook_url, :instagram_url, :linkedin_url,
                    :has_projects, :project_count, :project_names
                ) RETURNING id
            """), {
                "hash": content_hash,
                "name": data["company_name"],
                "website": data.get("website"),
                "phone": data.get("phone"),
                "email": data.get("email"),
                "address": data.get("address"),
                "city": data.get("city"),
                "district": data.get("district"),
                "category": data.get("company_category"),
                "description": data.get("company_description"),
                "services": data.get("services"),
                "contact_url": data.get("contact_page_url"),
                "source": data.get("source_url", ""),
                "source_site": data.get("source_site", ""),
                "director": data.get("director"),
                "founded_year": data.get("founded_year"),
                "employee_count": data.get("employee_count"),
                "ownership_type": data.get("ownership_type"),
                "gps_lat": data.get("gps_lat"),
                "gps_lon": data.get("gps_lon"),
                "facebook_url": data.get("facebook_url"),
                "instagram_url": data.get("instagram_url"),
                "linkedin_url": data.get("linkedin_url"),
                "has_projects": data.get("has_active_projects", False),
                "project_count": data.get("project_count", 0),
                "project_names": data.get("project_names"),
            })
            conn.commit()
            return result.fetchone()[0]


def insert_project(engine, company_id: int, name: str, url: str = None, source: str = None):
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO projects (company_id, project_name, project_url, source_url)
            VALUES (:company_id, :name, :url, :source)
        """), {"company_id": company_id, "name": name, "url": url, "source": source})
        conn.commit()


def insert_contact(engine, company_id: int, contact_type: str, value: str, source: str = None):
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO contacts (company_id, contact_type, contact_value, source_url)
            VALUES (:company_id, :type, :value, :source)
        """), {"company_id": company_id, "type": contact_type, "value": value, "source": source})
        conn.commit()


def insert_named_contact(engine, company_id: int, name: str, title: str, source: str = None):
    """Insert a named individual contact (e.g. an architect) linked to a company."""
    value = f"{name} ({title})" if title else name
    insert_contact(engine, company_id, "named_contact", value, source)


def get_all_companies(engine) -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM companies ORDER BY company_name", engine)


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
                     "with_projects", "emails_sent", "emails_failed", "emails_pending"]
            return dict(zip(cols, row))
        return {}
