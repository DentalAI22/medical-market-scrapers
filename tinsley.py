"""
Tinsley Medical Practice Brokers — medical practice listings scraper.

Tinsley (tinsleymedicalpracticebrokers.com) is a nationwide, 40+-year PRIMARY
medical-practice broker (seller-side) and our richest DATA source: nearly every
card carries Annual Gross Revenue AND EBITDA right on the index.

Structure (verified 2026-07-10):
  - /listings/ renders <article class="card__single-practice"> blocks.
  - Each card: an <h5> title ("Established Southern California Family Medicine
    Practice"), a body line "Annual Gross Revenue – $707,940 EBITDA – $348,201",
    and a "Learn More" link to /practice/{slug}/.
  - Location + specialty live in the title text (e.g. "Southern California
    Family Medicine", "Western Oklahoma Family Medicine & Concierge").
  - Pagination: /listings/?paged=2, /listings/?paged=3 ...

HONEST COUNT: Tinsley segregates sold to a separate archive; the /listings/ index
is active. We still defensively drop any card whose text/status says SOLD/UNDER
CONTRACT. ~24 active across 2 pages.

Source: https://tinsleymedicalpracticebrokers.com/listings/
Output: output/tinsley_raw.csv
"""

from __future__ import annotations

import csv
import logging
import os
import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

from utils import (get_session, polite_delay, parse_price, clean_text,
                   parse_location, infer_practice_type)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("tinsley")

BASE_URL = "https://tinsleymedicalpracticebrokers.com"
LISTINGS_URL = "{}/listings/".format(BASE_URL)
MAX_PAGES = 6  # index is short; guard against runaway pagination
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "tinsley_raw.csv")

FIELDNAMES = [
    "source_id", "title", "city", "state", "asking_price", "annual_revenue",
    "ebitda", "practice_type", "description", "broker_name", "listing_url",
    "exam_rooms", "listing_code",
]

GROSS_RE = re.compile(
    r"(?:Annual\s+)?Gross\s+Revenue[\s–—:–-]*\$?\s*([\d.,]+\s*(?:MM|M|mil(?:lion)?|k|K)?)",
    re.I)
EBITDA_RE = re.compile(
    r"(?:EBITDA|SDE|Cash\s*Flow|Net\s+Income)[\s–—:–-]*\$?\s*([\d.,]+\s*(?:MM|M|mil(?:lion)?|k|K)?)",
    re.I)
SOLD_RE = re.compile(r"\b(sold|under\s+contract|sale\s+pending)\b", re.I)


def slug_from_url(url: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-",
                  url.rstrip("/").split("/")[-1].lower())[:60]


def parse_card(art) -> Optional[Dict]:
    card_text = clean_text(art.get_text(" "))
    if SOLD_RE.search(card_text):
        return None  # honest: drop sold/pending

    h = art.find(["h5", "h4", "h3", "h2"])
    title = clean_text(h.get_text()) if h else ""
    if not title:
        return None

    listing_url = ""
    for a in art.find_all("a", href=True):
        if "/practice/" in a["href"] or "/listing" in a["href"]:
            listing_url = a["href"].split("?")[0]
            break
    if listing_url and not listing_url.startswith("http"):
        listing_url = BASE_URL + listing_url

    annual_revenue = None
    m = GROSS_RE.search(card_text)
    if m:
        v = parse_price(m.group(1))
        if v and v >= 50_000:
            annual_revenue = v

    ebitda = None
    m = EBITDA_RE.search(card_text)
    if m:
        v = parse_price(m.group(1))
        if v and v >= 10_000:
            ebitda = v

    city, state = parse_location(title)
    if not state:
        _, state = parse_location(card_text)

    practice_type = infer_practice_type(title + " " + card_text)

    desc = card_text
    if title and desc.startswith(title):
        desc = desc[len(title):].strip()
    desc = re.sub(r"Learn More\s*$", "", desc).strip()[:600]

    code = slug_from_url(listing_url) if listing_url else ""
    source_id = "tinsley-{}".format(code) if code else "tinsley-{}".format(
        re.sub(r"[^a-z0-9]+", "-", title.lower())[:48])

    return {
        "source_id": source_id,
        "title": title,
        "city": city,
        "state": state,
        "asking_price": None,       # Tinsley leads with revenue/EBITDA, not asking
        "annual_revenue": annual_revenue,
        "ebitda": ebitda,
        "practice_type": practice_type,
        "description": desc,
        "broker_name": "Tinsley Medical Practice Brokers",
        "listing_url": listing_url or LISTINGS_URL,
        "exam_rooms": None,
        "listing_code": "",
    }


def run() -> List[Dict]:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = get_session()
    all_listings, seen = [], set()

    for page in range(1, MAX_PAGES + 1):
        url = LISTINGS_URL if page == 1 else "{}?paged={}".format(LISTINGS_URL, page)
        logger.info("Fetching Tinsley page %d: %s", page, url)
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 404:
                break
            resp.raise_for_status()
        except Exception as e:
            logger.error("Failed to fetch Tinsley page %d: %s", page, e)
            break

        soup = BeautifulSoup(resp.text, "lxml")
        cards = soup.select("article.card__single-practice, article.single-practice")
        if not cards:
            logger.info("No cards on page %d — stopping pagination.", page)
            break
        logger.info("Page %d: %d cards", page, len(cards))

        new_on_page = 0
        for art in cards:
            listing = parse_card(art)
            if listing and listing["source_id"] not in seen:
                seen.add(listing["source_id"])
                all_listings.append(listing)
                new_on_page += 1
                logger.info("  ACTIVE %s — %s — rev $%s ebitda $%s — %s",
                            listing["source_id"], listing["state"] or "?",
                            listing.get("annual_revenue") or "N/A",
                            listing.get("ebitda") or "N/A", listing["title"][:44])
        if new_on_page == 0:
            break
        polite_delay()

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_listings)
    logger.info("Wrote %d ACTIVE Tinsley listings to %s", len(all_listings), OUTPUT_FILE)
    return all_listings


if __name__ == "__main__":
    results = run()
    print("Done. {} listings saved to {}".format(len(results), OUTPUT_FILE))
