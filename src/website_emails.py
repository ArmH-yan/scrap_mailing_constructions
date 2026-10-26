"""
Lead Generation — Website email finder
Visits a company website and returns the best contact email.

    1. Resolve the site: fix typos, try https/http and with/without www, fail fast
    2. Fetch the homepage, then the site's own contact/about links (en/hy/ru)
    3. Extract emails: mailto, Cloudflare-protected, obfuscated, scripts, plain text
    4. Drop junk (images, Sentry, placeholders) and rank the rest:
       site's own domain > free mail providers > other domains
    5. Optional browser pass (Playwright) for JS-only or blocking sites
    6. If nothing is found, a guess (info@domain) is returned separately —
       never as the real email
"""

import re
import time
import logging
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("leadgen")

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = (5, 10)          # (connect, read) seconds
MAX_PAGES = 5              # homepage + up to 4 contact/about pages
MIN_TEXT_CHARS = 300       # less visible text than this = probably rendered by JavaScript

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,12}")

FREE_PROVIDERS = {
    "gmail.com", "mail.ru", "yandex.ru", "yandex.com", "ya.ru", "yahoo.com", "outlook.com",
    "hotmail.com", "live.com", "icloud.com", "inbox.ru", "bk.ru", "list.ru", "rambler.ru",
    "googlemail.com", "proton.me", "protonmail.com",
}
JUNK_DOMAINS = (
    "example.", "domain.com", "yourdomain", "mysite.com", "email.com", "test.com",
    "sentry", "wixpress.com", "w3.org", "schema.org", "cloudflare", "godaddy",
    "jquery", "bootstrap", "googleapis", "gstatic", "placeholder",
)
JUNK_LOCAL_PREFIXES = (
    "noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "postmaster",
    "hostmaster", "webmaster", "abuse", "spam", "unsubscribe", "bounce", "example",
    "wordpress", "cpanel", "plesk", "yourname", "youremail",
)
JUNK_LOCAL_EXACT = {"name", "user", "email", "your", "username", "test"}
FILE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".js", ".css", ".pdf")
ROLE_PRIORITY = ("info", "office", "contact", "sales", "hello", "mail", "director", "admin")

# Link text / URL fragments that point to contact or about pages (en / hy / ru)
CONTACT_WORDS = ("contact", "kontakt", "/kap/", "/kap-", "կապ", "կոնտակտ", "контакт", "связ")
ABOUT_WORDS = ("about", "մեր մասին", "о нас", "о компании", "company")
FALLBACK_PATHS = ("/contact", "/contacts", "/contact-us", "/about")

SOCIAL_HOSTS = ("facebook.com", "fb.com", "instagram.com", "youtube.com", "youtu.be",
                "linkedin.com", "t.me", "telegram.me", "vk.com", "ok.ru", "tiktok.com",
                "twitter.com", "x.com")


@dataclass
class SiteResult:
    status: str = "dead"            # found | no_email | dead | blocked
    email: str = ""
    emails: list = field(default_factory=list)
    phones: set = field(default_factory=set)
    guess: str = ""
    final_url: str = ""
    used_browser: bool = False
    needs_browser: bool = False
    error: str = ""


# === Helpers ===

def is_social_url(url: str) -> bool:
    host = (urlparse(url if "//" in url else "https://" + url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in SOCIAL_HOSTS)


def site_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _same_site(email_domain: str, domain: str) -> bool:
    if not domain:
        return False
    return (email_domain == domain or email_domain.endswith("." + domain)
            or domain.endswith("." + email_domain))


def decode_cfemail(hex_str: str) -> str:
    """Decode Cloudflare's email protection (XOR with the first byte)."""
    try:
        key = int(hex_str[:2], 16)
        return "".join(chr(int(hex_str[i:i + 2], 16) ^ key) for i in range(2, len(hex_str), 2))
    except ValueError:
        return ""


def _deobfuscate(text: str) -> str:
    text = re.sub(r"&#0*64;|&commat;", "@", text)
    text = re.sub(r"&#0*46;", ".", text)
    text = re.sub(r"\s*[\[\(\{]\s*at\s*[\]\)\}]\s*", "@", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*[\[\(\{]\s*dot\s*[\]\)\}]\s*", ".", text, flags=re.IGNORECASE)
    return text


def clean_email(email: str) -> str:
    """Lowercase and validate; returns '' for junk."""
    email = email.strip().strip(".,;:").lower()
    if email.startswith("mailto:"):
        email = email[7:]
    email = email.split("?")[0]
    if not re.fullmatch(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,12}", email):
        return ""
    local, domain = email.split("@", 1)
    if email.endswith(FILE_EXTENSIONS) or re.search(r"@\dx\.", email):
        return ""
    if any(j in domain for j in JUNK_DOMAINS):
        return ""
    if local in JUNK_LOCAL_EXACT or local.startswith(JUNK_LOCAL_PREFIXES):
        return ""
    if re.fullmatch(r"[0-9a-f]{16,}", local):   # hashed ids (e.g. Sentry keys)
        return ""
    return email


def extract_emails(html: str, soup: BeautifulSoup) -> dict:
    """Return {email: found_in_mailto} for every email on the page."""
    found = {}

    def add(raw, mailto=False):
        email = clean_email(raw)
        if email:
            found[email] = found.get(email, False) or mailto

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().startswith("mailto:"):
            for part in href[7:].split("?")[0].split(","):
                add(part, mailto=True)
        elif "/cdn-cgi/l/email-protection#" in href:
            add(decode_cfemail(href.split("#", 1)[1]), mailto=True)
    for el in soup.select("[data-cfemail]"):
        add(decode_cfemail(el["data-cfemail"]))

    for email in EMAIL_RE.findall(_deobfuscate(html)):
        add(email)
    return found


def rank_email(email: str, domain: str, mailto: bool) -> tuple:
    local, email_domain = email.split("@", 1)
    if _same_site(email_domain, domain):
        tier = 0
    elif email_domain in FREE_PROVIDERS:
        tier = 1
    else:
        tier = 2
    role = next((i for i, r in enumerate(ROLE_PRIORITY) if local.startswith(r)), len(ROLE_PRIORITY))
    return (tier, not mailto, role, len(email))


def pick_best(found: dict, domain: str) -> list[str]:
    """Emails sorted best-first."""
    return sorted(found, key=lambda e: rank_email(e, domain, found[e]))


def find_contact_links(soup: BeautifulSoup, page_url: str, limit: int = MAX_PAGES - 1) -> list[str]:
    """Same-site links whose URL or text looks like a contact/about page; contact first."""
    host = site_domain(page_url)
    scored = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = urldefrag(urljoin(page_url, href))[0]
        if site_domain(url) != host or url.rstrip("/") == page_url.rstrip("/"):
            continue
        haystack = f"{urlparse(url).path.rstrip('/')}/ {a.get_text(' ', strip=True)}".lower()
        if any(w in haystack for w in CONTACT_WORDS):
            scored[url] = min(scored.get(url, 9), 0)
        elif any(w in haystack for w in ABOUT_WORDS):
            scored[url] = min(scored.get(url, 9), 1)
    return sorted(scored, key=scored.get)[:limit]


def guess_email(domain: str) -> str:
    """info@domain if the domain receives mail (MX record). Unverified — store separately."""
    try:
        import dns.resolver
        if dns.resolver.resolve(domain, "MX", lifetime=5):
            return f"info@{domain}"
    except Exception:
        pass
    return ""


# === Fetching ===

def _candidate_urls(website: str) -> list[str]:
    website = website.strip()
    website = re.sub(r"^(https?://)?w{3,}\.", r"\1www.", website)     # "wwww." typos
    if "//" not in website:
        website = "https://" + website
    parsed = urlparse(website)
    host = parsed.hostname or ""
    bare = host[4:] if host.startswith("www.") else host
    path = parsed.path or "/"
    hosts = [host] + [h for h in (bare, "www." + bare) if h != host]
    urls = []
    for scheme in ("https", "http"):
        for h in hosts:
            urls.append(f"{scheme}://{h}{path}")
    return urls


def _visible_text_len(soup: BeautifulSoup) -> int:
    for el in soup(["script", "style", "noscript"]):
        el.decompose()
    return len(soup.get_text(" ", strip=True))


def _scan_page(html: str, url: str, result: SiteResult, found: dict) -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")
    for email, mailto in extract_emails(html, soup).items():
        found[email] = found.get(email, False) or mailto
    for a in soup.find_all("a", href=True):
        if a["href"].startswith("tel:"):
            phone = a["href"][4:].strip()
            if len(re.sub(r"\D", "", phone)) >= 6:
                result.phones.add(phone)
    return soup


def _good_enough(found: dict, domain: str) -> bool:
    return any(rank_email(e, domain, m)[0] <= 1 for e, m in found.items())


def check_website(website: str, session: requests.Session | None = None) -> SiteResult:
    """HTTP pass. Sets needs_browser when a rendered page might do better."""
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", USER_AGENT)
    result = SiteResult()

    home_html, home_url, blocked = None, "", False
    for url in _candidate_urls(website):
        try:
            resp = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        except requests.RequestException as e:
            result.error = type(e).__name__
            continue
        if resp.status_code in (401, 403, 429, 503):
            blocked, home_url = True, resp.url
            break
        if resp.ok and "html" in resp.headers.get("Content-Type", "html"):
            home_html, home_url = resp.text, resp.url
            break

    if home_html is None:
        result.status = "blocked" if blocked else "dead"
        result.final_url = home_url
        result.needs_browser = blocked
        return result

    result.final_url = home_url
    domain = site_domain(home_url)
    found = {}
    soup = _scan_page(home_html, home_url, result, found)
    js_only = _visible_text_len(BeautifulSoup(home_html, "html.parser")) < MIN_TEXT_CHARS

    pages = find_contact_links(soup, home_url) or [urljoin(home_url, p) for p in FALLBACK_PATHS]
    for url in pages:
        if _good_enough(found, domain):
            break
        try:
            resp = session.get(url, timeout=TIMEOUT)
            if resp.ok:
                _scan_page(resp.text, resp.url, result, found)
        except requests.RequestException:
            pass
        time.sleep(0.2)

    result.emails = pick_best(found, domain)
    result.email = result.emails[0] if result.emails else ""
    result.status = "found" if result.email else "no_email"
    result.needs_browser = not result.email and (js_only or not pages)
    return result


class BrowserFetcher:
    """Playwright-rendered pages for JS-only or blocking sites. Not thread-safe: use from one thread."""

    def __init__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._context = self._browser.new_context(user_agent=USER_AGENT)

    def get(self, url: str) -> tuple[str, str] | None:
        page = self._context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            return page.content(), page.url
        except Exception:
            return None
        finally:
            page.close()

    def close(self):
        self._browser.close()
        self._pw.stop()


def check_website_browser(website: str, fetcher: BrowserFetcher, previous: SiteResult) -> SiteResult:
    """Browser pass: render the homepage and its contact links."""
    result = previous
    start_url = previous.final_url or _candidate_urls(website)[0]
    page = fetcher.get(start_url)
    if not page:
        return result

    html, url = page
    result.used_browser = True
    result.final_url = url
    domain = site_domain(url)
    found = {e: True for e in result.emails}
    soup = _scan_page(html, url, result, found)
    for link in find_contact_links(soup, url, limit=3):
        if _good_enough(found, domain):
            break
        page = fetcher.get(link)
        if page:
            _scan_page(page[0], page[1], result, found)

    result.emails = pick_best(found, domain)
    result.email = result.emails[0] if result.emails else ""
    result.status = "found" if result.email else "no_email"
    return result
