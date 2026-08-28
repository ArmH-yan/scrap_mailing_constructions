# Lead Generation Pipeline v2 — Project Summary

## Overview

A web scraper for finding Armenian construction companies. Collects leads and sends them personalized emails about safety services (Dust-Proof Mesh, Metal Structures, Safety Nets).

## Architecture

```
Web sources → Crawler (BS4 + Playwright) → PostgreSQL → Exports
                                                          ↓
                                                   Email Sending
```

## Key Components

| Component | File | Purpose |
|-----------|------|---------|
| Main Pipeline | `main.py` | Orchestrates the entire workflow |
| Crawler | `src/crawler.py` | Multi-source parallel scraper |
| Database | `src/database.py` | PostgreSQL storage |
| Normalization | `src/scoring.py` | Phone/email/website normalization |
| Email Sender | `src/email_sender.py` | Gmail SMTP email sending |
| Export | `src/export.py` | CSV/TXT exports |
| Logging | `src/log.py` | Run logging to files |

## Data Sources

| Source | Type | Companies | Notes |
|--------|------|-----------|-------|
| construction.am | Static/BS4 | ~556 | Armenian letter pagination (38 letters) |
| spyur.am | Static/BS4 | ~400 | Page pagination (20/page) |

## Data Collected

- Company info: name, phone, email, address, city, district
- Business data: director, founded year, employee count, ownership type
- Location: GPS coordinates
- Social: Facebook, Instagram, LinkedIn URLs
- Projects: project count and names
- Email tracking: status, sent timestamp, error messages

## Email Features

- **Template-based**: HTML email templates with Jinja2
- **Personalized**: Company name and project names
- **Tracked**: Email status (pending/sent/failed)
- **Gmail SMTP**: Uses App Password authentication

## Exports

- `data/exports/companies_*.csv` — All companies
- `data/exports/summary_report_*.txt` — Quick stats

## Tech Stack

- **Language**: Python 3
- **Scraping**: BeautifulSoup4, Playwright (Chromium)
- **Database**: PostgreSQL 15 (Docker)
- **Email**: Gmail SMTP (smtplib)
- **Templates**: Jinja2
- **Data**: pandas, openpyxl

## CLI Usage

```bash
python main.py                    # All sources
python main.py construction_am    # Single source
python main.py spyur_am           # Single source
python main.py --schedule         # Every 6 hours
python main.py --send-test        # Send test email
python main.py --send-dry         # Preview emails
python main.py --send             # Send all pending
```

## Environment Configuration

Key `.env` variables:
- `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASS` — PostgreSQL
- `SCRAPE_DELAY`, `SCRAPE_TIMEOUT`, `MAX_WORKERS` — Scraper tuning
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` — Gmail SMTP
- `SMTP_FROM_NAME`, `SMTP_FROM_EMAIL` — Sender info
- `EMAIL_TEMPLATE_PATH` — Template file location
- `TEST_EMAIL` — Test email address

## Key Features

1. **Incremental saves** — Stores companies in batches during crawl
2. **Graceful shutdown** — Handles SIGINT/SIGTERM
3. **Deduplication** — SHA-1 hash of (name + phone + source_url)
4. **Error resilience** — HTTP errors logged, never crashes on 403/timeout
5. **Email tracking** — Tracks sent/failed/pending status
6. **Template system** — Customizable HTML email templates

## Services Offered

- Dust-Proof Mesh Installation
- Installation of Metal Structures
- Safety Net System Installation
