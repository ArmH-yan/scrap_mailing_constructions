# Lead Generation Pipeline

Finds Armenian construction companies, collects their contact details, and runs
an email outreach campaign for **The Nest** (thenest.am) safety services:

- Dust-Proof Mesh Installation
- Installation of Metal Structures
- Safety Net System Installation

## Working with NEST (agent mode)

The NEST operations platform (nest-apps) now holds the companies and does the mailing:
campaigns, sending, click/unsubscribe tracking and the reply check all happen there
(Business → Mailing). This project only **scrapes**, as an agent:

```bash
python main.py --agent
```

It asks NEST for work every `AGENT_POLL_SECONDS`, runs the directory scrapes and website
email searches requested in Mailing → Scraping, and sends every company it finds to
NEST (which deduplicates them). Set `NEST_API_URL` and `NEST_SERVICE_KEY` (NEST's
`OUTREACH_SERVICE_KEY`) in `.env`. In this mode the local PostgreSQL, `--send`,
`--check-inbox` and `tracker/app.py` are not used. The standalone pipeline below still works.

## Features

- **Scraping** from four Armenian directories (construction.am, spyur.am, norakaruyc.am, myhome.am)
- **Website email finder** that visits company websites to fill in missing emails, including
  Cloudflare-protected addresses and JavaScript sites (rendered with Playwright)
- **PostgreSQL storage** that keeps data between runs and merges the same company found on
  several sites into one record
- **Email sending** through Zoho Mail over one connection, with a delay between emails and a daily cap
- **Tracking:** per-company link clicks, replies, bounces and unsubscribes, plus a funnel report
- **Exports** to CSV and a text summary for each run

## How it works

```
Directories ──► Crawler ──► PostgreSQL ◄── Website email finder
                               │
                               ├──► CSV / summary exports
                               │
                               └──► Email sending (Zoho SMTP)
                                        │
                     clicks ◄── tracker/app.py      inbox.py ──► replies / bounces
```

## Project structure

```
main.py                 CLI: scrape, send, check inbox, stats
from_website_email.py   Find missing emails on company websites
src/
  crawler.py            Directory scrapers and normalization
  website_emails.py     Website email extraction and ranking
  database.py           PostgreSQL access
  email_sender.py       SMTP sending and tracked links
  inbox.py              IMAP reply and bounce checker
  export.py             CSV and summary exports
  log.py                Logging to data/logs/
tracker/app.py          Click redirect and unsubscribe server (Flask)
sql/schema/             Database schema (safe to re-run, upgrades in place)
templates/              Email templates (Jinja2)
```

## Getting started

Setup, configuration, commands and developer notes are in **[INSTRUCTIONS.md](INSTRUCTIONS.md)**.

Short version:

```bash
pip install -r requirements.txt
cp .env.example .env    # then fill in your settings
docker-compose up -d
python main.py
```
