"""
Lead Generation Scraper — Crawler
Multi-source parallel scraper: Playwright (JS) + BS4 (static)
"""

import time
import random
import re
import hashlib
import logging
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
import requests

log = logging.getLogger("leadgen")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import os

SCRAPE_DELAY = float(os.getenv("SCRAPE_DELAY", "0.5"))
SCRAPE_TIMEOUT = int(os.getenv("SCRAPE_TIMEOUT", "15"))
SCRAPE_MAX_RETRIES = int(os.getenv("SCRAPE_MAX_RETRIES", "3"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "5"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "100"))

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
]

# Source definitions
SOURCES = {
    "construction_am": {
        "base_url": "https://www.construction.am",
        "listing_pages": ["/developers.php", "/construction.php", "/suppliers.php"],
        "type": "static",
        "letter_pagination": True,
    },
    "spyur_am": {
        "base_url": "https://www.spyur.am",
        "listing_pages": ["/en/home/advanced_search/?search=1&products_and_services=1&yp_cat3=375"],
        "type": "static",
        "letter_pagination": False,
        "page_pagination": True,
    },
    "norakaruyc_am": {
        "base_url": "https://api.norakaruyc.am",
        "listing_pages": [],
        "type": "api",
        "letter_pagination": False,
    },
    "myhome_am": {
        "base_url": "https://myhome.am",
        "listing_pages": [],
        "type": "api_rest",
        "letter_pagination": False,
    },
}


# === Normalization (moved from scoring.py) ===

def normalize_phone(phone: str) -> str:
    if not phone:
        return ""
    cleaned = re.sub(r'[^\d+\-\(\)\s]', '', phone.strip())
    if cleaned.startswith("374") and not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    return cleaned


def normalize_email(email: str) -> str:
    if not email:
        return ""
    email = email.strip().lower()
    junk_prefixes = [
        "noreply", "no-reply", "donotreply", "do-not-reply",
        "mailer-daemon", "postmaster", "hostmaster", "webmaster",
        "abuse", "spam", "unsubscribe", "bounce",
        "test", "example", "admin@example",
        "wordpress", "cpanel", "plesk",
    ]
    local_part = email.split("@")[0] if "@" in email else ""
    if any(junk in local_part for junk in junk_prefixes):
        return ""
    if re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,12}$', email):
        return email
    return ""


def normalize_company(company: dict) -> dict:
    company["phone"] = normalize_phone(company.get("phone", ""))
    company["email"] = normalize_email(company.get("email", ""))
    company.pop("_all_text", None)
    return company


# === Core infrastructure ===

def compute_content_hash(name: str, phone: str, url: str) -> str:
    raw = f"{name}|{phone}|{url}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


class ScrapingAgent:
    def __init__(self, agent_id: int):
        self.agent_id = agent_id
        self.user_agent = USER_AGENTS[agent_id % len(USER_AGENTS)]
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        self.requests_made = 0

    def fetch(self, url: str, retries: int = SCRAPE_MAX_RETRIES) -> str | None:
        for attempt in range(retries):
            try:
                resp = self.session.get(url, timeout=SCRAPE_TIMEOUT)
                resp.raise_for_status()
                self.requests_made += 1
                return resp.text
            except Exception as e:
                if attempt < retries - 1:
                    time.sleep(1)
        return None


class BatchBuffer:
    """Buffer that flushes every BATCH_SIZE rows or on manual flush."""
    def __init__(self, batch_size: int = BATCH_SIZE, on_flush=None):
        self.batch_size = batch_size
        self.buffer = []
        self.on_flush = on_flush

    def add(self, company: dict):
        self.buffer.append(company)
        if len(self.buffer) >= self.batch_size:
            return self.flush()
        return []

    def flush(self) -> list[dict]:
        batch = self.buffer[:]
        self.buffer = []
        if self.on_flush and batch:
            self.on_flush(batch)
        return batch

    @property
    def size(self):
        return len(self.buffer)


def create_agents(count: int) -> list[ScrapingAgent]:
    return [ScrapingAgent(i) for i in range(count)]


# === Email extraction helpers ===

def _decode_obfuscated_email(text: str) -> list[str]:
    """Decode common email obfuscation techniques and extract emails."""
    emails = []
    if not text:
        return emails
    decoded = text
    decoded = re.sub(r"&#64;", "@", decoded)
    decoded = re.sub(r"&#46;", ".", decoded)
    decoded = re.sub(r"&#0*64;", "@", decoded)
    decoded = re.sub(r"&#0*46;", ".", decoded)
    decoded = re.sub(r"\s*\[at\]\s*", "@", decoded, flags=re.IGNORECASE)
    decoded = re.sub(r"\s*\(at\)\s*", "@", decoded, flags=re.IGNORECASE)
    decoded = re.sub(r"\s*\[dot\]\s*", ".", decoded, flags=re.IGNORECASE)
    decoded = re.sub(r"\s*\(dot\)\s*", ".", decoded, flags=re.IGNORECASE)
    decoded = re.sub(r"\s+at\s+(?=\w+\.)", "@", decoded, flags=re.IGNORECASE)
    decoded = re.sub(r"\s+dot\s+(?=\w{2,}$)", ".", decoded, flags=re.IGNORECASE)
    found = re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', decoded)
    emails.extend(found)
    return emails


def _extract_emails_from_scripts(soup: BeautifulSoup) -> list[str]:
    """Extract emails from script tags and JSON-LD structured data."""
    emails = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            content = script.string or ""
            found = re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', content)
            emails.extend(found)
        except Exception:
            pass
    for meta in soup.find_all("meta"):
        name = (meta.get("name") or "").lower()
        prop = (meta.get("property") or "").lower()
        content = meta.get("content", "")
        if any(k in name for k in ["email", "contact"]) or any(k in prop for k in ["email", "contact"]):
            found = re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', content)
            emails.extend(found)
    for script in soup.find_all("script"):
        if script.string:
            found = re.findall(
                r'(?:email|e_mail|mail|contact|from)\s*[:=]\s*["\']([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})["\']',
                script.string, re.IGNORECASE,
            )
            emails.extend(found)
    return emails


def _discover_email_from_domain(domain: str) -> str | None:
    """Try common email prefixes for a domain via DNS MX check."""
    try:
        import dns.resolver
    except ImportError:
        return None
    try:
        mx_records = dns.resolver.resolve(domain, "MX")
        if not mx_records:
            return None
    except Exception:
        return None
    prefixes = ["info", "contact", "admin", "office", "hello", "mail", "support"]
    for prefix in prefixes:
        email = f"{prefix}@{domain}"
        if re.match(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', email):
            return email
    return None


def _extract_emails_from_html(html: str, soup: BeautifulSoup) -> set:
    """Extract all emails from HTML using multiple methods."""
    emails = set()
    # mailto links
    for a in soup.find_all("a", href=True):
        if a["href"].startswith("mailto:"):
            email = a["href"].replace("mailto:", "").split("?")[0].strip()
            if email and re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", email):
                emails.add(email)
    # obfuscated
    for email in _decode_obfuscated_email(html):
        clean = email.strip(".").lower()
        if len(clean) > 5 and not clean.endswith((".png", ".jpg", ".gif", ".js", ".css", ".svg")):
            emails.add(clean)
    # scripts/meta
    for email in _extract_emails_from_scripts(soup):
        clean = email.strip(".").lower()
        if len(clean) > 5:
            emails.add(clean)
    # plain regex
    for e in re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', html):
        clean = e.strip(".").lower()
        if len(clean) > 5 and not clean.endswith((".png", ".jpg", ".gif", ".js", ".css", ".svg")):
            emails.add(clean)
    return emails


# === construction.am parsers ===

def parse_construction_am_links(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    companies = []
    for item in soup.find_all("div", class_="post-item"):
        link = item.find("a", href=True)
        if link and "/companies/" in link["href"]:
            full_url = urljoin(base_url, link["href"])
            name = item.get_text(strip=True)
            companies.append({"url": full_url, "name": name if name else None})
    return companies


def parse_construction_am_profile(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    data = {"source_url": url, "source_site": "construction.am"}

    # Name
    title_div = soup.find("div", class_="page-title")
    if title_div:
        h3 = title_div.find("h3")
        if h3:
            a_tag = h3.find("a")
            data["company_name"] = (a_tag or h3).get_text(strip=True)
    if not data.get("company_name"):
        h1 = soup.find("h1")
        if h1:
            data["company_name"] = h1.get_text(strip=True)

    # Phone
    for a in soup.find_all("a", href=True):
        if a["href"].startswith("tel:"):
            phone = a["href"].replace("tel:", "").strip()
            if phone and len(phone) > 5:
                data["phone"] = phone
                break

    # Email
    email_btn = soup.find("a", attrs={"data-toggle": "popover"})
    if email_btn:
        for attr in ["data-original-title", "data-content", "title"]:
            val = email_btn.get(attr, "").strip()
            if "@" in val:
                match = re.search(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+", val)
                if match:
                    data["email"] = match.group(0)
                break

    # Director
    director_icon = soup.find("i", class_=lambda c: c and "fa-user-circle" in c)
    if director_icon:
        span = director_icon.find_next_sibling("span")
        if span:
            data["director"] = span.get_text(strip=True)

    # City
    address_icon = soup.find("i", class_=lambda c: c and "fa-map-signs" in c)
    if address_icon:
        parent = address_icon.parent
        if parent:
            parts = []
            for sibling in address_icon.next_siblings:
                text = sibling.string if sibling.string else sibling.get_text(strip=True)
                if text:
                    parts.append(text.strip())
            addr_text = " ".join(parts).strip()
            if addr_text:
                city_parts = [p.strip() for p in addr_text.split(",")]
                for part in city_parts:
                    if part in ["Hayastan", "Armenia", "\u0540\u0561\u0575\u0561\u057d\u057f\u0561\u0576"]:
                        continue
                    if re.match(r"^\d+$", part):
                        continue
                    cleaned = re.sub(r"^[\u0563\u0533][\.\u002E\u2024]\s*", "", part)
                    if cleaned:
                        data["city"] = cleaned.split()[0]
                        break
    if not data.get("city"):
        text = soup.get_text()
        if "\u0535\u0580\u0565\u057e\u0561\u0576" in text or "Yerevan" in text:
            data["city"] = "Yerevan"

    return data


# === norakaruyc.am API parser ===

def _parse_norakaruyc_api(items: list[dict]) -> list[dict]:
    """Parse norakaruyc.am API response into company dicts."""
    companies = []
    for item in items:
        name = item.get("Name", "")
        if not name:
            continue
        name_en = name
        for trans in item.get("NameTranslations", []):
            if trans.get("Key") == "en" and trans.get("Value"):
                name_en = trans["Value"]
                break
        address_en = item.get("Address", "")
        for trans in item.get("AddressTranslations", []):
            if trans.get("Key") == "en" and trans.get("Value"):
                address_en = trans["Value"]
                break
        company = {
            "company_name": name_en,
            "source_url": f"https://norakaruyc.am/en/building/{item.get('Id', '')}",
            "source_site": "norakaruyc.am",
        }
        if address_en:
            parts = [p.strip() for p in address_en.split(",")]
            for part in parts:
                lower = part.lower()
                if any(skip in lower for skip in ["region", "community", "village"]):
                    continue
                if part in ["Armenia", "Hayastan"]:
                    continue
                if re.match(r"^\d+$", part):
                    continue
                company["city"] = part
                break
        companies.append(company)
    return companies


# === spyur.am parsers ===

def parse_spyur_am_links(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    companies = []
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if "/companies/" in href:
            full_url = urljoin(base_url, href)
            name = a.get_text(strip=True)
            if name and len(name) > 3:
                companies.append({"url": full_url, "name": name})
    return companies


def parse_spyur_am_profile(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    data = {"source_url": url, "source_site": "spyur.am"}

    # Name
    h1 = soup.select_one(".right_col h1.page_title")
    if not h1:
        h1 = soup.find("h1")
    if h1:
        data["company_name"] = h1.get_text(strip=True)

    # Director
    lead_info = soup.select_one(".lead_block .lead_info")
    if lead_info:
        data["director"] = lead_info.get_text(strip=True)

    # Phone
    phones = []
    for a in soup.select(".branch_block .phone_info a.call[href^='tel:']"):
        phone = a["href"].replace("tel:", "").strip()
        if phone and phone not in phones:
            phones.append(phone)
    if phones:
        data["phone"] = phones[0]

    # City
    address_block = soup.select_one(".branch_block .address_block")
    if address_block:
        addr_text = address_block.get_text(separator=", ", strip=True)
        if addr_text:
            parts = [p.strip() for p in addr_text.split(",")]
            for part in parts:
                lower = part.lower()
                if part in ["Hayastan", "Armenia", "\u0540\u0561\u0575\u0561\u057d\u057f\u0561\u0576"]:
                    continue
                if re.match(r"^\d+$", part):
                    continue
                if "marz" in lower or "\u0577\u0580\u057b" in part or "\u0574\u0561\u0580\u0566" in part or "\u0544\u0561\u0580\u0566" in part:
                    continue
                data["city"] = part
                break

    # Website (needed for enrichment — not stored in DB but used to crawl for emails)
    skip_host = ["spyur", "facebook", "instagram", "linkedin"]
    for a in soup.select(".contact_subblock a.web_link[href^='http']"):
        href = a["href"]
        if not any(s in href.lower() for s in skip_host):
            data["website"] = href
            break

    return data


# === myhome.am parser (Playwright) ===

def parse_myhome_am_links(html: str, base_url: str) -> list[dict]:
    """Parse myhome.am partner listing page (rendered by Playwright)."""
    soup = BeautifulSoup(html, "html.parser")
    companies = []
    # Look for partner/developer cards — try multiple selector patterns
    for card in soup.select("a[href*='/partner/'], a[href*='/building/'], a[href*='/project/']"):
        href = card.get("href", "")
        if href and not href.startswith("http"):
            href = urljoin(base_url, href)
        name = card.get_text(strip=True)
        if name and len(name) > 2 and href:
            companies.append({"url": href, "name": name})
    # Also try generic card patterns
    for card in soup.select("[class*='partner'], [class*='developer'], [class*='card']"):
        link = card.find("a", href=True)
        if link:
            href = link["href"]
            if not href.startswith("http"):
                href = urljoin(base_url, href)
            name_el = card.select_one("h2, h3, h4, [class*='name'], [class*='title']")
            name = name_el.get_text(strip=True) if name_el else link.get_text(strip=True)
            if name and len(name) > 2:
                companies.append({"url": href, "name": name})
    return companies


def parse_myhome_am_profile(html: str, url: str) -> dict:
    """Parse a myhome.am company page (rendered by Playwright)."""
    soup = BeautifulSoup(html, "html.parser")
    data = {"source_url": url, "source_site": "myhome.am"}

    # Name
    h1 = soup.find("h1")
    if h1:
        data["company_name"] = h1.get_text(strip=True)

    # Phone
    for a in soup.find_all("a", href=True):
        if a["href"].startswith("tel:"):
            phone = a["href"].replace("tel:", "").strip()
            if phone and len(phone) > 5:
                data["phone"] = phone
                break

    # Email
    for a in soup.find_all("a", href=True):
        if a["href"].startswith("mailto:"):
            email = a["href"].replace("mailto:", "").split("?")[0].strip()
            if email and re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", email):
                data["email"] = email
                break

    # Also check for emails in page text
    if not data.get("email"):
        emails = _extract_emails_from_html(html, soup)
        if emails:
            data["email"] = next(iter(emails))

    # Director / contact person
    for el in soup.select("[class*='director'], [class*='contact'], [class*='manager']"):
        text = el.get_text(strip=True)
        if text and len(text) > 3 and len(text) < 200:
            data["director"] = text
            break

    # City
    for el in soup.select("[class*='city'], [class*='location'], [class*='address']"):
        text = el.get_text(strip=True)
        if text and len(text) > 2 and len(text) < 100:
            data["city"] = text
            break

    return data


def _parse_myhome_am_detail(detail: dict, base_url: str) -> dict:
    """Parse myhome.am builder detail API response."""
    data = {"source_site": "myhome.am"}

    # Company name (English)
    brand = detail.get("builderBrandName", {})
    if isinstance(brand, dict):
        data["company_name"] = brand.get("eng") or brand.get("arm") or brand.get("rus", "")
    else:
        data["company_name"] = str(brand)

    if not data["company_name"]:
        return None

    # Email
    email = detail.get("email", "")
    if email:
        data["email"] = email

    # Phone
    phone = detail.get("builderMobilePhoneNumber", "") or detail.get("phoneNumber", "")
    if phone:
        data["phone"] = phone

    # City from address
    address = detail.get("builderBusinessAddress", {})
    if isinstance(address, dict):
        addr_text = address.get("eng") or address.get("arm") or address.get("rus", "")
    else:
        addr_text = str(address)
    if addr_text:
        # Extract city from address
        parts = [p.strip() for p in addr_text.split(",")]
        for part in parts:
            if "yerevan" in part.lower() or "\u0535\u0580\u0565\u057e\u0561\u0576" in part:
                data["city"] = "Yerevan"
                break
            if len(part) > 2 and not any(c.isdigit() for c in part):
                data["city"] = part
                break

    # Source URL
    translit = detail.get("translitUrl", "")
    builder_id = detail.get("id", "")
    if translit and builder_id:
        data["source_url"] = f"{base_url}/en/partners/developers/{builder_id}/{translit}"
    else:
        data["source_url"] = f"{base_url}/en/partners/developers/{builder_id}"

    return data


# === Website enrichment ===

def crawl_company_website(agent: ScrapingAgent, website_url: str) -> dict:
    result = {"all_text": [], "emails": set(), "phones": set()}
    paths = [
        "/", "/about", "/about-us", "/contacts",
        "/contact", "/contact-us", "/support",
        "/feedback", "/team", "/info", "/projects",
    ]
    for path in paths:
        url = urljoin(website_url, path)
        html = agent.fetch(url, retries=1)
        if html:
            soup = BeautifulSoup(html, "html.parser")
            # Extract emails
            emails = _extract_emails_from_html(html, soup)
            result["emails"].update(emails)
            # Extract phones
            for a in soup.find_all("a", href=True):
                if a["href"].startswith("tel:"):
                    phone = a["href"].replace("tel:", "").strip()
                    if phone and len(phone) > 5:
                        result["phones"].add(phone)
            # Capture page text
            for elem in soup(["script", "style"]):
                elem.decompose()
            text = soup.get_text(separator=" ", strip=True)[:2000]
            result["all_text"].append(text)
        time.sleep(0.2)

    result["full_text"] = " ".join(result["all_text"])
    if result["emails"]:
        result["email"] = next(iter(result["emails"]))
    if result["phones"]:
        result["phone"] = next(iter(result["phones"]))
    return result


def _fetch_profile(args: tuple) -> dict | None:
    link, agent, parser = args
    html = agent.fetch(link["url"])
    if html:
        data = parser(html, link["url"])
        if not data.get("company_name") and link.get("name"):
            data["company_name"] = link["name"]
        return data
    return None


def _enrich_company(args: tuple) -> dict:
    company, agent = args
    website = company.get("website")
    if website:
        if not website.startswith(("http://", "https://")):
            website = "https://" + website
            company["website"] = website
        web_data = crawl_company_website(agent, website)
        if web_data.get("full_text"):
            company["_all_text"] = web_data["full_text"]
        if not company.get("email") and web_data.get("email"):
            company["email"] = web_data["email"]
        if not company.get("phone") and web_data.get("phone"):
            company["phone"] = web_data["phone"]

    # DNS MX discovery if still no email
    if not company.get("email") and website:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(website if website.startswith("http") else "https://" + website)
            domain = parsed.hostname
            if domain:
                discovered = _discover_email_from_domain(domain)
                if discovered:
                    company["email"] = discovered
        except Exception:
            pass

    return company


# === Main source runner ===

def run_source(source_key: str, source_config: dict, agents: list[ScrapingAgent], buffer: BatchBuffer, max_pages: int = None) -> list[dict]:
    """Crawl a single source and add to buffer."""
    base_url = source_config["base_url"]
    listing_pages = source_config["listing_pages"]
    is_js = source_config["type"] == "js"
    use_letters = source_config.get("letter_pagination", False)
    is_api_rest = source_config["type"] == "api_rest"

    log.info(f"  Source: {source_key} ({source_config['type']})")
    all_links = []

    if source_config["type"] == "api":
        # API-based source (norakaruyc.am)
        log.info(f"    Using API for {source_key}...")
        try:
            import requests as req
            api_url = source_config["base_url"] + "/api/website/getHomePageCards"
            payload = {"HomeType": 4, "HomeState": 4, "Lang": "en"}
            resp = req.post(api_url, json=payload, timeout=SCRAPE_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("Data", [])
            if items:
                companies = _parse_norakaruyc_api(items)
                all_links.extend(companies)
                log.info(f"    API: Found {len(companies)} companies")
            else:
                log.warning(f"    API returned no data")
        except Exception as e:
            log.error(f"    API error: {e}")

    elif source_config["type"] == "api_rest":
        # REST API source (myhome.am)
        log.info(f"    Using REST API for {source_key}...")
        try:
            import requests as req
            # Step 1: Get list of builders
            list_url = source_config["base_url"] + "/API/v1/builders?lang=en"
            resp = req.get(list_url, timeout=SCRAPE_TIMEOUT)
            resp.raise_for_status()
            builders = resp.json()
            log.info(f"    Found {len(builders)} builders in list")

            # Step 2: Fetch details for each builder
            companies = []
            for i, builder in enumerate(builders):
                builder_id = builder.get("id")
                if not builder_id:
                    continue
                try:
                    detail_url = f"{source_config['base_url']}/API/v1/builders/{builder_id}"
                    detail_resp = req.get(detail_url, timeout=SCRAPE_TIMEOUT)
                    if detail_resp.status_code == 200:
                        detail = detail_resp.json()
                        company = _parse_myhome_am_detail(detail, source_config["base_url"])
                        if company and company.get("company_name"):
                            companies.append(company)
                except Exception:
                    pass
                if (i + 1) % 20 == 0:
                    log.info(f"      Fetched {i + 1}/{len(builders)} builder details")
                time.sleep(0.3)

            all_links.extend(companies)
            log.info(f"    API: Found {len(companies)} companies with details")
        except Exception as e:
            log.error(f"    API error: {e}")

    elif is_js:
        # Use Playwright for JS-rendered pages
        log.info(f"    Using Playwright for {source_key}...")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=random.choice(USER_AGENTS))
            page = context.new_page()

            for listing_page in listing_pages:
                url = base_url + listing_page
                try:
                    page.goto(url, wait_until="networkidle", timeout=SCRAPE_TIMEOUT * 1000)
                    time.sleep(3)
                    html = page.content()
                    companies = parse_myhome_am_links(html, base_url)
                    all_links.extend(companies)
                    log.info(f"    {listing_page}: Found {len(companies)} companies")
                except Exception as e:
                    log.error(f"    Error on {listing_page}: {e}")
                time.sleep(SCRAPE_DELAY)

            browser.close()

    elif source_config.get("page_pagination"):
        # Use requests with page number pagination
        page_num = 1
        spyur_max = max_pages if max_pages else 20
        while page_num <= spyur_max:
            url = base_url + listing_pages[0].replace("advanced_search", f"advanced_search-{page_num}" if page_num > 1 else "advanced_search")
            if page_num == 1:
                url = base_url + listing_pages[0]
            html = agents[0].fetch(url)
            if not html:
                break
            companies = parse_spyur_am_links(html, base_url)
            if not companies:
                break
            all_links.extend(companies)
            log.info(f"      Page {page_num}: Found {len(companies)} companies")
            page_num += 1
            time.sleep(SCRAPE_DELAY)
    else:
        # Use requests for static pages
        for listing_page in listing_pages:
            log.info(f"    {listing_page}")
            html = agents[0].fetch(base_url + listing_page)
            if html:
                companies = parse_construction_am_links(html, base_url)
                all_links.extend(companies)
                log.info(f"      Page 1: Found {len(companies)} companies")

            if use_letters:
                letters = [
                    "%D4%B1", "%D4%B2", "%D4%B3", "%D4%B4", "%D4%B5",
                    "%D4%B6", "%D4%B7", "%D4%B8", "%D4%B9", "%D4%BA",
                    "%D4%BB", "%D4%BC", "%D4%BD", "%D4%BE", "%D4%BF",
                    "%D5%B0", "%D5%B1", "%D5%B2", "%D5%B3", "%D5%B4",
                    "%D5%B5", "%D5%B6", "%D5%B7", "%D5%B8", "%D5%B9",
                    "%D5%BA", "%D5%BB", "%D5%BC", "%D5%BD", "%D5%BE",
                    "%D5%BF", "%D6%80", "%D6%81", "%D6%82", "%D6%83",
                    "%D6%84", "%D6%85", "%D6%86",
                ]
                letter_limit = max_pages if max_pages else len(letters)
                for i, letter in enumerate(letters[:letter_limit]):
                    page_url = f"{base_url}{listing_page}?letter={letter}"
                    html = agents[(i + 1) % len(agents)].fetch(page_url)
                    if html:
                        companies = parse_construction_am_links(html, base_url)
                        all_links.extend(companies)
                        if companies:
                            log.info(f"      Letter {i + 1}: Found {len(companies)} companies")
                    time.sleep(SCRAPE_DELAY)

    # Deduplicate links
    seen = set()
    unique = []
    for l in all_links:
        url = l.get("url") or l.get("source_url", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(l)

    log.info(f"    Total unique items: {len(unique)}")

    if not unique:
        return []

    # API source already returns full company data, skip profile fetching
    if source_config["type"] in ("api", "api_rest"):
        companies = unique
    else:
        # Crawl profiles
        if source_key == "spyur_am":
            parser = parse_spyur_am_profile
        elif source_key == "myhome_am":
            parser = parse_myhome_am_profile
        else:
            parser = parse_construction_am_profile
        profile_args = [(link, agents[i % len(agents)], parser) for i, link in enumerate(unique)]
        companies = []

        with ThreadPoolExecutor(max_workers=min(len(agents), len(unique))) as executor:
            futures = [executor.submit(_fetch_profile, args) for args in profile_args]
            for i, future in enumerate(as_completed(futures), 1):
                result = future.result()
                if result and result.get("company_name"):
                    companies.append(result)
            if i % 20 == 0 or i == len(futures):
                log.info(f"      Profiles: {i}/{len(futures)}")

    # Add to batch buffer BEFORE enrichment
    flushed = 0
    for company in companies:
        batch = buffer.add(company)
        if batch:
            flushed += len(batch)

    # Enrich (visits company websites for emails/phones)
    if companies:
        enrich_args = [(company, agents[i % len(agents)]) for i, company in enumerate(companies)]
        with ThreadPoolExecutor(max_workers=min(len(agents), len(companies))) as executor:
            futures = [executor.submit(_enrich_company, args) for args in enrich_args]
            companies = [f.result() for f in as_completed(futures)]

    return companies


def run_crawler(num_agents: int = MAX_WORKERS, sources: list[str] = None, max_pages_per_source: dict = None, on_flush=None) -> list[dict]:
    """Main crawler. Returns all companies."""
    agents = create_agents(num_agents)
    buffer = BatchBuffer(on_flush=on_flush)

    if sources is None:
        sources = list(SOURCES.keys())

    log.info(f"  Created {num_agents} scraping agents")
    log.info(f"  Sources: {', '.join(sources)}")

    all_companies = []

    for source_key in sources:
        if source_key not in SOURCES:
            log.warning(f"  Unknown source: {source_key}")
            continue
        max_pages = (max_pages_per_source or {}).get(source_key)
        companies = run_source(source_key, SOURCES[source_key], agents, buffer, max_pages=max_pages)
        all_companies.extend(companies)

    # Final flush
    remaining = buffer.flush()
    if remaining:
        log.info(f"  Final flush: {len(remaining)} rows")

    total_requests = sum(a.requests_made for a in agents)
    log.info(f"  Total: {len(all_companies)} companies, {total_requests} requests")

    return all_companies
