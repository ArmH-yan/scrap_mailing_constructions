"""
Lead Generation Scraper — Crawler v2
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
from playwright.sync_api import sync_playwright

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
        "listing_pages": ["/arm/developers.php", "/arm/construction.php", "/arm/suppliers.php"],
        "type": "static",
        "letter_pagination": True,
    },
    "spyur_am": {
        "base_url": "https://www.spyur.am",
        "listing_pages": ["/am/home/advanced_search/?search=1&products_and_services=1&yp_cat3=375"],
        "type": "static",
        "letter_pagination": False,
        "page_pagination": True,
    },
    "norakaruyc_am": {
        "base_url": "https://norakaruyc.am",
        "listing_pages": ["/builders", "/developers"],
        "type": "js",
        "letter_pagination": False,
    },
}


def compute_content_hash(name: str, phone: str, url: str) -> str:
    """SHA-1 hash for dedup."""
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


# === construction.am parsers (static/BS4) ===

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

    # Name from h3 in page-title
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

    # Phones - collect ALL
    phones = []
    for a in soup.find_all("a", href=True):
        if a["href"].startswith("tel:"):
            phone = a["href"].replace("tel:", "").strip()
            if phone and len(phone) > 5 and phone not in phones:
                phones.append(phone)
    if phones:
        data["phone"] = phones[0]
        if len(phones) > 1:
            data["phone_secondary"] = ", ".join(phones[1:])

    # Email - in data-original-title or data-content of popover button
    email_btn = soup.find("a", attrs={"data-toggle": "popover"})
    if email_btn:
        for attr in ["data-original-title", "data-content", "title"]:
            val = email_btn.get(attr, "").strip()
            if "@" in val:
                # data-content may contain HTML, extract email from it
                match = re.search(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+", val)
                if match:
                    data["email"] = match.group(0)
                break

    # Website - from button with text or any external link
    websites = []
    for a in soup.find_all("a", href=True, class_="button"):
        href = a["href"]
        if href.startswith("http") and "construction.am" not in href:
            if href not in websites:
                websites.append(href)
    if not websites:
        skip = ["construction.am", "facebook", "instagram", "yandex", "mail.ru",
                "rambler", "google", "liveinternet", "linkedin"]
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("http") and not any(d in href for d in skip):
                if href not in websites:
                    websites.append(href)
    if websites:
        data["website"] = websites[0]

    # Address - from i.fa-map-signs, get text after the icon
    address_icon = soup.find("i", class_=lambda c: c and "fa-map-signs" in c)
    if address_icon:
        # Get the parent div, then extract only the text nodes (skip icon text)
        parent = address_icon.parent
        if parent:
            # Collect text from siblings of the <i> tag
            parts = []
            for sibling in address_icon.next_siblings:
                text = sibling.string if sibling.string else sibling.get_text(strip=True)
                if text:
                    parts.append(text.strip())
            addr_text = " ".join(parts).strip()
            if addr_text:
                data["address"] = addr_text

    # City - from address or text
    if data.get("address"):
        parts = [p.strip() for p in data["address"].split(",")]
        for part in parts:
            lower = part.lower()
            if part in ["Hayastan", "Haastan", "Armenia", "\u0540\u0561\u0575\u0561\u057d\u057f\u0561\u0576"]:
                continue
            if re.match(r"^\d+$", part):
                continue
            if "marz" in lower or "\u0574\u0561\u0580\u0566" in part or "\u0544\u0561\u0580\u0566" in part:
                continue
            # Strip "գ." prefix (means "city" in Armenian) - handles both ASCII and Armenian period
            cleaned = re.sub(r"^[\u0563\u0533][\.\u002E\u2024]\s*", "", part)
            if cleaned:
                # Take only the city name (first word), not the street after it
                # e.g. "Երևան Վերին Անտառային փող." -> "Երևան"
                city_candidate = cleaned.split()[0] if cleaned.split() else cleaned
                data["city"] = city_candidate
                break
    if not data.get("city"):
        text = soup.get_text()
        if "\u0535\u0580\u0565\u057e\u0561\u0576" in text or "Yerevan" in text:
            data["city"] = "Yerevan"
        elif "Gyumri" in text:
            data["city"] = "Gyumri"

    # Director - from i.fa-user-circle
    director_icon = soup.find("i", class_=lambda c: c and "fa-user-circle" in c)
    if director_icon:
        span = director_icon.find_next_sibling("span")
        if span:
            data["director"] = span.get_text(strip=True)

    # Founded year
    for span in soup.find_all("span", class_="hidden-xs"):
        if span.get_text(strip=True).startswith("\u0540\u056b\u0574\u0576\u0561\u0564\u057e\u0565\u056c \u0567"):
            badge = span.find("span", class_="badge")
            if badge:
                try:
                    data["founded_year"] = int(badge.get_text(strip=True))
                except ValueError:
                    pass

    # GPS coordinates from script
    scripts = soup.find_all("script")
    for script in scripts:
        text = script.get_text()
        match = re.search(r"var\s+markers\s*=\s*\[.*?\[.*?,\s*([\d.]+),\s*([\d.]+)\]", text, re.DOTALL)
        if match:
            data["gps_lat"] = float(match.group(1))
            data["gps_lon"] = float(match.group(2))
            break

    # Social media links - skip source site's own social pages
    source_domain = "construction.am"
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "facebook.com" in href and "adrotators" not in href and "ad_click" not in href:
            if source_domain not in href and not data.get("facebook_url"):
                data["facebook_url"] = href
            elif "adrotators" in href or "ad_click" in href:
                import urllib.parse
                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                real_url = parsed.get("ad_url", [None])[0]
                if real_url and "facebook.com" in real_url and source_domain not in real_url and not data.get("facebook_url"):
                    data["facebook_url"] = real_url
        elif "instagram.com" in href and source_domain not in href and not data.get("instagram_url"):
            if "adrotators" not in href:
                data["instagram_url"] = href
        elif "linkedin.com" in href and source_domain not in href and not data.get("linkedin_url"):
            data["linkedin_url"] = href

    # Description
    meta = soup.find("meta", attrs={"name": "description"})
    if meta:
        data["company_description"] = meta.get("content", "")

    # Services - from structured list
    services = []
    for li in soup.select("div[style*='text-align:justify'] ul li"):
        text = li.get_text(strip=True)
        if text and len(text) < 200:
            services.append(text)
    if services:
        data["services"] = ", ".join(services)

    return data


# === norakaruyc.am parsers (JS/Playwright) ===

def parse_norakaruyc_links(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    companies = []
    for card in soup.select("div.card, div.builder-card, article, div.listing-item"):
        link = card.find("a", href=True)
        if link:
            full_url = urljoin(base_url, link["href"])
            name = card.select_one("h2, h3, h4, .title, .name")
            companies.append({
                "url": full_url,
                "name": name.get_text(strip=True) if name else link.get_text(strip=True)
            })
    return companies


def parse_norakaruyc_profile(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    data = {"source_url": url, "source_site": "norakaruyc.am"}

    h1 = soup.find("h1")
    if h1:
        data["company_name"] = h1.get_text(strip=True)

    for a in soup.find_all("a", href=True):
        if a["href"].startswith("tel:"):
            phone = a["href"].replace("tel:", "").strip()
            if phone and len(phone) > 5:
                data["phone"] = phone
                break

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("http") and "norakaruyc" not in href and "facebook" not in href:
            data["website"] = href
            break

    meta = soup.find("meta", attrs={"name": "description"})
    if meta:
        data["company_description"] = meta.get("content", "")

    return data


# === spyur.am parsers (static/BS4) ===

def parse_spyur_am_links(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    companies = []
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if "/am/companies/" in href:
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

    # Phones - from first branch
    phones = []
    for a in soup.select(".branch_block .phone_info a.call[href^='tel:']"):
        phone = a["href"].replace("tel:", "").strip()
        if phone and phone not in phones:
            phones.append(phone)
    if phones:
        data["phone"] = phones[0]

    # Address - from first branch (HQ)
    address_block = soup.select_one(".branch_block .address_block")
    if address_block:
        addr_text = address_block.get_text(separator=", ", strip=True)
        if addr_text:
            data["address"] = addr_text

    # City - extract from address
    if data.get("address"):
        parts = [p.strip() for p in data["address"].split(",")]
        for part in parts:
            lower = part.lower()
            if part in ["Hayastan", "Armenia", "\u0540\u0561\u0575\u0561\u057d\u057f\u0561\u0576"]:
                continue
            if re.match(r"^\d+$", part):
                continue
            # \u0577\u0580\u057b = shrj (marz in Armenian), \u0574\u0561\u0580\u0566 = marz in Armenian
            if "marz" in lower or "\u0577\u0580\u057b" in part or "\u0574\u0561\u0580\u0566" in part or "\u0544\u0561\u0580\u0566" in part:
                continue
            data["city"] = part
            break

    # District
    district_block = soup.select_one(".branch_block .destriction_block")
    if district_block:
        dist_text = district_block.get_text(strip=True).strip("()")
        if dist_text:
            data["district"] = dist_text

    # Website
    skip_host = ["spyur", "facebook", "instagram", "linkedin"]
    websites = []
    for a in soup.select(".contact_subblock a.web_link[href^='http']"):
        href = a["href"]
        if not any(s in href.lower() for s in skip_host):
            if href not in websites:
                websites.append(href)
    if websites:
        data["website"] = websites[0]

    # Work hours
    work_hours = soup.select_one(".branch_block .work_hours")
    if work_hours:
        data["work_hours"] = work_hours.get_text(strip=True)

    # GPS coordinates
    map_canvas = soup.select_one("#map_canvas")
    if map_canvas:
        lat = map_canvas.get("lat")
        lon = map_canvas.get("lon")
        if lat and lon:
            try:
                data["gps_lat"] = float(lat)
                data["gps_lon"] = float(lon)
            except ValueError:
                pass

    # Other info - founding year, employee count, ownership
    for li in soup.select(".other_info .info_list li"):
        subtitle = li.select_one(".inner_subtitle")
        text_block = li.select_one(".text_block")
        if subtitle and text_block:
            label = subtitle.get_text(strip=True).lower()
            value = text_block.get_text(strip=True)
            # \u0570\u056b\u0574\u0576\u0561\u0564\u0580\u0574\u0561\u0576 = "հիմնադրման"
            if "\u0570\u056b\u0574\u0576\u0561\u0564\u0580\u0574\u0561\u0576" in label:
                try:
                    data["founded_year"] = int(value)
                except ValueError:
                    pass
            # \u0561\u0577\u056d\u0561\u057f\u0578\u0572\u0576\u0565\u0580\u056b = "աշխատողների"
            elif "\u0561\u0577\u056d\u0561\u057f\u0578\u0572\u0576\u0565\u0580\u056b" in label:
                data["employee_count"] = value
            # \u057d\u0565\u0583\u0561\u056f\u0561\u0576\u0578\u0582\u0569\u0575\u0561\u0576 = "սepakanut'yan"
            elif "\u057d\u0565\u0583\u0561\u056f\u0561\u0576\u0578\u0582\u0569\u0575\u0561\u0576" in label:
                data["ownership_type"] = value

    # Social links
    for a in soup.select(".contact_subblock a"):
        href = a.get("href", "")
        classes = a.get("class", [])
        if "facebook" in href.lower() or "facebook_link" in classes:
            if not data.get("facebook_url"):
                data["facebook_url"] = href
        elif "instagram" in href.lower() or "instagram_link" in classes:
            if not data.get("instagram_url"):
                data["instagram_url"] = href
        elif "linkedin" in href.lower():
            if not data.get("linkedin_url"):
                data["linkedin_url"] = href

    # Description - from meta or business activity section
    meta = soup.find("meta", attrs={"name": "description"})
    if meta:
        data["company_description"] = meta.get("content", "")
    if not data.get("company_description"):
        activity = soup.select_one(".details_info .info_section:first-child .info_content .text_block")
        if activity:
            data["company_description"] = activity.get_text(strip=True)[:1000]

    # Services - from multilevel list
    services = []
    for a in soup.select(".info_content .multilevel_list a[href*='business_directory']"):
        text = a.get_text(strip=True)
        if text and len(text) < 200:
            services.append(text)
    if services:
        data["services"] = ", ".join(services[:20])

    return data


def crawl_company_website(agent: ScrapingAgent, website_url: str) -> dict:
    result = {"projects": [], "all_text": [], "emails": set(), "phones": set()}
    for path in ["/", "/about", "/projects", "/contact"]:
        url = urljoin(website_url, path)
        html = agent.fetch(url, retries=1)
        if html:
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                if a["href"].startswith("mailto:"):
                    email = a["href"].replace("mailto:", "").split("?")[0].strip()
                    if email and re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", email):
                        result["emails"].add(email)
                elif a["href"].startswith("tel:"):
                    phone = a["href"].replace("tel:", "").strip()
                    if phone and len(phone) > 5:
                        result["phones"].add(phone)
            body_emails = re.findall(r'[\w.+\-]+@[\w\-]+\.[\w.\-]+', html)
            for e in body_emails:
                clean = e.strip(".")
                if len(clean) > 5 and not clean.endswith((".png", ".jpg", ".gif", ".js", ".css")):
                    result["emails"].add(clean)
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
        if not company.get("website") and web_data.get("website"):
            company["website"] = web_data["website"]
    return company


def run_source(source_key: str, source_config: dict, agents: list[ScrapingAgent], buffer: BatchBuffer, max_pages: int = None) -> list[dict]:
    """Crawl a single source and add to buffer."""
    base_url = source_config["base_url"]
    listing_pages = source_config["listing_pages"]
    is_js = source_config["type"] == "js"
    use_letters = source_config.get("letter_pagination", False)

    log.info(f"  Source: {source_key} ({source_config['type']})")
    all_links = []

    if is_js:
        # Use Playwright for JS-rendered pages
        log.info(f"    Using Playwright for {source_key}...")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=random.choice(USER_AGENTS))
            page = context.new_page()

            for listing_page in listing_pages:
                url = base_url + listing_page
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=SCRAPE_TIMEOUT * 1000)
                    time.sleep(2)
                    html = page.content()
                    companies = parse_norakaruyc_links(html, base_url)
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
        if l["url"] not in seen:
            seen.add(l["url"])
            unique.append(l)

    log.info(f"    Total unique links: {len(unique)}")

    if not unique:
        return []

    # Crawl profiles
    if is_js:
        parser = parse_norakaruyc_profile
    elif source_key == "spyur_am":
        parser = parse_spyur_am_profile
    else:
        parser = parse_construction_am_profile
    profile_args = [(link, agents[i % len(agents)], parser) for i, link in enumerate(unique)]
    companies = []

    with ThreadPoolExecutor(max_workers=min(len(agents), len(unique))) as executor:
        futures = [executor.submit(_fetch_profile, args) for args in profile_args]
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            if result and result.get("company_name"):
                # Add content hash for dedup
                result["content_hash"] = compute_content_hash(
                    result.get("company_name", ""),
                    result.get("phone", ""),
                    result.get("source_url", "")
                )
                companies.append(result)
            if i % 20 == 0 or i == len(futures):
                log.info(f"      Profiles: {i}/{len(futures)}")

    # Add to batch buffer BEFORE enrichment so data is saved even if interrupted
    flushed = 0
    for company in companies:
        batch = buffer.add(company)
        if batch:
            flushed += len(batch)

    # Enrich (slow — visits company websites for emails/phones)
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
