-- ===========================================================================
-- Lead Generation v2 — PostgreSQL Schema
-- ===========================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 1. Core Tables -----------------------------------------------------------

CREATE TABLE IF NOT EXISTS companies (
    id                SERIAL       PRIMARY KEY,
    content_hash      VARCHAR(40)  UNIQUE NOT NULL,
    company_name      VARCHAR(500) NOT NULL,
    website           VARCHAR(500),
    phone             VARCHAR(200),
    email             VARCHAR(255),
    address           TEXT,
    city              VARCHAR(255),
    district          VARCHAR(255),
    company_category  VARCHAR(255),
    company_description TEXT,
    services          TEXT,
    contact_page_url  VARCHAR(500),
    source_url        VARCHAR(500),
    source_site       VARCHAR(100),
    director          VARCHAR(500),
    founded_year      INTEGER,
    employee_count    VARCHAR(100),
    ownership_type    VARCHAR(255),
    gps_lat           DECIMAL(10, 7),
    gps_lon           DECIMAL(10, 7),
    facebook_url      VARCHAR(500),
    instagram_url     VARCHAR(500),
    linkedin_url      VARCHAR(500),
    has_active_projects BOOLEAN DEFAULT FALSE,
    project_count     INTEGER DEFAULT 0,
    project_names     TEXT,
    email_status      VARCHAR(20) DEFAULT 'pending',
    email_sent_at     TIMESTAMPTZ,
    email_error       TEXT,
    first_seen        TIMESTAMPTZ DEFAULT NOW(),
    last_seen         TIMESTAMPTZ DEFAULT NOW(),
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE companies IS 'Companies with content-hash dedup and email tracking.';

CREATE INDEX IF NOT EXISTS idx_companies_hash ON companies(content_hash);
CREATE INDEX IF NOT EXISTS idx_companies_email_status ON companies(email_status);


CREATE TABLE IF NOT EXISTS projects (
    id                  SERIAL       PRIMARY KEY,
    company_id          INTEGER      REFERENCES companies(id) ON DELETE CASCADE,
    project_name        VARCHAR(500) NOT NULL,
    project_description TEXT,
    project_url         VARCHAR(500),
    source_url          VARCHAR(500),
    detected_at         TIMESTAMPTZ DEFAULT NOW()
);


CREATE TABLE IF NOT EXISTS contacts (
    id            SERIAL       PRIMARY KEY,
    company_id    INTEGER      REFERENCES companies(id) ON DELETE CASCADE,
    contact_type  VARCHAR(50)  NOT NULL,
    contact_value VARCHAR(500) NOT NULL,
    is_primary    BOOLEAN DEFAULT FALSE,
    source_url    VARCHAR(500)
);


CREATE TABLE IF NOT EXISTS crawl_runs (
    id                 SERIAL       PRIMARY KEY,
    source_url         VARCHAR(500),
    companies_found    INTEGER DEFAULT 0,
    companies_enriched INTEGER DEFAULT 0,
    started_at         TIMESTAMPTZ DEFAULT NOW(),
    finished_at        TIMESTAMPTZ,
    status             VARCHAR(50) DEFAULT 'running',
    error_message      TEXT
);


-- 2. Views ------------------------------------------------------------------

CREATE OR REPLACE VIEW v_lead_summary AS
SELECT
    COUNT(*) AS total_companies,
    COUNT(*) FILTER (WHERE website IS NOT NULL AND website != '') AS with_website,
    COUNT(*) FILTER (WHERE email IS NOT NULL AND email != '') AS with_email,
    COUNT(*) FILTER (WHERE phone IS NOT NULL AND phone != '') AS with_phone,
    COUNT(*) FILTER (WHERE has_active_projects = TRUE) AS with_projects,
    COUNT(*) FILTER (WHERE email_status = 'sent') AS emails_sent,
    COUNT(*) FILTER (WHERE email_status = 'failed') AS emails_failed,
    COUNT(*) FILTER (WHERE email_status = 'pending') AS emails_pending
FROM companies;
