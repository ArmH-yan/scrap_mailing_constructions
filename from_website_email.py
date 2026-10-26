"""
from_website_email.py — Scrape emails from company websites.

Finds companies in the DB that have a website but no email, visits each
website, scrapes the email, and updates the DB.

    1. HTTP pass, several sites in parallel (--workers)
    2. Browser pass (Playwright) for JS-only or blocking sites that found nothing
    3. Still nothing: store an unverified info@domain guess in email_guess
       (never in email, so it is never sent automatically)

Every site check is recorded (website_status, website_checked_at), so later
runs skip sites checked in the last --recheck-days and sites found dead.

Usage:
    python from_website_email.py                   # process all eligible companies
    python from_website_email.py --dry-run         # show what would be updated (no DB writes)
    python from_website_email.py --limit 50        # process max 50 companies
    python from_website_email.py --workers 8       # parallel HTTP checks (default 5)
    python from_website_email.py --no-browser      # skip the Playwright pass
    python from_website_email.py --recheck-days 0  # recheck everything, ignoring past checks
    python from_website_email.py --include-dead    # also retry sites found dead/blocked
"""

import sys
import io
import time
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import requests

from src.database import get_engine, get_companies_without_email, save_website_check
from src.website_emails import (
    USER_AGENT,
    BrowserFetcher,
    SiteResult,
    check_website,
    check_website_browser,
    guess_email,
    site_domain,
)

_local = threading.local()


def _session() -> requests.Session:
    """One HTTP session per worker thread."""
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
        _local.session.headers["User-Agent"] = USER_AGENT
    return _local.session


def _http_check(company: dict):
    try:
        return company, check_website(company["website"], session=_session())
    except Exception as e:
        return company, SiteResult(status="dead", error=f"{type(e).__name__}: {e}")


def main():
    parser = argparse.ArgumentParser(description="Scrape emails from company websites")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be updated without writing to DB")
    parser.add_argument("--limit", type=int, default=None, help="Max companies to process")
    parser.add_argument("--workers", type=int, default=5, help="Parallel HTTP checks")
    parser.add_argument("--no-browser", action="store_true", help="Skip the Playwright pass")
    parser.add_argument("--recheck-days", type=int, default=30, help="Skip sites checked within this many days")
    parser.add_argument("--include-dead", action="store_true", help="Also retry sites found dead or blocked")
    args = parser.parse_args()

    start = time.time()
    engine = get_engine()

    df = get_companies_without_email(engine, recheck_days=args.recheck_days,
                                     include_dead=args.include_dead)
    companies = df.to_dict("records")
    if args.limit:
        companies = companies[:args.limit]

    print("=" * 60)
    print("  FROM WEBSITE EMAIL SCRAPER")
    print("=" * 60)
    print(f"  Companies to check: {len(companies)}")
    print(f"  Mode: {'DRY RUN' if args.dry_run else 'LIVE'}   Workers: {args.workers}   "
          f"Browser: {'off' if args.no_browser else 'on'}")
    print("=" * 60)

    if not companies:
        print("  Nothing to do.")
        return

    # 1. HTTP pass (parallel)
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(_http_check, c) for c in companies]
        for i, future in enumerate(as_completed(futures), 1):
            company, result = future.result()
            results.append((company, result))
            note = result.email or result.status + (f" ({result.error})" if result.error and result.status == "dead" else "")
            print(f"  [{i}/{len(companies)}] {company['company_name'][:45]:45} {note}")

    # 2. Browser pass (one browser, sequential — Playwright sync API is single-threaded)
    to_render = [(c, r) for c, r in results if r.needs_browser]
    if to_render and not args.no_browser:
        print(f"\n  Browser pass: {len(to_render)} sites")
        try:
            fetcher = BrowserFetcher()
        except Exception as e:
            print(f"  Playwright unavailable ({e}) — skipping browser pass")
            fetcher = None
        if fetcher:
            try:
                for company, result in to_render:
                    check_website_browser(company["website"], fetcher, result)
                    print(f"  [browser] {company['company_name'][:45]:45} {result.email or result.status}")
            finally:
                fetcher.close()

    # 3. Guesses for live sites with no email, then save
    counts = {"found": 0, "no_email": 0, "dead": 0, "blocked": 0}
    guesses = 0
    for company, result in results:
        if result.status == "no_email":
            domain = site_domain(result.final_url or company["website"])
            result.guess = guess_email(domain) if domain else ""
            guesses += bool(result.guess)
        counts[result.status] = counts.get(result.status, 0) + 1
        if not args.dry_run:
            save_website_check(engine, company["id"], result.status, result.email,
                               result.emails, result.guess, result.final_url)

    found = [(c, r) for c, r in results if r.email]
    if found:
        print("\n  Emails found:")
        for company, result in found:
            extra = f"  (+{len(result.emails) - 1} more)" if len(result.emails) > 1 else ""
            via = " [browser]" if result.used_browser else ""
            print(f"    {company['company_name'][:40]:40} -> {result.email}{extra}{via}")

    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print(f"  Checked:          {len(results)}")
    print(f"  Emails found:     {counts['found']}")
    print(f"  No email on site: {counts['no_email']}  (guesses stored: {guesses})")
    print(f"  Dead sites:       {counts['dead']}")
    print(f"  Blocked:          {counts['blocked']}")
    print(f"  Time:             {time.time() - start:.0f}s")
    if args.dry_run:
        print("  (DRY RUN — no changes written to DB)")
    print("=" * 60)


if __name__ == "__main__":
    main()
