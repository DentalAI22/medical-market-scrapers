#!/usr/bin/env python3
"""
Master medical / physician-practice scraper runner — mirrors the optometry TOM
run_all.py.

Usage:
    python run_all.py                    # Run all scrapers + normalize
    python run_all.py --only tinsley
    python run_all.py --normalize        # Re-normalize existing CSVs (no scraping)

Sources (all PRIMARY seller-side brokers — public, no-login, polite-fetch; same
discipline as dental/vet/acct/optometry). Counts are honest ACTIVE (SOLD/pending
dropped in each module):
    tinsley         Tinsley Medical Practice Brokers   ~24 active  (gross + EBITDA on card)
    strategic       Strategic Medical Brokers          ~30 active  (walks pages 1-4; drops SOLD)
    doctorsbroker   Doctors Broker / Medical Practice   ~14 active  (SOLD segregated off-page)
    synergy         Synergy Business Brokers (medical)  ~15 active  (revenue + cash flow + asking)

BLOCKED (never scraped — same blocklist as dental/vet/acct/optometry): BizBuySell,
BizQuest, LoopNet, DealStream, BusinessBroker.net, PracticeOrbit, Provide/TUSK,
Sunbelt, Transworld.

REJECTED (verified but NOT scraped, documented in broker_codes.json
rejected_sources): American Healthcare Capital (platform/PE-scale deals, location
mostly "Not Disclosed" — too coarse for a location-first marketplace); Vertess
(bot-blocks plain fetch / JS-rendered); Practice Transitions Group (mostly dental;
only ~7 medical, revisit if we widen); medicalpracticelistings.com (parked domain);
1st Med Transitions (medical page ~empty; its volume is dental).

HONEST NOTE: medical is a DENSE direct-broker vertical (~80-90 active from these
four; ~110-120 network-wide). Full financials/identity sit behind NDA — we
aggregate teasers (specialty + metro + revenue band), which is exactly the
no-pricing-opinion, broker-is-the-gatekeeper doctrine.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("run_all")

# (display_name, module_name)
SCRAPERS = [
    ("Tinsley Medical Practice Brokers", "tinsley"),
    ("Strategic Medical Brokers", "strategic"),
    ("Doctors Broker (Medical Practice Brokers)", "doctorsbroker"),
    ("Synergy Business Brokers (Medical)", "synergy"),
]


def run_scraper(name, module_name):
    logger.info("=" * 60)
    logger.info("STARTING: %s", name)
    logger.info("=" * 60)
    try:
        mod = importlib.import_module(module_name)
        results = mod.run()
        count = len(results) if results else 0
        logger.info("%s: %d listings", name, count)
        return count
    except Exception as e:
        logger.error("%s failed: %s", name, e)
        return 0


def main():
    parser = argparse.ArgumentParser(description="Run medical listing scrapers")
    parser.add_argument("--only", type=str, help="Run one scraper by module name")
    parser.add_argument("--normalize", action="store_true", help="Only normalize existing CSVs")
    args = parser.parse_args()

    start = time.time()
    results = {}

    if not args.normalize:
        if args.only:
            matched = False
            for name, module_name in SCRAPERS:
                if module_name == args.only:
                    results[name] = run_scraper(name, module_name)
                    matched = True
                    break
            if not matched:
                logger.error("Unknown scraper: %s", args.only)
                logger.info("Available: %s", ", ".join(m for _, m in SCRAPERS))
                return 1
        else:
            for name, module_name in SCRAPERS:
                results[name] = run_scraper(name, module_name)

    logger.info("=" * 60)
    logger.info("STARTING: Normalizer")
    logger.info("=" * 60)
    try:
        import normalizer
        merged = normalizer.run()
        results["normalized"] = len(merged) if merged else 0
    except Exception as e:
        logger.error("Normalizer failed: %s", e)
        results["normalized"] = 0

    elapsed = time.time() - start
    logger.info("=" * 60)
    logger.info("MEDICAL SCRAPER RUN COMPLETE — %.1fs", elapsed)
    logger.info("=" * 60)
    for source, count in results.items():
        logger.info("  %-42s %d", source, count)

    total = results.get("normalized", 0)
    print("\nDone. {} total medical listings in listings.json ({:.1f}s)".format(total, elapsed))
    return 0 if total > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
