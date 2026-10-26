# Lead Generation Pipeline — Instructions

Full setup, usage and developer documentation. For a project overview, see [README.md](README.md).

---

## Part 1: Setup

### Prerequisites

- Python 3.10+
- Docker Desktop (runs PostgreSQL)
- A Zoho Mail account for the sending address (info@thenest.am)

### 1. Virtual environment

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

Playwright's Chromium is used for JavaScript-rendered pages: company websites that need a
browser, and any source configured with `"type": "js"`.

### 3. Configure

```bash
cp .env.example .env
```

Edit `.env`. Every setting is described in the [Configuration reference](#configuration-reference).
The minimum to change is the Zoho login:

```bash
SMTP_USER=info@thenest.am
SMTP_PASS=your_app_password
SMTP_FROM_NAME=The Nest
SMTP_FROM_EMAIL=info@thenest.am
TEST_EMAIL=your_personal_address@example.com
```

### 4. Zoho Mail setup

1. **App password** (needed if two-factor sign-in is on): Zoho Accounts → Security →
   App Passwords → generate one and put it in `SMTP_PASS`.
2. **IMAP** (needed for `--check-inbox`): Zoho Mail → Settings → Mail Accounts → IMAP Access → enable.
3. **Data center:** if your Zoho account is on zoho.eu, set `SMTP_HOST=smtp.zoho.eu` and
   `IMAP_HOST=imap.zoho.eu`.
4. **Domain records:** in Zoho Mail Admin → Domains, add the SPF, DKIM and DMARC records it
   shows to the DNS of thenest.am. Without them most cold email lands in spam.

### 5. Start PostgreSQL

```bash
docker-compose up -d
```

PostgreSQL runs on port **5433** (database `developers`, user/password `postgres`).
CloudBeaver, a web database browser, runs on http://localhost:8978.

The schema in `sql/schema/01_schema.sql` is applied on every run. It only creates what is
missing and upgrades older databases in place — existing data is never deleted.

---

## Part 2: Usage

### Scrape companies

```bash
python main.py                              # all sources
python main.py construction_am              # one source
python main.py construction_am spyur_am     # several sources
python main.py --schedule                   # repeat every RUN_INTERVAL_HOURS
```

You are asked how many pages to scrape per source (Enter = all). Companies are saved as they
are found, so an interrupted run (Ctrl+C) keeps what it collected.

| Source key | Site | Method |
|------------|------|--------|
| `construction_am` | construction.am | HTML, Armenian letter pagination (up to 38 pages) |
| `spyur_am` | spyur.am | HTML, page pagination (up to 20 pages) |
| `norakaruyc_am` | norakaruyc.am | JSON API |
| `myhome_am` | myhome.am | REST API (up to 5 pages) |

### Find emails on company websites

For companies that have a website but no email:

```bash
python from_website_email.py --dry-run     # preview, no database changes
python from_website_email.py               # save results
```

| Option | Default | Effect |
|--------|---------|--------|
| `--limit N` | all | Check at most N companies |
| `--workers N` | 5 | Websites checked in parallel |
| `--no-browser` | off | Skip the Playwright pass |
| `--recheck-days N` | 30 | Skip sites checked in the last N days (`0` = recheck all) |
| `--include-dead` | off | Also retry sites previously found dead or blocked |

How it works:

1. Fixes address typos and tries https/http, with and without `www`; gives up fast on dead sites.
2. Reads the homepage, then the site's own contact/about links (English, Armenian, Russian).
3. Extracts emails from mailto links, Cloudflare email protection, obfuscated text and page source,
   and drops junk (image names, Sentry keys, placeholders).
4. Picks the best address: the site's own domain, then free providers (gmail.com, mail.ru…),
   then other domains; `info@`, `office@`, `contact@` are preferred.
5. Opens JavaScript-only and blocking sites in a headless browser.
6. If a live site has no email, stores an unverified `info@domain` in `email_guess` only.

Results per company: `website_status` (`found` / `no_email` / `dead` / `blocked`),
`website_checked_at`, `email_source` (`website`). Up to 10 found addresses go to `contacts`.

### Send emails

```bash
python main.py --send-test     # one email to TEST_EMAIL
python main.py --send-dry      # list who would get an email, send nothing
python main.py --send          # send to every company with email_status = pending
```

- One SMTP connection per run, `SEND_DELAY_SECONDS` between emails, at most `DAILY_SEND_LIMIT` per run.
- Sent companies are marked `sent` and are never emailed again; failures are marked `failed`.
- The cap is per run, not per calendar day — don't run `--send` twice a day.
- A new domain should start low (20–30 a day) and increase weekly.

### Track results

```bash
python main.py --check-inbox   # record replies, bounces and "unsubscribe" replies
python main.py --stats         # funnel: pending / sent / clicked / replied / bounced / unsubscribed
```

`--check-inbox` reads the last `IMAP_LOOKBACK_DAYS` of the inbox without marking anything
as read, and is safe to run repeatedly. Replies are matched by the Message-ID they answer,
or by sender address. A bounced company is marked `bounced`; a reply starting with
"unsubscribe", "stop", "отписаться" or "չուղարկել" marks it `unsubscribed`.

### Link tracking server

Clicks and unsubscribes go through `tracker/app.py`:

```bash
python -m tracker.app          # http://localhost:8080
```

| Route | Effect |
|-------|--------|
| `GET /r/<token>` | Logs a click, redirects to `WEBSITE_URL?utm_source=email&utm_campaign=…&c=<token>` |
| `GET /u/<token>` | Unsubscribe confirmation page |
| `POST /u/<token>` | Unsubscribes (also used by Gmail/Outlook one-click unsubscribe) |
| `GET /health` | Health check |

Deploy it behind HTTPS on a subdomain (for example `go.thenest.am`) and set `TRACK_BASE_URL`
to that address. While `TRACK_BASE_URL` is empty, emails carry no tracked links.

### Outputs

- `data/exports/companies_<run>.csv` — companies from the run
  (name, email, source URL, phone, director, city, website, source site, email status)
- `data/exports/summary_report_<run>.txt` — counts and percentages
- `data/logs/run_<run>.log` — full log of each run

---

## Part 3: Developer documentation

### Modules

| File | Purpose |
|------|---------|
| `main.py` | CLI and pipeline orchestration (scrape → store → export, send, inbox, stats) |
| `from_website_email.py` | Batch website email finder (parallel HTTP + browser pass) |
| `src/crawler.py` | Source definitions, parsers, scraping agents, normalization |
| `src/website_emails.py` | Website fetching, email extraction, ranking, Playwright fetcher |
| `src/database.py` | Schema setup, company upsert/dedup, email status and event tracking |
| `src/email_sender.py` | SMTP connection, throttled sending, Message-ID, tracked links |
| `src/inbox.py` | IMAP inbox scan for replies, bounces and unsubscribes |
| `src/export.py` | CSV and summary exports |
| `src/log.py` | Console and file logging |
| `tracker/app.py` | Flask click/unsubscribe server |

### Data flow

1. **Scrape:** `crawler.run_source` collects listing links, fetches profiles in parallel and parses them.
2. **Enrich:** companies missing an email or phone get their website checked (HTTP only, no guessing).
3. **Normalize:** phones and emails are cleaned; social links are moved out of `website`.
4. **Store:** `database.upsert_company` inserts or updates the company (see Deduplication).
5. **Export:** CSV and summary for the run.
6. **Send:** pending companies get the rendered template; the Message-ID is saved.
7. **Track:** clicks via the tracker, replies/bounces via `--check-inbox`, all logged in `email_events`.

### Deduplication

A company's identity (`content_hash`) is the SHA-1 of its normalized name: lowercased,
punctuation removed, legal forms dropped (ՍՊԸ, ООО, LLC, CJSC, Ltd…). If the name does not
match, the company is matched by email, then by the last 8 digits of its phone. Matches update
the existing row, filling only empty fields.

### Database schema

**companies**

| Column | Description |
|--------|-------------|
| `id`, `content_hash` | Primary key and dedup key |
| `company_name`, `phone`, `email`, `director`, `city` | Contact details |
| `website`, `social_url` | Company website; Facebook/Instagram/YouTube etc. links go to `social_url` |
| `source_url`, `source_site` | Where the company was scraped |
| `email_source` | `directory` (from a listing) or `website` (found on their site) |
| `email_guess` | Unverified `info@domain`; never sent automatically |
| `website_status`, `website_checked_at` | Result of the last website check |
| `email_status` | `pending` / `sent` / `failed` / `bounced` / `unsubscribed` |
| `email_sent_at`, `email_error`, `email_message_id` | Last send |
| `tracking_token` | Per-company code used in tracked links |
| `clicked_at`, `replied_at`, `unsubscribed_at` | First engagement times |
| `first_seen`, `last_seen`, `created_at` | Timestamps |

**contacts** — `company_id`, `contact_type` (`phone` / `email`), `contact_value`, `source_url`;
unique per company, type and value.

**email_events** — `company_id`, `event_type` (`click` / `reply` / `bounce` / `unsubscribe`),
`detail`, `created_at`.

**crawl_runs** — reserved for run history.

**v_lead_summary** (view) — counts used by `--stats`.

### Adding a new source

1. Add an entry to `SOURCES` in `src/crawler.py`:

   ```python
   "new_source": {
       "base_url": "https://example.am",
       "listing_pages": ["/companies"],
       "type": "static",            # static | js | api | api_rest
       "letter_pagination": False,
   }
   ```

2. Write the parsers:
   - `parse_new_source_links(html, base_url)` — returns `[{"url": ..., "name": ...}]`
   - `parse_new_source_profile(html, url)` — returns a company dict
     (`company_name`, `phone`, `email`, `website`, `city`, `director`, `source_url`, `source_site`)
3. Wire them into `run_source` and add a page limit to `PAGE_LIMITS` in `main.py`.

### Email templates

Templates are Jinja2 files in `templates/` (set with `EMAIL_TEMPLATE_PATH`). Variables:

| Variable | Value |
|----------|-------|
| `{{ company_name }}` | Company name |
| `{{ sender_name }}` | `SMTP_FROM_NAME` |
| `{{ sender_email }}` | `SMTP_FROM_EMAIL` |
| `{{ website_url }}` | Tracked website link (empty while `TRACK_BASE_URL` is unset) |
| `{{ unsubscribe_url }}` | Tracked unsubscribe link (empty while `TRACK_BASE_URL` is unset) |

Show the website button only when the link exists:

```html
{% if website_url %}<a href="{{ website_url }}">Visit our website</a>{% endif %}
```

`{{ project_names }}` is not filled in by the pipeline — don't use it.

### Configuration reference

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_HOST` | localhost | PostgreSQL host |
| `DB_PORT` | 5432 | PostgreSQL port (`.env.example` uses 5433 for Docker) |
| `DB_NAME` | leadgen | Database name (`.env.example` uses `developers`) |
| `DB_USER` / `DB_PASS` | postgres | Database login |
| `SCRAPE_DELAY` | 0.5 | Delay between directory requests (seconds) |
| `SCRAPE_TIMEOUT` | 15 | Directory request timeout (seconds) |
| `SCRAPE_MAX_RETRIES` | 3 | Retries per directory request |
| `MAX_WORKERS` | 5 | Parallel scraping threads |
| `BATCH_SIZE` | 100 | Companies per database batch |
| `RUN_INTERVAL_HOURS` | 6 | Interval for `--schedule` |
| `SMTP_HOST` | smtp.zoho.com | SMTP server |
| `SMTP_PORT` | 587 | 587 (STARTTLS) or 465 (SSL) |
| `SMTP_USER` / `SMTP_PASS` | — | Zoho login (app password) |
| `SMTP_FROM_NAME` / `SMTP_FROM_EMAIL` | — | Sender shown to recipients |
| `SEND_DELAY_SECONDS` | 20 | Pause between emails |
| `DAILY_SEND_LIMIT` | 400 | Max emails per `--send` run |
| `IMAP_HOST` / `IMAP_PORT` | imap.zoho.com / 993 | Inbox for `--check-inbox` |
| `IMAP_USER` / `IMAP_PASS` | SMTP login | Inbox login, if different |
| `IMAP_FOLDER` | INBOX | Folder to scan |
| `IMAP_LOOKBACK_DAYS` | 30 | How far back to scan |
| `EMAIL_TEMPLATE_PATH` | templates/email_template.html | Template file |
| `TEST_EMAIL` | — | Recipient for `--send-test` |
| `TRACK_BASE_URL` | empty | Public address of `tracker/app.py`; empty = no tracked links |
| `WEBSITE_URL` | https://thenest.am | Where clicks are redirected |
| `EMAIL_CAMPAIGN` | outreach | `utm_campaign` value |
| `TRACKER_PORT` | 8080 | Port for `tracker/app.py` |

### CLI reference

| Command | Description |
|---------|-------------|
| `python main.py [source ...]` | Scrape all or the given sources |
| `python main.py --schedule` | Scrape every `RUN_INTERVAL_HOURS` |
| `python main.py --send-test` | Send a test email to `TEST_EMAIL` |
| `python main.py --send-dry` | Preview recipients |
| `python main.py --send` | Send to pending companies |
| `python main.py --check-inbox` | Record replies, bounces, unsubscribes |
| `python main.py --stats` | Show the email funnel |
| `python from_website_email.py [options]` | Find emails on company websites |
| `python -m tracker.app` | Run the click/unsubscribe server |

### Useful queries

```sql
-- Companies that clicked or replied
SELECT company_name, email, clicked_at, replied_at
FROM companies WHERE clicked_at IS NOT NULL OR replied_at IS NOT NULL;

-- Emails found on websites (review before sending)
SELECT company_name, email, website FROM companies WHERE email_source = 'website';

-- Unverified guesses
SELECT company_name, email_guess, website FROM companies WHERE email_guess IS NOT NULL;
```
