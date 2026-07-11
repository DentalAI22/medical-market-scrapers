# medical-market-scrapers

Daily aggregation of **real, active medical / physician-practice listings** for
**The Medical Practice Market** (themedicalpracticemarket.com) — the medical
vertical of the Practice Market network (dental → veterinary → accounting →
optometry → **medical**).

This repo is the **data pipeline only**. The live site repos PULL `listings.json`
from this repo's public raw URL at build time — there are no cross-repo push
credentials. A GitHub Action re-scrapes and re-commits `listings.json` daily.

## What it does

1. Each `*.py` scraper fetches ONE primary broker's public, no-login listings
   page, drops SOLD/pending, scrubs PII, and writes `output/<source>_raw.csv`.
2. `normalizer.py` merges every CSV to the site Listing schema, assigns a stable
   `TMM-XXXXX` siteId (persistent, never renumbered — registry in
   `site_id_registry.json`), dedupes, and writes `listings.json`.
3. `run_all.py` runs all scrapers then the normalizer.
4. `.github/workflows/scrape.yml` runs it daily at **09:30 UTC** and commits the
   refreshed dataset back to this repo (with a collapse guard — a near-empty
   scrape is rejected so it can never wipe the live sites).

```bash
pip install -r requirements.txt
python run_all.py               # all scrapers + normalize
python run_all.py --only tinsley
python run_all.py --normalize   # re-normalize existing CSVs, no scraping
```

## Sources (PRIMARY seller-side brokers only)

All are single-firm brokers representing their OWN sellers — **not** aggregators.
Counts are honest ACTIVE (SOLD/pending dropped per module):

| Source | Firm | Active | Notes |
|---|---|---:|---|
| `tinsley` | Tinsley Medical Practice Brokers | ~24 | Gross revenue + EBITDA on every card; richest data |
| `strategic` | Strategic Medical Brokers | ~30 | Deepest; archive walked to the SOLD wall, sold dropped |
| `doctorsbroker` | Doctors Broker (Medical Practice Brokers) | ~15 | SOLD segregated off-page; specialty + city/state in title |
| `synergy` | Synergy Business Brokers (medical category) | ~18 | Revenue + net cash flow + asking price |

**~87 active listings total** across 4 brokers (as of launch).

### Blocked aggregators (NEVER scraped — absolute, network-wide)
BizBuySell, BizQuest, LoopNet, DealStream, BusinessBroker.net, PracticeOrbit,
Provide/TUSK, Sunbelt, Transworld, and any site that reposts many brokers'
listings without being the broker.

### Rejected (verified but not used — see `broker_codes.json` `rejected_sources`)
American Healthcare Capital (PE/platform-scale, location "Not Disclosed"), Vertess
(JS/bot-blocked), Practice Transitions Group (mostly dental), 1st Med Transitions
(medical page ~empty), medicalpracticelistings.com (parked domain), CARR
(healthcare real estate, not practices).

## Doctrine honored
- **$0** — pure stdlib + requests/bs4; public GitHub Actions minutes (free).
- **FSBO → broker-finder** (no owner-listed inventory); **no pricing opinions**;
  **no days-on-market**; SOLD dropped; **0 PII** (emails/phones/license#s scrubbed).
- **siteId permanence** — the normalizer only assigns; never renumbers.
- **Retain-last-good** — the Action refuses to commit a collapsed dataset.
- Teaser-level data only (specialty + metro + revenue band); the broker remains
  the gatekeeper to full identity/financials.

Commit author: `jonathan@tdibroker.com`.
