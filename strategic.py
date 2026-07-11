"""
Strategic Medical Brokers — medical practice listings scraper.

Strategic Medical Brokers (strategicmedicalbrokers.com) is a physician-owned,
seller-side PRIMARY broker and our DEEPEST medical source. Its listing index
paginates at /listing/page/{n}/ with ~10 detail links per page.

Structure (verified 2026-07-10):
  - Browse page /browse-listings/ + paginated /listing/page/{n}/.
  - Detail URLs: /listing/{slug}/ where the slug is dense with location + revenue
    + specialty, e.g. "turnkey-family-practice-omaha-ne-metro-2-8m-rev-runs-w-o-owner",
    "mohs-surgery-derm-coastal-ct-6-7m-collections-3-4m-sde".
  - The index mixes ACTIVE with a growing SOLD archive as you paginate (page 1-2
    mostly active, page 4+ increasingly sold). We fetch the card title/status and
    DROP anything marked SOLD/UNDER CONTRACT. The slug carries the state, revenue
    band and specialty — enough to normalize without a per-detail fetch.

HONEST COUNT: ~30 active across pages 1-4 after dropping SOLD. We walk a bounded
number of pages and stop when a page yields no new ACTIVE cards.

Source: https://www.strategicmedicalbrokers.com/browse-listings/
Output: output/strategic_raw.csv
"""

from __future__ import annotations

import csv
import logging
import os
import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

from utils import (get_session, polite_delay, parse_price, clean_text,
                   parse_location, infer_practice_type, STATE_ABBRS)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("strategic")

BASE_URL = "https://strategicmedicalbrokers.com"
BROWSE_URL = "{}/browse-listings/".format(BASE_URL)
PAGE_URL = "{}/listing/page/{{}}/".format(BASE_URL)
MAX_PAGES = 6
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "strategic_raw.csv")

FIELDNAMES = [
    "source_id", "title", "city", "state", "asking_price", "annual_revenue",
    "ebitda", "practice_type", "description", "broker_name", "listing_url",
    "exam_rooms", "listing_code",
]

SOLD_RE = re.compile(r"\b(sold|under\s+contract|sale\s+pending|off[\s-]?market)\b", re.I)
# On the /listing/page/N/ endpoints, the anchor text is DERIVED FROM THE SLUG, so
# a "(SOLD)" prefix collapses to a leading "sold" with NO word boundary — e.g.
# slug "sold-premier-family-medicine..." -> title "Soldpremier Family Medicine".
# \bsold\b misses that, so we ALSO match a leading "sold" token on the slug/title.
SOLD_PREFIX_RE = re.compile(r"^\s*sold[\s\-_]?", re.I)
# revenue from slug: "2-8m-rev", "3m-rev", "6-7m-collections"
SLUG_REV_RE = re.compile(r"(\d+(?:-\d+)?)\s*m(?:m)?[-_](?:rev|collections|revenue|gross)")
SLUG_SDE_RE = re.compile(r"(\d+(?:-\d+)?)\s*m(?:m)?[-_](?:sde|ebitda|net|cash)")
# a "3022-patients" style number we ignore for revenue


def rev_from_slug(slug: str) -> Optional[int]:
    m = SLUG_REV_RE.search(slug)
    if not m:
        return None
    num = m.group(1).replace("-", ".")  # "2-8" -> "2.8"
    try:
        return int(float(num) * 1_000_000)
    except ValueError:
        return None


def sde_from_slug(slug: str) -> Optional[int]:
    m = SLUG_SDE_RE.search(slug)
    if not m:
        return None
    num = m.group(1).replace("-", ".")
    try:
        return int(float(num) * 1_000_000)
    except ValueError:
        return None


def state_from_slug(slug: str) -> str:
    # slugs embed the state as a 2-letter token: "omaha-ne-metro", "central-az",
    # "broward-county-fl", "coastal-ct".
    tokens = re.split(r"[-_]", slug)
    for tok in tokens:
        if tok.upper() in STATE_ABBRS:
            return tok.upper()
    return ""


def title_from_slug(slug: str) -> str:
    words = [w for w in re.split(r"[-_]", slug) if w]
    return " ".join(w.upper() if w.upper() in STATE_ABBRS else w.capitalize()
                    for w in words)


def parse_card_from_link(a, page_text_map) -> Optional[Dict]:
    href = a.get("href", "").split("?")[0]
    if "/listing/" not in href:
        return None
    slug = href.rstrip("/").split("/listing/")[-1].strip("/")
    if not slug or slug in ("page", "category-listings", "browse-listings"):
        return None
    if "/page/" in href:
        return None

    # nearest card text: use the link's own anchor text + parent block text.
    # IMPORTANT: Strategic marks sold with a "(SOLD)" PREFIX inside the anchor
    # text itself (e.g. "(SOLD) Established Pediatric Practice ..."). The parent
    # block sometimes drops that prefix, so we MUST test the anchor text too or
    # sold cards leak through (this is exactly what over-counted 54 vs ~30).
    link_text = clean_text(a.get_text(" "))
    parent = a.find_parent(["article", "div", "li"])
    card_text = clean_text(parent.get_text(" ")) if parent else link_text

    if (SOLD_RE.search(link_text) or SOLD_RE.search(card_text)
            or SOLD_RE.search(slug)
            or SOLD_PREFIX_RE.match(slug) or SOLD_PREFIX_RE.match(link_text)):
        return None

    listing_url = href if href.startswith("http") else BASE_URL + href
    title = link_text if len(link_text) > 12 else title_from_slug(slug)

    annual_revenue = rev_from_slug(slug)
    ebitda = sde_from_slug(slug)
    state = state_from_slug(slug)
    if not state:
        _, state = parse_location(card_text)

    practice_type = infer_practice_type(title + " " + slug.replace("-", " ") + " " + card_text)

    desc = card_text[:600]

    return {
        "source_id": "strategic-{}".format(slug[:60]),
        "title": title[:140],
        "city": "",
        "state": state,
        "asking_price": None,
        "annual_revenue": annual_revenue,
        "ebitda": ebitda,
        "practice_type": practice_type,
        "description": desc,
        "broker_name": "Strategic Medical Brokers",
        "listing_url": listing_url,
        "exam_rooms": None,
        "listing_code": "",
    }


def collect_links(soup) -> List:
    return [a for a in soup.find_all("a", href=True)
            if "/listing/" in a.get("href", "") and "/page/" not in a.get("href", "")]


def run() -> List[Dict]:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = get_session()
    all_listings, seen = [], set()

    # Use the /listing/page/N/ ARCHIVE exclusively — it is the honest ordering:
    # active listings cluster on the first pages, then a SOLD wall (verified
    # 2026-07-10: p1=10 active, p2=10, p3=8/2 sold, p4=2/8 sold, p5+=all sold).
    # We STOP the moment a page yields 0 active AND is dominated by sold, so we
    # capture the ~30 honest active set and never crawl the sold archive. Mixing
    # in /browse-listings/ inflated the count, so it's dropped.
    for i in range(1, MAX_PAGES + 1):
        url = PAGE_URL.format(i)
        logger.info("Fetching Strategic archive page %d: %s", i, url)
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 404:
                break
            resp.raise_for_status()
        except Exception as e:
            logger.error("Failed to fetch Strategic page %d: %s", i, e)
            break

        soup = BeautifulSoup(resp.text, "lxml")
        links = collect_links(soup)
        if not links:
            logger.info("No listing links on page %d — stopping.", i)
            break

        new_on_page = 0
        sold_on_page = 0
        page_seen = set()
        for a in links:
            href = a.get("href", "").split("?")[0]
            slug = href.rstrip("/").split("/listing/")[-1].strip("/")
            if not slug or slug in page_seen:
                continue
            page_seen.add(slug)
            listing = parse_card_from_link(a, None)
            if listing is None:
                sold_on_page += 1
                continue
            if listing["source_id"] not in seen:
                seen.add(listing["source_id"])
                all_listings.append(listing)
                new_on_page += 1
                logger.info("  ACTIVE %s — %s — rev $%s — %s",
                            listing["source_id"][:40], listing["state"] or "?",
                            listing.get("annual_revenue") or "N/A",
                            listing["title"][:40])
        logger.info("Page %d: %d new active, %d sold-dropped", i, new_on_page, sold_on_page)
        # Hit the sold wall: no new active this page -> the rest of the archive
        # is sold. Stop (honest active set captured).
        if new_on_page == 0:
            break
        polite_delay()

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_listings)
    logger.info("Wrote %d ACTIVE Strategic listings to %s", len(all_listings), OUTPUT_FILE)
    return all_listings


if __name__ == "__main__":
    results = run()
    print("Done. {} listings saved to {}".format(len(results), OUTPUT_FILE))
