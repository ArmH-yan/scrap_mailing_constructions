"""
Email link tracker — counts clicks and handles unsubscribes.

Routes (token = companies.tracking_token):
    GET  /r/<token>   log a click, redirect to WEBSITE_URL tagged with utm_source=email
    GET  /u/<token>   unsubscribe confirmation page
    POST /u/<token>   unsubscribe (also used by Gmail/Outlook one-click unsubscribe)
    GET  /health

Run locally:
    python -m tracker.app
Deploy behind https on a subdomain (e.g. go.thenest.am) and set TRACK_BASE_URL to it.
"""

import os
import sys
from pathlib import Path

from flask import Flask, redirect, request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import get_engine, find_company_by_token, record_email_event
from src.email_sender import utm_url

WEBSITE_URL = os.getenv("WEBSITE_URL", "https://thenest.am")

app = Flask(__name__)
engine = get_engine()

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>The Nest</title>
<style>body{{font-family:system-ui,sans-serif;max-width:480px;margin:15vh auto;padding:0 16px;color:#222}}
button{{padding:10px 18px;font-size:16px;cursor:pointer}}</style></head>
<body>{body}</body></html>"""


@app.get("/r/<token>")
def click(token):
    company = find_company_by_token(engine, token)
    if company:
        record_email_event(engine, company["id"], "click", request.headers.get("User-Agent", "")[:300])
    return redirect(utm_url(WEBSITE_URL, token if company else ""), code=302)


@app.get("/u/<token>")
def unsubscribe_page(token):
    # A confirm button, not an instant unsubscribe: mail scanners open every link in an email
    return PAGE.format(body="""
        <h2>Unsubscribe</h2>
        <p>Stop receiving emails from The Nest?</p>
        <form method="post"><button type="submit">Unsubscribe</button></form>""")


@app.post("/u/<token>")
def unsubscribe(token):
    company = find_company_by_token(engine, token)
    if company and company.get("email_status") != "unsubscribed":
        record_email_event(engine, company["id"], "unsubscribe", "link")
    return PAGE.format(body="<h2>You're unsubscribed</h2><p>You won't receive further emails from us.</p>")


@app.get("/health")
def health():
    return "ok"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("TRACKER_PORT", "8080")))
