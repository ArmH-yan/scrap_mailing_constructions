# Lead Generation Pipeline v2 — Instructions

## Overview

A web scraper for finding Armenian construction companies. Collects leads and sends them personalized emails about safety services (Dust-Proof Mesh, Metal Structures, Safety Nets).

## Architecture

```
Web sources → Crawler (BS4 + Playwright) → PostgreSQL → Exports
                                                          ↓
                                                   Email Sending
```

---

## Part 1: Setup Guide

### Prerequisites

- Python 3.10+
- Docker (for PostgreSQL)
- Gmail account with App Password

### 1. Virtual Environment

```bash
python -m venv venv
.\venv\Scripts\activate
python.exe -m pip install --upgrade pip
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
python -m playwright install chromium --with-deps
```

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your settings:

```bash
# PostgreSQL (default works with Docker)
DB_HOST=localhost
DB_PORT=5432
DB_NAME=leadgen
DB_USER=postgres
DB_PASS=postgres

# Gmail SMTP
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_email@gmail.com
SMTP_PASS=your_app_password
SMTP_FROM_NAME=Your Company Name
SMTP_FROM_EMAIL=your_email@gmail.com

# Email
EMAIL_TEMPLATE_PATH=templates/email_template.html
TEST_EMAIL=your_email@gmail.com
```

### 4. Gmail App Password Setup

1. Go to https://myaccount.google.com/security
2. Enable 2-Step Verification
3. Go to "App passwords" (search in account settings)
4. Select "Mail" and "Other (Custom name)"
5. Enter "Lead Generation Pipeline"
6. Copy the 16-character password
7. Paste it in `.env` as `SMTP_PASS`

### 5. Start PostgreSQL

```bash
docker-compose up -d
```

### 6. Run the Pipeline

```bash
# Scrape all sources
python main.py

# Scrape specific source
python main.py construction_am
python main.py spyur_am

# Multiple sources
python main.py construction_am spyur_am

# Scheduled runs (every 6 hours)
python main.py --schedule
```

### 7. Send Emails

```bash
# Send test email first
python main.py --send-test

# Preview emails without sending
python main.py --send-dry

# Send emails to all pending companies
python main.py --send
```

---

## Part 2: Developer Documentation

### Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     main.py                             │
│  Pipeline orchestrator + CLI interface                  │
└───────────────┬─────────────────────────────────────────┘
                │
    ┌───────────┼───────────┐
    ▼           ▼           ▼
┌────────┐ ┌────────┐ ┌────────┐
│crawler │ │database│ │export  │
│  .py   │ │  .py   │ │  .py   │
└────┬───┘ └────┬───┘ └────────┘
     │          │
     ▼          ▼
┌────────┐ ┌────────┐
│scoring │ │email_  │
│  .py   │ │sender  │
└────────┘ │  .py   │
           └────────┘
```

### Module Reference

| Module | Purpose |
|--------|---------|
| `main.py` | Pipeline orchestration, CLI interface |
| `src/crawler.py` | Web scraping (BS4 + Playwright) |
| `src/database.py` | PostgreSQL operations |
| `src/scoring.py` | Phone/email/website normalization |
| `src/export.py` | CSV/XLSX export |
| `src/email_sender.py` | Gmail SMTP email sending |
| `src/log.py` | Logging configuration |

### Data Flow

1. **Scraping**: `crawler.py` fetches pages from construction.am, spyur.am
2. **Parsing**: Extract company data (name, phone, email, address, etc.)
3. **Normalization**: `scoring.py` normalizes phone/email/website formats
4. **Storage**: `database.py` upserts to PostgreSQL with content-hash dedup
5. **Export**: `export.py` generates CSV reports
6. **Email**: `email_sender.py` sends HTML emails via Gmail SMTP

### Database Schema

**companies table:**
- `id`: Primary key
- `content_hash`: SHA-1 dedup key (name + phone + source_url)
- `company_name`, `website`, `phone`, `email`, `address`, `city`
- `director`, `founded_year`, `employee_count`, `ownership_type`
- `gps_lat`, `gps_lon`: Coordinates
- `facebook_url`, `instagram_url`, `linkedin_url`
- `has_active_projects`, `project_count`, `project_names`
- `email_status`: pending/sent/failed
- `email_sent_at`: Timestamp of last send
- `email_error`: Error message if failed
- `first_seen`, `last_seen`, `created_at`

**projects table:**
- `id`, `company_id` (FK), `project_name`, `project_url`, `source_url`

**contacts table:**
- `id`, `company_id` (FK), `contact_type`, `contact_value`, `source_url`

### Adding New Scrapers

1. Add source config to `SOURCES` dict in `src/crawler.py`:
```python
"new_source": {
    "base_url": "https://example.com",
    "listing_pages": ["/page1", "/page2"],
    "type": "static",  # or "js" for Playwright
    "letter_pagination": False,
}
```

2. Create parser functions:
- `parse_new_source_links(html, base_url)` - Extract company links
- `parse_new_source_profile(html, url)` - Extract company data

3. Add to `PAGE_LIMITS` in `main.py` if needed

### Customizing Email Templates

Edit `templates/email_template.html`. Available variables:

```html
{{ company_name }}      - Company name from scraped data
{{ project_names }}     - Comma-separated project names
{{ sender_name }}       - From SMTP_FROM_NAME
{{ sender_email }}      - From SMTP_FROM_EMAIL
```

### Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_HOST` | localhost | PostgreSQL host |
| `DB_PORT` | 5432 | PostgreSQL port |
| `DB_NAME` | leadgen | Database name |
| `DB_USER` | postgres | Database user |
| `DB_PASS` | postgres | Database password |
| `SCRAPE_DELAY` | 0.5 | Delay between requests (seconds) |
| `SCRAPE_TIMEOUT` | 15 | Request timeout (seconds) |
| `MAX_WORKERS` | 5 | Parallel scraping threads |
| `BATCH_SIZE` | 100 | Batch size for DB inserts |
| `RUN_INTERVAL_HOURS` | 6 | Scheduler interval |
| `SMTP_HOST` | smtp.gmail.com | SMTP server |
| `SMTP_PORT` | 587 | SMTP port |
| `SMTP_USER` | - | Gmail address |
| `SMTP_PASS` | - | Gmail App Password |
| `SMTP_FROM_NAME` | - | Sender name |
| `SMTP_FROM_EMAIL` | - | Sender email |
| `EMAIL_TEMPLATE_PATH` | templates/email_template.html | Template file |
| `TEST_EMAIL` | - | Test email address |

### CLI Commands

| Command | Description |
|---------|-------------|
| `python main.py` | Scrape all sources |
| `python main.py <source>` | Scrape specific source |
| `python main.py --schedule` | Run on schedule (every 6h) |
| `python main.py --send` | Send emails to pending |
| `python main.py --send-dry` | Preview emails (no send) |
| `python main.py --send-test` | Send test email |

### Logs

Every run creates a log file in `data/logs/run_*.log` with timestamps.

### Exports

- `data/exports/companies_*.csv` - All companies
- `data/exports/summary_report_*.txt` - Quick stats
