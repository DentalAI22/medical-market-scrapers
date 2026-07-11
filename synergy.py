"""
Synergy Business Brokers — medical practices for sale scraper.

Synergy (synergybb.com) is a PRIMARY M&A brokerage (not an aggregator — it
represents its own sellers) with a dedicated medical-practices category. Cards
publish asking price, Annual Revenue AND Net Cash Flow openly.

Structure (verified 2026-07-10):
  - /businesses-for-sale/medical-practices-for-sale/ renders
    <div class="sale-list-item-content"> cards. Each card:
      * a title link to /listings/{slug}/
      * "$18,000,000" asking (sometimes absent / "Contact for Details")
      * "Annual Revenue: $6,578,488 Net Cash Flow: $3,376,803"
      * a paragraph description.
  - SOLD examples are shown on the page too — we DROP any card marked Sold.

HONEST COUNT: ~15 active medical listings (the page also links a few sold refs).

Source: https://synergybb.com/businesses-for-sale/medical-practices-for-sale/
Output: output/synergy_raw.csv
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
logger = logging.getLogger("synergy")

BASE_URL = "https://synergybb.com"
LISTINGS_URL = "{}/businesses-for-sale/medical-practices-for-sale/".format(BASE_URL)
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "synergy_raw.csv")

FIELDNAMES = [
    "source_id", "title", "city", "state", "asking_price", "annual_revenue",
    "ebitda", "practice_type", "description", "broker_name", "listing_url",
    "exam_rooms", "listing_code",
]

REV_RE = re.compile(r"Annual\s+Revenue:\s*\$?\s*([\d.,]+)", re.I)
CF_RE = re.compile(r"(?:Net\s+Cash\s+Flow|Cash\s+Flow|SDE|EBITDA):\s*\$?\s*([\d.,]+)", re.I)
ASKING_RE = re.compile(r"\$\s*([\d][\d.,]{3,})")
SOLD_RE = re.compile(r"\b(sold|under\s+contract|sale\s+pending)\b", re.I)


def parse_card(card) -> Optional[Dict]:
    card_text = clean_text(card.get_text(" "))
    if SOLD_RE.search(card_text):
        return None

    a = None
    for link in card.find_all("a", href=True):
        if "/listings/" in link["href"]:
            a = link
            break
    if a is None:
        return None
    href = a["href"].split("?")[0]
    listing_url = href if href.startswith("http") else BASE_URL + href
    slug = href.rstrip("/").split("/listings/")[-1].strip("/")
    if not slug:
        return None

    title = clean_text(a.get_text(" "))
    if not title or len(title) < 6:
        # fall back to a heading in the card
        h = card.find(re.compile(r"^h[1-6]$"))
        title = clean_text(h.get_text()) if h else slug.replace("-", " ").title()
    title = title[:160]

    annual_revenue = None
    m = REV_RE.search(card_text)
    if m:
        annual_revenue = parse_price(m.group(1))

    ebitda = None
    m = CF_RE.search(card_text)
    if m:
        ebitda = parse_price(m.group(1))

    # asking price = the first big $ that isn't the revenue/cashflow figure
    asking = None
    for m in ASKING_RE.finditer(card_text):
        val = parse_price(m.group(1))
        if val and val != annual_revenue and val != ebitda and val >= 50_000:
            asking = val
            break

    city, state = parse_location(title)
    if not state:
        _, state = parse_location(card_text)

    practice_type = infer_practice_type(title + " " + card_text)

    # description: strip the title + money lines to a clean prose tail
    desc = card_text
    desc = re.sub(r"Annual Revenue:.*?(?=[A-Z])", "", desc)
    desc = desc[:600]

    return {
        "source_id": "synergy-{}".format(slug[:60]),
        "title": title,
        "city": city,
        "state": state,
        "asking_price": asking,
        "annual_revenue": annual_revenue,
        "ebitda": ebitda,
        "practice_type": practice_type,
        "description": desc,
        "broker_name": "Synergy Business Brokers",
        "listing_url": listing_url,
        "exam_rooms": None,
        "listing_code": "",
    }


def run() -> List[Dict]:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = get_session()
    logger.info("Fetching Synergy medical listings: %s", LISTINGS_URL)
    try:
        resp = session.get(LISTINGS_URL, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.error("Failed to fetch Synergy: %s", e)
        with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    cards = soup.select("div.sale-list-item-content, div.sale-list-item, article.sale-list-item")
    if not cards:
        # fallback: group by /listings/ anchors' parent blocks
        anchors = [a for a in soup.find_all("a", href=True) if "/listings/" in a.get("href", "")]
        cards = []
        seen_par = set()
        for a in anchors:
            par = a.find_parent(["div", "article", "li"])
            if par is not None and id(par) not in seen_par:
                seen_par.add(id(par))
                cards.append(par)
    logger.info("Found %d Synergy medical cards", len(cards))

    all_listings, seen = [], set()
    for card in cards:
        listing = parse_card(card)
        if listing and listing["source_id"] not in seen:
            seen.add(listing["source_id"])
            all_listings.append(listing)
            logger.info("  ACTIVE %s — %s — rev $%s — %s",
                        listing["source_id"][:40], listing["state"] or "?",
                        listing.get("annual_revenue") or "N/A", listing["title"][:44])

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_listings)
    logger.info("Wrote %d ACTIVE Synergy medical listings to %s", len(all_listings), OUTPUT_FILE)
    return all_listings


if __name__ == "__main__":
    results = run()
    print("Done. {} listings saved to {}".format(len(results), OUTPUT_FILE))
