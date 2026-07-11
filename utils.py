"""Shared utilities for medical / physician-practice scrapers.

Ported faithfully from the optometry TOM rig (itself accounting TAM, vet TVM,
dental TDPM ~/dental-practice-market-live/scrapers/utils.py). Same polite-fetch
discipline: real browser UA, 1.5-3.5s random delays, tolerant price parsing,
US-state helpers. Medical-practice listings are teaser-level (specialty + metro +
revenue band; full financials/identity behind NDA) — brokers publish gross
revenue + EBITDA/SDE openly on the card, so those are the load-bearing numbers.
"""

from __future__ import annotations

import re
import logging
import time
import random
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}


def get_session() -> requests.Session:
    """Create a requests session with browser-like headers."""
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def polite_delay(min_sec: float = 1.5, max_sec: float = 3.5) -> None:
    """Sleep a random interval to be polite to servers."""
    time.sleep(random.uniform(min_sec, max_sec))


def parse_price(text: Optional[str]) -> Optional[int]:
    """Extract a dollar amount from text like '$455,000', '$1.4MM', '750k'."""
    if not text:
        return None
    text = text.strip().replace(",", "").replace("$", "")
    # "1.4 MM" / "1.2 mil" / "1.2 million" / "1.2M"
    m = re.search(r"([\d.]+)\s*(?:MM\b|mil(?:lion)?\b|M\b)", text, re.I)
    if m:
        return int(float(m.group(1)) * 1_000_000)
    # "600 K" / "600k" / "750k"
    m = re.search(r"([\d.]+)\s*[Kk]\b", text)
    if m:
        return int(float(m.group(1)) * 1_000)
    # plain contiguous integer (>= 4 digits to be a plausible dollar sum)
    m = re.fullmatch(r"\d+", text)
    if m and len(text) >= 4:
        return int(text)
    m = re.search(r"\b(\d{4,})\b", text)
    if m:
        return int(m.group(1))
    return None


def clean_text(text: Optional[str]) -> str:
    """Collapse whitespace and strip a string."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


# --- PII scrub (contact info in broker prose) -------------------------------

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
_CONTACT_RE = re.compile(
    r"\bContact\s+[A-Z][a-z]+.*?(?=\.\s|$)", re.S)
_LIC_RE = re.compile(r"\bLic\.?\s*#?\s*\d+", re.I)
_NDA_RE = re.compile(r"\bNDA Request\b", re.I)


def scrub_pii(text: Optional[str]) -> str:
    """Strip broker/seller contact strings from a description before storage.

    Optometry (esp. Weebly/assoc) descriptions embed 'Contact Frank Lic 130877 -
    425-985-8390 or frank@omni-pg.com'. We never store contact PII — a buyer
    reaches the broker through the source_url, not a scraped phone. Removes
    emails, phones, license numbers, 'Contact <Name> …' tails, and NDA-CTA noise.
    """
    if not text:
        return ""
    t = _EMAIL_RE.sub("", text)
    t = _PHONE_RE.sub("", t)
    t = _LIC_RE.sub("", t)
    t = _CONTACT_RE.sub("", t)
    t = _NDA_RE.sub("", t)
    # collapse leftover 'or  .' / dangling connectors
    t = re.sub(r"\b(?:Contact|Call|Email)\b[\s:.-]*$", "", t, flags=re.I)
    t = re.sub(r"\s*[-–—]\s*(?:or|and)\s*[.,]?\s*", " ", t)
    return clean_text(t)


# --- US state helpers --------------------------------------------------------

STATE_ABBRS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}

STATE_NAME_TO_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}


def parse_location(text: Optional[str]) -> Tuple[str, str]:
    """Best-effort (city, state) from a free-text location string.

    Handles 'Fort Wayne, IN', 'Greater Portland, OR', 'Santa Fe, New Mexico',
    'Ventura County, CA', 'Virginia'. Returns ("", "") if nothing parseable.
    """
    if not text:
        return "", ""
    text = clean_text(text)

    # "City, ST"
    m = re.search(r"([A-Za-z .'-]+?),\s*([A-Z]{2})\b", text)
    if m and m.group(2) in STATE_ABBRS:
        return m.group(1).strip().title(), m.group(2)

    # "City, State Name"
    m = re.search(r"([A-Za-z .'-]+?),\s*([A-Za-z ]+?)$", text)
    if m:
        st = STATE_NAME_TO_ABBR.get(m.group(2).strip().lower())
        if st:
            return m.group(1).strip().title(), st

    # bare state name anywhere
    low = text.lower()
    for name, abbr in STATE_NAME_TO_ABBR.items():
        if re.search(r"\b" + re.escape(name) + r"\b", low):
            return "", abbr

    # bare 2-letter code
    m = re.search(r"\b([A-Z]{2})\b", text)
    if m and m.group(1) in STATE_ABBRS:
        return "", m.group(1)

    return "", ""


def state_from_code(code: Optional[str]) -> str:
    """State abbr from a broker listing code like 'CA-76747', 'WAO116', 'ca2006'.
    The leading 2 letters are the state."""
    if not code:
        return ""
    m = re.match(r"^([A-Za-z]{2})", code.strip())
    if m and m.group(1).upper() in STATE_ABBRS:
        return m.group(1).upper()
    return ""


# --- medical practice-type / specialty inference ----------------------------

def infer_practice_type(text: Optional[str]) -> str:
    """Best-effort medical-practice TYPE (specialty family) from title/desc.
    A wrong guess is worse than the safe default, so only promote off
    'Multi-Specialty' fallback on a clear keyword. Order matters (most-specific
    first). Mirrors the medical site-config businessTypes:
    Family Medicine, Internal Medicine, Pediatrics, Urgent Care, Multi-Specialty,
    Concierge, OB/GYN, Other. Specialty practices that don't map to a listed
    bucket (derm, ortho, cardiology, pain, etc.) fall to 'Other' — the card title
    still carries the true specialty, so nothing is lost."""
    t = (text or "").lower()
    if any(k in t for k in ("concierge", "direct primary care", "membership medicine",
                            "boutique practice", "dpc ")):
        return "Concierge"
    if any(k in t for k in ("urgent care", "walk-in clinic", "walk in clinic", "immediate care")):
        return "Urgent Care"
    if any(k in t for k in ("ob/gyn", "ob-gyn", "obgyn", "obstetrics", "gynecolog",
                            "women's health", "womens health")):
        return "OB/GYN"
    if any(k in t for k in ("pediatric", "peds ", "children's", "childrens")):
        return "Pediatrics"
    if any(k in t for k in ("family medicine", "family practice", "family physician",
                            "general practice")):
        return "Family Medicine"
    if any(k in t for k in ("internal medicine", "internist", "primary care", "geriatric")):
        return "Internal Medicine"
    if any(k in t for k in ("multi-specialty", "multispecialty", "multi specialty",
                            "multidisciplinary", "multi-disciplinary", "group practice",
                            "multi-site", "multi site")):
        return "Multi-Specialty"
    return "Other"
