"""
NEST API client for the scraper agent (python main.py --agent).

Companies, runs and website checks now live in the NEST operations platform
(nest-apps, ARCHITECTURE §26c). The agent authenticates with the outreach service
key (NEST's OUTREACH_SERVICE_KEY) in the X-Service-Key header.
"""

import os
import logging

import requests

log = logging.getLogger("leadgen")

NEST_API_URL = os.getenv("NEST_API_URL", "http://localhost:8000").rstrip("/")
NEST_SERVICE_KEY = os.getenv("NEST_SERVICE_KEY", "")
TIMEOUT = 60

# NEST's limits for one company (longer values are cut, so one odd listing can't
# make NEST refuse a whole batch)
LIMITS = {
    "company_name": 500, "email": 255, "email_source": 20, "email_guess": 255,
    "phone": 200, "director": 500, "city": 255, "website": 500, "social_url": 500,
    "source_site": 100, "source_url": 500,
}


class NestError(Exception):
    pass


class NestClient:
    def __init__(self, base_url: str = NEST_API_URL, key: str = NEST_SERVICE_KEY):
        if not key:
            raise NestError("NEST_SERVICE_KEY is not set in .env (NEST's OUTREACH_SERVICE_KEY).")
        self.base = f"{base_url}/api/v1/integrations"
        self.http = requests.Session()
        self.http.headers["X-Service-Key"] = key

    def _call(self, method: str, path: str, **kwargs):
        resp = self.http.request(method, f"{self.base}{path}", timeout=TIMEOUT, **kwargs)
        if resp.status_code == 204:
            return None
        if resp.status_code >= 400:
            raise NestError(f"{method} {path}: {resp.status_code} {resp.text[:300]}")
        return resp.json()

    # --- runs ---------------------------------------------------------------

    def claim(self) -> dict | None:
        """The next requested run (now RUNNING), or None."""
        return self._call("POST", "/scrape-runs/claim")

    def progress(self, run_id: int, found: int, saved: int, message: str = "") -> bool:
        """Reports progress; returns True when the run was cancelled in NEST."""
        body = {"found": found, "saved": saved, "message": message[:500] or None}
        return bool(self._call("POST", f"/scrape-runs/{run_id}/progress", json=body)["cancelled"])

    def finish(self, run_id: int, found: int, saved: int, error: str | None = None, message: str = ""):
        body = {
            "status": "FAILED" if error else "DONE",
            "found": found,
            "saved": saved,
            "message": message[:500] or None,
            "error": (error or "")[:4000] or None,
        }
        self._call("POST", f"/scrape-runs/{run_id}/finish", json=body)

    # --- companies ----------------------------------------------------------

    def import_companies(self, companies: list[dict]) -> dict:
        """Sends found companies (NEST deduplicates). Returns {created, updated}."""
        clean = []
        for c in companies:
            name = (c.get("company_name") or "").strip()
            if not name:
                continue
            item = {k: (str(c[k]).strip()[:n] or None) for k, n in LIMITS.items() if c.get(k)}
            item["company_name"] = name[: LIMITS["company_name"]]
            clean.append(item)
        totals = {"created": 0, "updated": 0}
        for i in range(0, len(clean), 500):
            r = self._call("POST", "/outreach/companies", json={"companies": clean[i:i + 500]})
            totals["created"] += r["created"]
            totals["updated"] += r["updated"]
        return totals

    def website_candidates(self, limit: int, recheck_days: int, include_dead: bool) -> list[dict]:
        params = {"limit": limit, "recheck_days": recheck_days, "include_dead": str(include_dead).lower()}
        return self._call("GET", "/outreach/website-candidates", params=params)

    def website_results(self, results: list[dict]) -> dict:
        totals = {"saved": 0, "emails_found": 0}
        for i in range(0, len(results), 500):
            r = self._call("POST", "/outreach/website-results", json={"results": results[i:i + 500]})
            totals["saved"] += r["saved"]
            totals["emails_found"] += r["emails_found"]
        return totals
