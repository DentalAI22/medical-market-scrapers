#!/usr/bin/env python3
"""
Medical / physician-practice listings normalizer.

Ported from the optometry TOM normalizer (itself accounting TAM, vet TVM, dental
TDPM). Reads every output/<source>_raw.csv, maps each row to the site's Listing
schema (mirrors the medical skin src/lib/types.ts), assigns a persistent
TMM-XXXXX siteId from site_id_registry.json (never renumbers; never collides with
dental's TDPM-, veterinary's TVM-, accounting's TAM-, or optometry's TOM-),
dedupes within + across sources, and writes:
  - listings.json                                     (canonical, this dir)
  - ../medical/public/data/listings.json              (site consumer, if present)
  - ../medicalpractice/public/data/listings.json      (sibling, if present)

The medical flagship keyMetric is "Exam rooms" (field exam_rooms) per
site-config.ts. Medical brokers essentially never publish exam-room counts on
teasers, so that field is usually null; the load-bearing card fields are
annual_revenue and ebitda (published openly) and, where present, asking_price.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from utils import scrub_pii

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("normalizer")

HERE = os.path.dirname(__file__)
OUTPUT_DIR = os.path.join(HERE, "output")
BROKER_CODES_JSON = os.path.join(HERE, "broker_codes.json")
SITE_ID_REGISTRY = os.path.join(HERE, "site_id_registry.json")
LISTINGS_JSON = os.path.join(HERE, "listings.json")

SITE_DATA_TARGETS = [
    os.path.join(HERE, "..", "medical", "public", "data", "listings.json"),
    os.path.join(HERE, "..", "medicalpractice", "public", "data", "listings.json"),
]

SITE_PREFIX = "TMM"   # The Medical (Practice) Market
BASE_SITE_ID = 1

_codes = None


def load_codes() -> Dict:
    global _codes
    if _codes is None:
        with open(BROKER_CODES_JSON) as f:
            _codes = json.load(f)
    return _codes


def to_int(v) -> Optional[int]:
    if v in (None, "", "None"):
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


# --- siteId registry (persistent, stable, never renumber) -------------------

def load_registry():
    if os.path.exists(SITE_ID_REGISTRY):
        with open(SITE_ID_REGISTRY) as f:
            d = json.load(f)
        return d.get("next_id", BASE_SITE_ID), d.get("map", {})
    return BASE_SITE_ID, {}


def save_registry(next_id: int, id_map: Dict) -> None:
    with open(SITE_ID_REGISTRY, "w") as f:
        json.dump({"prefix": SITE_PREFIX, "base": BASE_SITE_ID,
                   "next_id": next_id, "map": id_map}, f, indent=2)


def assign_site_ids(listings: List[Dict]) -> None:
    next_id, id_map = load_registry()
    used = set(id_map.values())
    for l in listings:
        key = l["source_id"]
        if key in id_map:
            num = id_map[key]
        else:
            while next_id in used:
                next_id += 1
            num = next_id
            id_map[key] = num
            used.add(num)
            next_id += 1
        l["siteId"] = "{}-{:05d}".format(SITE_PREFIX, num)
    save_registry(next_id, id_map)


# --- normalization ----------------------------------------------------------

def broker_ref(source_key: str, listing_code: str) -> str:
    codes = load_codes()
    meta = codes.get("sources", {}).get(source_key, {})
    prefix = meta.get("ref_prefix", source_key.upper())
    code = (listing_code or "").strip()
    if code and not re.fullmatch(r"[A-Za-z]{1,4}[-]?\d{1,6}[A-Za-z]?", code):
        return prefix
    return "{} #{}".format(prefix, code) if code else prefix


def redacted_name(practice_type: str) -> str:
    """Never store real practice names. Emit a generic descriptor from the type."""
    pt = (practice_type or "").strip().lower()
    mapping = {
        "family medicine": "Family Medicine Practice",
        "internal medicine": "Internal Medicine / Primary Care Practice",
        "pediatrics": "Pediatric Practice",
        "urgent care": "Urgent Care Clinic",
        "multi-specialty": "Multi-Specialty Medical Group",
        "concierge": "Concierge / Direct-Care Practice",
        "ob/gyn": "OB/GYN Practice",
    }
    return mapping.get(pt, "Medical Practice")


def normalize_row(source_key: str, row: Dict, today: str, recent_cutoff: str) -> Optional[Dict]:
    codes = load_codes()
    meta = codes.get("sources", {}).get(source_key, {})

    title = (row.get("title") or "").strip()
    state = (row.get("state") or "").strip().upper()
    if not title:
        return None

    scraped = row.get("scraped_date") or today
    is_new = scraped >= recent_cutoff

    exam_rooms = to_int(row.get("exam_rooms"))
    annual_revenue = to_int(row.get("annual_revenue"))
    ebitda = to_int(row.get("ebitda"))

    return {
        "source_id": row.get("source_id") or "",  # internal key (dropped before write)
        "id": row.get("source_id") or "",
        "siteId": "",  # filled by assign_site_ids
        "source": source_key,
        "source_url": row.get("listing_url") or meta.get("broker_url", ""),
        "type": row.get("practice_type") or "Other",
        "state": state,
        "city": (row.get("city") or "").strip(),
        "asking_price": to_int(row.get("asking_price")),
        "annual_revenue": annual_revenue,
        "annual_collections": annual_revenue,  # medical "collections" == revenue teaser
        "ebitda": ebitda,
        "key_metric_value": exam_rooms,  # site keyMetric field = exam_rooms
        "exam_rooms": exam_rooms,
        "broker_name": row.get("broker_name") or meta.get("broker_name", ""),
        "broker_company": row.get("broker_name") or meta.get("broker_name", ""),
        "broker_url": meta.get("broker_url", ""),
        "broker_ref": broker_ref(source_key, row.get("listing_code", "")),
        "description": scrub_pii(row.get("description") or ""),
        "business_name_redacted": redacted_name(row.get("practice_type", "")),
        "scraped_date": scraped,
        "is_new": is_new,
    }


def dedupe(listings: List[Dict]) -> List[Dict]:
    """Cross-source dedupe. Same source_id, or same (state, revenue) signature
    with a very similar title, collapses to one (keep the richer)."""
    by_key: Dict[str, Dict] = {}
    order: List[str] = []
    for l in listings:
        sig_bits = [l.get("state", ""), str(l.get("annual_revenue") or ""),
                    str(l.get("asking_price") or "")]
        title_norm = re.sub(r"[^a-z0-9]", "", (l.get("title") or "").lower())[:24]
        strong = (l.get("annual_revenue") or l.get("asking_price"))
        key = l["source_id"]
        if strong and title_norm:
            key = "|".join(sig_bits + [title_norm])
        if key in by_key:
            def score(x):
                return sum(1 for k in ("asking_price", "annual_revenue", "ebitda",
                                       "exam_rooms", "city", "description")
                           if x.get(k))
            if score(l) > score(by_key[key]):
                by_key[key] = l
        else:
            by_key[key] = l
            order.append(key)
    return [by_key[k] for k in order]


def run() -> List[Dict]:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    recent_cutoff = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
    codes = load_codes()
    known = set(codes.get("sources", {}).keys())

    all_norm: List[Dict] = []
    if os.path.isdir(OUTPUT_DIR):
        for fname in sorted(os.listdir(OUTPUT_DIR)):
            if not fname.endswith("_raw.csv"):
                continue
            source_key = fname[:-len("_raw.csv")]
            if source_key not in known:
                logger.warning("Skipping unknown source file: %s", fname)
                continue
            path = os.path.join(OUTPUT_DIR, fname)
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            n = 0
            for r in rows:
                nr = normalize_row(source_key, r, today, recent_cutoff)
                if nr:
                    all_norm.append(nr)
                    n += 1
            logger.info("%-16s %d rows -> %d normalized", source_key, len(rows), n)

    before = len(all_norm)
    all_norm = dedupe(all_norm)
    logger.info("Deduped %d -> %d", before, len(all_norm))

    assign_site_ids(all_norm)

    all_norm.sort(key=lambda x: (not x.get("is_new"), x.get("state", "")))

    public = []
    for l in all_norm:
        d = dict(l)
        d.pop("source_id", None)
        public.append(d)

    with open(LISTINGS_JSON, "w") as f:
        json.dump(public, f, indent=2)
    for target in SITE_DATA_TARGETS:
        site_root = os.path.dirname(os.path.dirname(os.path.dirname(target)))
        if not os.path.isdir(site_root):
            logger.info("Skipping sibling write (not present): %s",
                        os.path.relpath(target, HERE))
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as f:
            json.dump(public, f, indent=2)
        logger.info("Wrote %d listings -> %s", len(public), os.path.relpath(target, HERE))

    logger.info("Wrote %d listings -> listings.json", len(public))
    return public


if __name__ == "__main__":
    out = run()
    print("Done. {} listings normalized.".format(len(out)))
