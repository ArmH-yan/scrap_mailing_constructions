"""
Lead Generation — Main Pipeline
Scrape → Store → Export → Send Emails
"""

import sys
import io
import os
import time
import signal
from pathlib import Path
from datetime import datetime, timezone

# UTF-8 stdout support
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Load environment variables
try:
    from dotenv import load_dotenv
except ImportError:
    raise ImportError(
        "python-dotenv is not installed. Install it with: pip install python-dotenv"
    )

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"

load_dotenv(dotenv_path=ENV_PATH)

# Config
RUN_INTERVAL_HOURS = int(os.getenv("RUN_INTERVAL_HOURS", "6"))
EMAIL_TEMPLATE_PATH = os.getenv("EMAIL_TEMPLATE_PATH", "templates/email_template.html")
TEST_EMAIL = os.getenv("TEST_EMAIL", "")

from src.log import setup_logging, get_logger

from src.database import (
    get_engine,
    init_schema,
    upsert_company,
    insert_contact,
    get_summary,
    get_all_companies,
    update_email_status,
)

from src.crawler import run_source, SOURCES, create_agents, BatchBuffer, normalize_company

from src.export import (
    export_all_companies,
    generate_summary_report,
)

from src.email_sender import send_email

PAGE_LIMITS = {
    "construction_am": 38,
    "spyur_am": 20,
    "norakaruyc_am": 1,
    "myhome_am": 5,
}


def get_user_config(sources: list[str]) -> dict:
    """Prompt user for max pages per source. Returns dict of source -> max_pages."""
    config = {}
    for source in sources:
        max_allowed = PAGE_LIMITS.get(source)
        if max_allowed is None:
            continue
        try:
            ans = input(
                f"  Max pages for {source}? (1-{max_allowed}, Enter=all): "
            ).strip()
            if ans:
                val = int(ans)
                if 1 <= val <= max_allowed:
                    config[source] = val
                else:
                    print(f"    Invalid, using all {max_allowed} pages")
            else:
                print(f"    Using all {max_allowed} pages")
        except (ValueError, EOFError):
            print(f"    Using all {max_allowed} pages")
    return config


def _store_company(engine, company: dict):
    """Store a single company and its contacts in the database."""
    company_id = upsert_company(engine, company)

    if company.get("phone"):
        insert_contact(
            engine, company_id, "phone", company["phone"], company.get("source_url"),
        )

    if company.get("email"):
        insert_contact(
            engine, company_id, "email", company["email"], company.get("source_url"),
        )

    return company_id


def run_pipeline(sources: list[str] = None, config: dict = None):
    start = time.time()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    log = setup_logging(run_id)

    log.info("=" * 60)
    log.info("  LEAD GENERATION PIPELINE")
    log.info(f"  Run ID:   {run_id}")
    log.info(f"  Started:  {datetime.now(timezone.utc).isoformat()}")
    log.info("=" * 60)

    # Graceful shutdown handling
    interrupted = False

    def handle_interrupt(sig, frame):
        nonlocal interrupted
        if interrupted:
            log.warning("  Force exit.")
            sys.exit(1)
        log.warning("  Interrupt received — finishing current batch...")
        interrupted = True

    signal.signal(signal.SIGINT, handle_interrupt)
    signal.signal(signal.SIGTERM, handle_interrupt)

    # Step 1: Init database
    log.info("[1/3] Initializing database schema...")
    engine = get_engine()
    init_schema()

    # Step 2: Crawl + store per source (incremental)
    log.info("[2/3] Crawling web sources...")
    if sources is None:
        sources = list(SOURCES.keys())

    max_pages_per_source = config or {}
    all_companies = []

    agents = create_agents(5)

    for source_key in sources:
        if interrupted:
            log.warning(f"  Stopped before {source_key}")
            break
        if source_key not in SOURCES:
            log.warning(f"  Unknown source: {source_key}")
            continue

        max_pages = max_pages_per_source.get(source_key)

        def on_batch_flush(batch):
            """Callback: store every batch."""
            for c in batch:
                normalize_company(c)
                _store_company(engine, c)

        buffer = BatchBuffer(on_flush=on_batch_flush)
        companies = run_source(source_key, SOURCES[source_key], agents, buffer, max_pages=max_pages)

        # Store remaining items in buffer
        remaining = buffer.buffer[:]
        buffer.buffer.clear()
        for c in remaining:
            normalize_company(c)
            _store_company(engine, c)

        all_companies.extend(companies)
        all_companies.extend(remaining)
        log.info(f"  {source_key}: {len(companies) + len(remaining)} companies saved")

    # Export local files
    log.info("[3/3] Generating exports...")
    export_all_companies(all_companies, run_id)
    generate_summary_report(all_companies, run_id)

    # Summary
    elapsed = round(time.time() - start, 2)

    log.info("=" * 60)
    log.info(f"  PIPELINE COMPLETE in {elapsed}s")
    log.info(f"  Run ID:          {run_id}")
    log.info(f"  Total companies: {len(all_companies)}")
    if interrupted:
        log.info(f"  (Interrupted — partial results saved)")
    log.info("=" * 60)

    return all_companies


def run_email_send(dry_run: bool = False):
    """Send emails to all companies with pending status."""
    log = get_logger()

    log.info("=" * 60)
    log.info("  EMAIL SENDING")
    log.info("=" * 60)

    # Load template
    template_path = BASE_DIR / EMAIL_TEMPLATE_PATH
    if not template_path.exists():
        log.error(f"  Template not found: {template_path}")
        return

    from jinja2 import Template
    with open(template_path, "r", encoding="utf-8") as f:
        template = Template(f.read())

    # Get companies with email and pending status
    engine = get_engine()
    companies_df = get_all_companies(engine)
    companies = companies_df.to_dict("records")

    # Filter companies with email and pending status
    eligible = [
        c for c in companies
        if c.get("email") and c.get("email_status") == "pending"
    ]

    log.info(f"  Found {len(eligible)} companies with pending emails")

    if not eligible:
        log.info("  No emails to send")
        return

    # Get sender config
    from_name = os.getenv("SMTP_FROM_NAME", "")
    from_email = os.getenv("SMTP_FROM_EMAIL", "")

    if not from_name or not from_email:
        log.error("  SMTP_FROM_NAME and SMTP_FROM_EMAIL must be set in .env")
        return

    sent_count = 0
    failed_count = 0

    for company in eligible:
        company_name = company.get("company_name", "Valued Partner")

        # Render template
        html_body = template.render(
            company_name=company_name,
            sender_name=from_name,
            sender_email=from_email,
        )

        subject = f"Safety Solutions for {company_name}"

        if dry_run:
            log.info(f"  [DRY RUN] Would send to: {company.get('email')}")
            sent_count += 1
        else:
            success = send_email(
                to_email=company.get("email"),
                subject=subject,
                html_body=html_body,
            )
            if success:
                update_email_status(engine, company["id"], "sent")
                sent_count += 1
            else:
                update_email_status(engine, company["id"], "failed", "SMTP send failed")
                failed_count += 1

    log.info("=" * 60)
    log.info(f"  EMAIL COMPLETE")
    log.info(f"  Sent: {sent_count}")
    log.info(f"  Failed: {failed_count}")
    log.info("=" * 60)


def run_email_test():
    """Send a test email to the configured test address."""
    log = get_logger()

    if not TEST_EMAIL:
        log.error("  TEST_EMAIL not configured in .env")
        return

    log.info(f"  Sending test email to: {TEST_EMAIL}")

    # Load template
    template_path = BASE_DIR / EMAIL_TEMPLATE_PATH
    if not template_path.exists():
        log.error(f"  Template not found: {template_path}")
        return

    from jinja2 import Template
    with open(template_path, "r", encoding="utf-8") as f:
        template = Template(f.read())

    from_name = os.getenv("SMTP_FROM_NAME", "")
    from_email = os.getenv("SMTP_FROM_EMAIL", "")

    html_body = template.render(
        company_name="Test Company",
        sender_name=from_name,
        sender_email=from_email,
    )

    success = send_email(
        to_email=TEST_EMAIL,
        subject="Test Email - Safety Solutions",
        html_body=html_body,
    )

    if success:
        log.info("  Test email sent successfully!")
    else:
        log.error("  Failed to send test email")


def run_scheduled():
    log = get_logger()
    log.info(f"Scheduler started. Running every {RUN_INTERVAL_HOURS} hours.")
    log.info("Press Ctrl+C to stop.")

    running = True

    def handle_signal(sig, frame):
        nonlocal running
        log.info("Stopping scheduler...")
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    while running:
        try:
            run_pipeline()
        except Exception as e:
            log.error(f"Pipeline failed: {e}", exc_info=True)

        if not running:
            break

        log.info(f"Next run in {RUN_INTERVAL_HOURS} hours...")

        for _ in range(RUN_INTERVAL_HOURS * 3600):
            if not running:
                break
            time.sleep(1)


def main():
    sources = None

    if len(sys.argv) > 1:
        if sys.argv[1] == "--schedule":
            run_scheduled()
            return

        if sys.argv[1] == "--send":
            run_email_send()
            return

        if sys.argv[1] == "--send-dry":
            run_email_send(dry_run=True)
            return

        if sys.argv[1] == "--send-test":
            run_email_test()
            return

        sources = sys.argv[1:]

    # Prompt for page limits
    config = None
    if sources:
        config = get_user_config(sources)
    else:
        config = get_user_config(list(SOURCES.keys()))

    run_pipeline(sources=sources, config=config)


if __name__ == "__main__":
    main()
