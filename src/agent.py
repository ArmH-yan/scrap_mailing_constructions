"""
Scraper agent: runs the scrapes requested in the NEST manager app
(Business → Mailing → Scraping) and sends the results to NEST.

    python main.py --agent

It asks NEST for work every AGENT_POLL_SECONDS, runs one job at a time, reports
progress (which also tells it when a manager cancelled the job) and stops cleanly
with Ctrl+C. Companies go straight to NEST, which deduplicates them; nothing is
stored in the local PostgreSQL.

Jobs:
    SCRAPE          the directory scrapers (src/crawler.py) for the chosen sources
    WEBSITE_EMAILS  the website email finder (from_website_email.py) for companies
                    NEST has without an email
"""

import os
import time
import signal
import logging

from src.crawler import SOURCES, BatchBuffer, create_agents, normalize_company, run_source
from src.nest_client import NestClient, NestError

log = logging.getLogger("leadgen")

POLL_SECONDS = int(os.getenv("AGENT_POLL_SECONDS", "30"))


class Cancelled(Exception):
    """The run was cancelled in NEST, or the agent is stopping."""


class Agent:
    def __init__(self, client: NestClient):
        self.client = client
        self.stopping = False

    # --- one job ------------------------------------------------------------

    def run_scrape(self, run: dict) -> tuple[int, int]:
        found = saved = 0
        sources = run.get("sources") or list(SOURCES)
        max_pages = (run.get("options") or {}).get("max_pages") or {}
        agents = create_agents(5)

        for source_key in sources:
            if source_key not in SOURCES:
                log.warning(f"  Unknown source: {source_key}")
                continue

            def send(batch: list[dict], source_key: str = source_key):
                nonlocal found, saved
                for c in batch:
                    normalize_company(c)
                result = self.client.import_companies(batch)
                found += len(batch)
                saved += result["created"]
                if self.stopping or self.client.progress(
                    run["id"], found, saved, f"{source_key}: {found} found, {saved} new"
                ):
                    raise Cancelled()

            buffer = BatchBuffer(on_flush=send)
            run_source(source_key, SOURCES[source_key], agents, buffer, max_pages=max_pages.get(source_key))
            buffer.flush()  # what's left after the last full batch
        return found, saved

    def run_website_emails(self, run: dict) -> tuple[int, int]:
        from from_website_email import check_companies  # imports Playwright helpers lazily

        options = run.get("options") or {}
        candidates = self.client.website_candidates(
            limit=options.get("limit") or 500,
            recheck_days=int(options.get("recheck_days", 30)),
            include_dead=bool(options.get("include_dead")),
        )
        companies = [{"id": c["id"], "company_name": c["company_name"], "website": c["website"]}
                     for c in candidates]
        if self.client.progress(run["id"], 0, 0, f"{len(companies)} websites to check"):
            raise Cancelled()

        checked = emails = 0
        for i in range(0, len(companies), 50):
            chunk = companies[i:i + 50]
            results = check_companies(chunk, workers=5, browser=True)
            payload = [{
                "company_id": company["id"],
                "status": result.status,
                "email": result.email or None,
                "emails": list(result.emails)[:10],
                "guess": result.guess or None,
                "final_url": result.final_url or None,
            } for company, result in results]
            saved = self.client.website_results(payload)
            checked += len(chunk)
            emails += saved["emails_found"]
            if self.stopping or self.client.progress(
                run["id"], checked, emails, f"{checked}/{len(companies)} websites checked"
            ):
                raise Cancelled()
        return checked, emails

    def run(self, run: dict):
        kind = run["kind"]
        log.info(f"  Run #{run['id']}: {kind} {', '.join(run.get('sources') or [])}")
        try:
            found, saved = (self.run_scrape(run) if kind == "SCRAPE" else self.run_website_emails(run))
        except Cancelled:
            log.info(f"  Run #{run['id']} cancelled")
            return
        except Exception as e:
            log.exception(f"  Run #{run['id']} failed")
            try:
                self.client.finish(run["id"], 0, 0, error=f"{type(e).__name__}: {e}")
            except NestError as report:
                log.error(f"  Could not report the failure: {report}")
            return
        self.client.finish(run["id"], found, saved, message=f"{found} found, {saved} saved")
        log.info(f"  Run #{run['id']} done: {found} found, {saved} saved")

    # --- the loop -----------------------------------------------------------

    def forever(self):
        log.info(f"  Agent started: asking NEST for work every {POLL_SECONDS}s (Ctrl+C to stop)")
        while not self.stopping:
            try:
                run = self.client.claim()
            except (NestError, OSError) as e:
                log.warning(f"  NEST unreachable: {e}")
                run = None
            if run:
                self.run(run)
                continue
            for _ in range(POLL_SECONDS):
                if self.stopping:
                    break
                time.sleep(1)
        log.info("  Agent stopped")


def run_agent():
    agent = Agent(NestClient())

    def stop(sig, frame):
        if agent.stopping:
            raise SystemExit(1)
        log.warning("  Stopping after the current batch... (Ctrl+C again to force)")
        agent.stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    agent.forever()
