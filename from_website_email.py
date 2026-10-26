"""
from_website_email.py — Scrape emails from company websites.

Finds companies in the DB that have a website but no email,
visits each website, scrapes the email, and updates the DB.

Usage:
    python from_website_email.py               # process all eligible companies
    python from_website_email.py --dry-run     # show what would be updated (no DB writes)
    python from_website_email.py --limit 50    # process max 50 companies
"""

import sys
import io
import os
import time
import argparse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from src.database import (
    get_engine,
    get_companies_without_email,
    update_company_email,
)
from src.crawler import (
    create_agents,
    crawl_company_website,
    normalize_email,
    _discover_email_from_domain,
)


def main():
    parser = argparse.ArgumentParser(description="Scrape emails from company websites")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be updated without writing to DB")
    parser.add_argument("--limit", type=int, default=None, help="Max companies to process")
    args = parser.parse_args()

    engine = get_engine()

    # Get companies with website but no email
    df = get_companies_without_email(engine)
    companies = df.to_dict("records")

    if args.limit:
        companies = companies[:args.limit]

    print("=" * 60)
    print("  FROM WEBSITE EMAIL SCRAPER")
    print("=" * 60)
    print(f"  Companies with website but no email: {len(companies)}")
    print(f"  Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print("=" * 60)

    if not companies:
        print("  Nothing to do.")
        return

    agents = create_agents(3)

    found = 0
    not_found = 0
    errors = 0

    for i, company in enumerate(companies, 1):
        company_name = company.get("company_name", "?")
        website = company.get("website", "")
        company_id = company.get("id")

        if not website:
            not_found += 1
            continue

        # Normalize URL
        if not website.startswith(("http://", "https://")):
            website = "https://" + website

        email = ""
        try:
            agent = agents[i % len(agents)]
            web_data = crawl_company_website(agent, website)

            # Try direct extraction first
            if web_data.get("email"):
                email = normalize_email(web_data["email"])

            # Fallback: DNS MX discovery
            if not email:
                from urllib.parse import urlparse
                parsed = urlparse(website)
                domain = parsed.hostname
                if domain:
                    discovered = _discover_email_from_domain(domain)
                    if discovered:
                        email = normalize_email(discovered)

        except Exception as e:
            errors += 1
            print(f"  [{i}/{len(companies)}] ERROR: {company_name} — {e}")
            continue

        if email:
            found += 1
            if args.dry_run:
                print(f"  [{i}/{len(companies)}] [DRY RUN] {company_name} -> {email}")
            else:
                update_company_email(engine, company_id, email)
                print(f"  [{i}/{len(companies)}] [OK] {company_name} -> {email}")
        else:
            not_found += 1
            if i % 10 == 0 or i == len(companies):
                print(f"  [{i}/{len(companies)}] No email found for {company_name}")

        time.sleep(0.3)

    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print(f"  Total processed:  {len(companies)}")
    print(f"  Emails found:     {found}")
    print(f"  Not found:        {not_found}")
    print(f"  Errors:           {errors}")
    if args.dry_run:
        print("  (DRY RUN — no changes written to DB)")
    print("=" * 60)


if __name__ == "__main__":
    main()
