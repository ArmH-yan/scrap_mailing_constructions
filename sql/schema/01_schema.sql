-- ===========================================================================
-- Lead Generation — PostgreSQL Schema (Simplified)
-- ===========================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 1. Core Tables -----------------------------------------------------------

CREATE TABLE IF NOT EXISTS companies (
    id                SERIAL       PRIMARY KEY,
    content_hash      VARCHAR(40)  UNIQUE NOT NULL,
    company_name      VARCHAR(500) NOT NULL,
    email             VARCHAR(255),
    source_url        VARCHAR(500),
    phone             VARCHAR(200),
    director          VARCHAR(500),
    city              VARCHAR(255),
    website           VARCHAR(500),
    source_site       VARCHAR(100),
    email_status      VARCHAR(20) DEFAULT 'pending',
    email_sent_at     TIMESTAMPTZ,
    email_error       TEXT,
    first_seen        TIMESTAMPTZ DEFAULT NOW(),
    last_seen         TIMESTAMPTZ DEFAULT NOW(),
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE companies IS 'Companies with content-hash dedup and email tracking.';

-- Upgrade older databases created before the website column existed
ALTER TABLE companies ADD COLUMN IF NOT EXISTS website VARCHAR(500);

-- Email tracking: per-company link token, sent Message-ID, engagement times.
-- The volatile default gives every existing row its own token on upgrade.
ALTER TABLE companies ADD COLUMN IF NOT EXISTS tracking_token VARCHAR(16)
    DEFAULT substr(md5(random()::text || clock_timestamp()::text), 1, 12);
ALTER TABLE companies ADD COLUMN IF NOT EXISTS email_message_id VARCHAR(255);
ALTER TABLE companies ADD COLUMN IF NOT EXISTS clicked_at       TIMESTAMPTZ;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS replied_at       TIMESTAMPTZ;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS unsubscribed_at  TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS uq_companies_token ON companies(tracking_token);
CREATE INDEX IF NOT EXISTS idx_companies_message_id ON companies(email_message_id);

CREATE INDEX IF NOT EXISTS idx_companies_hash ON companies(content_hash);
CREATE INDEX IF NOT EXISTS idx_companies_email_status ON companies(email_status);


CREATE TABLE IF NOT EXISTS contacts (
    id            SERIAL       PRIMARY KEY,
    company_id    INTEGER      REFERENCES companies(id) ON DELETE CASCADE,
    contact_type  VARCHAR(50)  NOT NULL,
    contact_value VARCHAR(500) NOT NULL,
    is_primary    BOOLEAN DEFAULT FALSE,
    source_url    VARCHAR(500)
);

-- Remove duplicates left by older runs, then enforce uniqueness
DELETE FROM contacts a USING contacts b
WHERE a.id > b.id
  AND a.company_id = b.company_id
  AND a.contact_type = b.contact_type
  AND a.contact_value = b.contact_value;

CREATE UNIQUE INDEX IF NOT EXISTS uq_contacts_value
    ON contacts(company_id, contact_type, contact_value);


-- One row per click / reply / bounce / unsubscribe (email_status = latest state)
CREATE TABLE IF NOT EXISTS email_events (
    id          SERIAL      PRIMARY KEY,
    company_id  INTEGER     REFERENCES companies(id) ON DELETE CASCADE,
    event_type  VARCHAR(20) NOT NULL,
    detail      TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_email_events_company ON email_events(company_id, event_type);


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

-- Dropped first: CREATE OR REPLACE can't reorder columns
DROP VIEW IF EXISTS v_lead_summary;
CREATE VIEW v_lead_summary AS
SELECT
    COUNT(*) AS total_companies,
    COUNT(*) FILTER (WHERE website IS NOT NULL AND website != '') AS with_website,
    COUNT(*) FILTER (WHERE email IS NOT NULL AND email != '') AS with_email,
    COUNT(*) FILTER (WHERE phone IS NOT NULL AND phone != '') AS with_phone,
    COUNT(*) FILTER (WHERE email_status = 'sent') AS emails_sent,
    COUNT(*) FILTER (WHERE email_status = 'failed') AS emails_failed,
    COUNT(*) FILTER (WHERE email_status = 'pending') AS emails_pending,
    COUNT(*) FILTER (WHERE clicked_at IS NOT NULL) AS emails_clicked,
    COUNT(*) FILTER (WHERE replied_at IS NOT NULL) AS emails_replied,
    COUNT(*) FILTER (WHERE email_status = 'bounced') AS emails_bounced,
    COUNT(*) FILTER (WHERE email_status = 'unsubscribed') AS unsubscribed
FROM companies;
