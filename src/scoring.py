"""
Lead Generation v2 — Normalization
Phone, email, and website normalization utilities.
"""

import re


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
    if re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        return email
    return ""


def normalize_website(website: str) -> str:
    if not website:
        return ""
    website = website.strip()
    if not website.startswith(("http://", "https://")):
        website = "https://" + website
    return website.rstrip("/")


def normalize_company(company: dict) -> dict:
    company["phone"] = normalize_phone(company.get("phone", ""))
    company["email"] = normalize_email(company.get("email", ""))
    company["website"] = normalize_website(company.get("website", ""))
    company.pop("_all_text", None)
    return company
