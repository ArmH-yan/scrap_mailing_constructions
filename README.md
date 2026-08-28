# Lead Generation Pipeline v2

Web scraper for finding Armenian construction companies. Collects leads and sends them personalized emails about safety services.

## Services Offered

- Dust-Proof Mesh Installation
- Installation of Metal Structures
- Safety Net System Installation

## What it does

1. **Scrapes** company data from Armenian business directories
2. **Stores** everything in PostgreSQL
3. **Exports** CSV/TXT files locally
4. **Sends** personalized emails via Gmail SMTP

Extracts: name, phone, email, full address, city, district, director, founded year, employee count, ownership type, GPS coordinates, social media links, services, and project information.

## Architecture

```
Web sources → Crawler (BS4 + Playwright) → PostgreSQL → Exports
                                                          ↓
                                                   Email Sending
```

## Setup

### 1. Virtual Env

```bash
python -m venv venv
.\venv\Scripts\activate
python.exe -m pip install --upgrade pip
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
python -m playwright install chromium --with-deps
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env with your database and SMTP credentials
```

### 4. Docker (PostgreSQL)

```bash
docker-compose up -d
```

### 5. Gmail SMTP Setup

1. Go to https://myaccount.google.com/security
2. Enable 2-Step Verification
3. Go to "App passwords"
4. Create app password for "Mail"
5. Add to `.env`:
   ```
   SMTP_USER=your_email@gmail.com
   SMTP_PASS=your_16_char_app_password
   ```

## Usage

### Scrape Companies

```bash
# All sources
python main.py

# Single source
python main.py construction_am
python main.py spyur_am

# Multiple sources
python main.py construction_am spyur_am

# Scheduled runs (every 6 hours)
python main.py --schedule
```

### Send Emails

```bash
# Test email first
python main.py --send-test

# Preview emails (dry run)
python main.py --send-dry

# Send to all pending
python main.py --send
```

## Sources

| Source | Type | Companies | Notes |
|--------|------|-----------|-------|
| construction.am | Static/BS4 | ~556 | Armenian letter pagination (38 letters) |
| spyur.am | Static/BS4 | ~400 | Page pagination, 20 per page |

## Data Collected

| Field | Description |
|-------|-------------|
| company_name | Original name from source |
| phone | Primary phone number |
| email | Business email |
| address | Full location address |
| city | Extracted city name |
| district | Administrative district |
| director | Company director/owner name |
| founded_year | Year founded |
| employee_count | Employee range |
| ownership_type | Private/state/etc |
| gps_lat, gps_lon | Map coordinates |
| facebook_url, instagram_url, linkedin_url | Social media |
| services | Company services/role description |
| project_count, project_names | Detected projects |
| email_status | pending/sent/failed |

## Exports

- `data/exports/companies_*.csv` - All companies
- `data/exports/summary_report_*.txt` - Quick stats

## Logs

Every run creates a log file in `data/logs/run_*.log` with timestamps.

## Notes

- construction.am uses Armenian letter pagination (38 letters) to find company profiles
- spyur.am uses page-based pagination (20 companies per page)
- Emails on construction.am are hidden in popover button attributes
- The pipeline deduplicates companies using a SHA-1 hash of (name + phone + source_url)
- All HTTP errors are caught and logged — the pipeline never crashes on a 403/timeout

## Documentation

See [INSTRUCTIONS.md](INSTRUCTIONS.md) for detailed setup and developer documentation.
