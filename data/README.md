# Data directory

Nothing in this directory (except this README) is committed. Recreate locally:

## MTGJSON daily price snapshots

```
python src/fetch_price_snapshot.py
```

Downloads `AllPricesToday` from `https://mtgjson.com/api/v5/` and stores it as
`data/snapshots/allpricestoday_YYYY-MM-DD.json.gz`. Run daily (idempotent —
skips if today's snapshot already exists). MTGJSON rebuilds once per day, so
one run per day captures everything available.

The 90-day `AllPrices` file (backfill at project start) comes from the same
endpoint: `https://mtgjson.com/api/v5/AllPrices.json.gz`.

## Scryfall bulk data

Fetch script to be added in Batch 1. Bulk endpoint documented at
`https://scryfall.com/docs/api/bulk-data` — no API key required.

## Hasbro segment revenue

Quarterly Wizards of the Coast & Digital Gaming segment revenue, extracted
manually from 10-Q/10-K filings on EDGAR (CIK 0000046080). Extraction log and
the resulting CSV schema are documented alongside the file when created.
