"""
Lead Generation Scraper — Export
CSV and XLSX export for leads.
"""

import os
import csv
import logging
import pandas as pd
from datetime import datetime, timezone

log = logging.getLogger("leadgen")


EXPORT_DIR = "data/exports"


def ensure_export_dir():
    os.makedirs(EXPORT_DIR, exist_ok=True)


def export_all_companies(companies: list[dict], timestamp: str = None) -> str:
    ensure_export_dir()
    if not timestamp:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    filepath = os.path.join(EXPORT_DIR, f"companies_{timestamp}.csv")
    columns = [
        "company_name", "website", "phone", "email", "address", "city",
        "district", "director", "founded_year", "employee_count", "ownership_type",
        "gps_lat", "gps_lon",
        "facebook_url", "instagram_url", "linkedin_url",
        "company_category", "company_description", "services",
        "contact_page_url", "source_url", "has_active_projects",
        "project_count", "project_names", "email_status", "source_site"
    ]

    _write_csv(filepath, companies, columns)
    log.info(f"  [OK] Exported {len(companies)} companies to {filepath}")
    return filepath


def generate_summary_report(companies: list[dict], timestamp: str = None) -> str:
    ensure_export_dir()
    if not timestamp:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    total = len(companies)
    if total == 0:
        log.warning("  No companies to report on")
        return ""

    report_path = os.path.join(EXPORT_DIR, f"summary_report_{timestamp}.txt")

    with_website = sum(1 for c in companies if c.get("website"))
    with_email = sum(1 for c in companies if c.get("email"))
    with_phone = sum(1 for c in companies if c.get("phone"))
    with_projects = sum(1 for c in companies if c.get("has_active_projects"))
    emails_sent = sum(1 for c in companies if c.get("email_status") == "sent")
    emails_failed = sum(1 for c in companies if c.get("email_status") == "failed")
    emails_pending = sum(1 for c in companies if c.get("email_status") == "pending")

    report = f"""
====================================================
LEAD GENERATION SUMMARY REPORT
Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC
====================================================

OVERVIEW
--------
Total companies found:      {total}
Companies with website:     {with_website} ({with_website/total*100:.1f}%)
Companies with email:       {with_email} ({with_email/total*100:.1f}%)
Companies with phone:       {with_phone} ({with_phone/total*100:.1f}%)
Companies with projects:    {with_projects} ({with_projects/total*100:.1f}%)

EMAIL STATUS
------------
Emails sent:                {emails_sent}
Emails failed:              {emails_failed}
Emails pending:             {emails_pending}

====================================================
"""

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    log.info(f"  Summary report: {report_path}")
    return report_path


def _write_csv(filepath: str, data: list[dict], columns: list[str]):
    if not data:
        return
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data)
