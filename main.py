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

from src.email_sender import send_email, EmailSender, DAILY_SEND_LIMIT, tracking_links

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

        # run_source already returns every company, including those left in the buffer
        all_companies.extend(companies)
        log.info(f"  {source_key}: {len(companies)} companies saved")

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
    if len(eligible) > DAILY_SEND_LIMIT:
        log.info(f"  Capping this run at DAILY_SEND_LIMIT={DAILY_SEND_LIMIT}")
        eligible = eligible[:DAILY_SEND_LIMIT]

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

    sender = EmailSender() if not dry_run else None
    if sender and sender.__enter__().server is None:
        log.error("  Could not connect to SMTP — aborting")
        return

    try:
        for company in eligible:
            company_name = company.get("company_name", "Valued Partner")

            links = tracking_links(company.get("tracking_token"))

            # Render template
            html_body = template.render(
                company_name=company_name,
                sender_name=from_name,
                sender_email=from_email,
                **links,
            )

            subject = f"Safety Solutions for {company_name}"

            if dry_run:
                link_note = f" (link: {links['website_url']})" if links["website_url"] else ""
                log.info(f"  [DRY RUN] Would send to: {company.get('email')}{link_note}")
                sent_count += 1
                continue

            message_id = sender.send(company.get("email"), subject, html_body,
                                     unsubscribe_url=links["unsubscribe_url"])
            if message_id:
                update_email_status(engine, company["id"], "sent", message_id=message_id)
                sent_count += 1
            else:
                update_email_status(engine, company["id"], "failed", "SMTP send failed")
                failed_count += 1
    finally:
        if sender:
            sender.close()

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
        **tracking_links("test"),
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


def run_inbox_check():
    """Record replies, bounces and unsubscribe replies from the Zoho inbox."""
    from src.inbox import check_inbox

    log = get_logger()
    log.info("=" * 60)
    log.info("  INBOX CHECK")
    log.info("=" * 60)

    stats = check_inbox(get_engine())

    log.info(f"  Scanned: {stats['scanned']}  New replies: {stats['replies']}  "
             f"Bounces: {stats['bounces']}  Unsubscribes: {stats['unsubscribes']}")


def run_stats():
    """Print email funnel counts."""
    log = get_logger()
    s = get_summary(get_engine())
    sent = s.get("emails_sent", 0) + s.get("emails_bounced", 0) + s.get("unsubscribed", 0)

    def pct(n):
        return f"{n / sent * 100:.1f}%" if sent else "-"

    log.info("=" * 60)
    log.info("  EMAIL STATS")
    log.info("=" * 60)
    log.info(f"  Companies:     {s.get('total_companies', 0)}  (with email: {s.get('with_email', 0)})")
    log.info(f"  Pending:       {s.get('emails_pending', 0)}")
    log.info(f"  Sent:          {sent}")
    log.info(f"  Clicked:       {s.get('emails_clicked', 0)}  ({pct(s.get('emails_clicked', 0))})")
    log.info(f"  Replied:       {s.get('emails_replied', 0)}  ({pct(s.get('emails_replied', 0))})")
    log.info(f"  Bounced:       {s.get('emails_bounced', 0)}  ({pct(s.get('emails_bounced', 0))})")
    log.info(f"  Unsubscribed:  {s.get('unsubscribed', 0)}")
    log.info(f"  Failed:        {s.get('emails_failed', 0)}")


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

        if sys.argv[1] == "--check-inbox":
            run_inbox_check()
            return

        if sys.argv[1] == "--stats":
            run_stats()
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
