"""
Doctors Broker (Medical Practice Brokers) — medical practice listings scraper.

Doctors Broker (doctorsbroker.com) is a PRIMARY seller-side medical-practice
broker. Its /medical-practices page is clean: SOLD deals live on a separate
/sold-practices page, so the main page is active-only.

Structure (verified 2026-07-10):
  - /medical-practices renders one <h5> per listing beginning "For Sale: ..."
    e.g. "For Sale: Exceptional Orthopedic Hand Surgery Practice in Bellevue
    Washington", "For Sale: Established Family Medicine Practice for Sale in
    Prime Oviedo, FL."
  - The heading carries the SPECIALTY + CITY + STATE. Price is usually "Call"
    (teaser-level), so we key on specialty + location, not revenue.

HONEST COUNT: ~14 active. We defensively drop anything the title marks SOLD.

Source: https://doctorsbroker.com/medical-practices
Output: output/doctorsbroker_raw.csv
"""

from __future__ import annotations

import csv
import logging
import os
import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

from utils import (get_session, parse_price, clean_text, parse_location,
                   infer_practice_type)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("doctorsbroker")

BASE_URL = "https://doctorsbroker.com"
LISTINGS_URL = "{}/medical-practices".format(BASE_URL)
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "doctorsbroker_raw.csv")

FIELDNAMES = [
    "source_id", "title", "city", "state", "asking_price", "annual_revenue",
    "ebitda", "practice_type", "description", "broker_name", "listing_url",
    "exam_rooms", "listing_code",
]

FORSALE_RE = re.compile(r"^\s*for\s+sale\s*[:\-]\s*", re.I)
SOLD_RE = re.compile(r"\b(sold|under\s+contract|sale\s+pending)\b", re.I)
PRICE_RE = re.compile(r"\$\s*([\d.,]+\s*(?:MM|M|mil(?:lion)?|k|K)?)")


def clean_title(raw: str) -> str:
    t = FORSALE_RE.sub("", clean_text(raw))
    # some headings double the text; keep the first sentence-length chunk
    t = re.sub(r"\bfor sale\b.*$", "", t, flags=re.I).strip(" .:-")
    return t[:160]


def parse_heading(h) -> Optional[Dict]:
    raw = clean_text(h.get_text())
    if not FORSALE_RE.search(raw):
        return None
    if SOLD_RE.search(raw):
        return None

    title = clean_title(raw)
    if not title:
        return None

    # location: "... in Bellevue Washington" / "... in Oviedo, FL."
    city, state = parse_location(title)
    if not state:
        # try trailing "in <City> <State full/abbr>"
        m = re.search(r"\bin\s+([A-Za-z .'-]+?)(?:,?\s+([A-Z]{2})\b|\s+([A-Za-z]+))\.?$", title)
        if m:
            _, st = parse_location(m.group(0))
            state = st

    # nearest anchor for the detail link
    listing_url = LISTINGS_URL
    par = h.find_parent(["div", "article", "section", "li"])
    if par:
        for a in par.find_all("a", href=True):
            href = a["href"]
            if "/medical-practice" in href.lower() or "/practice" in href.lower() or "/listing" in href.lower():
                listing_url = href if href.startswith("http") else BASE_URL + "/" + href.lstrip("/")
                break

    ptext = clean_text(par.get_text(" ")) if par else title
    asking = None
    m = PRICE_RE.search(ptext)
    if m:
        asking = parse_price(m.group(1))

    practice_type = infer_practice_type(title + " " + ptext)

    slug = re.sub(r"[^a-z0-9]+", "-", title.lower())[:60]
    return {
        "source_id": "doctorsbroker-{}".format(slug),
        "title": title,
        "city": city,
        "state": state,
        "asking_price": asking,
        "annual_revenue": None,
        "ebitda": None,
        "practice_type": practice_type,
        "description": ptext[:600],
        "broker_name": "Doctors Broker (Medical Practice Brokers)",
        "listing_url": listing_url,
        "exam_rooms": None,
        "listing_code": "",
    }


def run() -> List[Dict]:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = get_session()
    logger.info("Fetching Doctors Broker: %s", LISTINGS_URL)
    try:
        resp = session.get(LISTINGS_URL, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.error("Failed to fetch Doctors Broker: %s", e)
        with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    headings = [h for h in soup.find_all(re.compile(r"^h[1-6]$"))
                if FORSALE_RE.search(h.get_text())]
    logger.info("Found %d 'For Sale' headings", len(headings))

    all_listings, seen = [], set()
    for h in headings:
        listing = parse_heading(h)
        if listing and listing["source_id"] not in seen:
            seen.add(listing["source_id"])
            all_listings.append(listing)
            logger.info("  ACTIVE %s — %s — %s",
                        listing["source_id"][:40], listing["state"] or "?",
                        listing["title"][:50])

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_listings)
    logger.info("Wrote %d ACTIVE Doctors Broker listings to %s", len(all_listings), OUTPUT_FILE)
    return all_listings


if __name__ == "__main__":
    results = run()
    print("Done. {} listings saved to {}".format(len(results), OUTPUT_FILE))
