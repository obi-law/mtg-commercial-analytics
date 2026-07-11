"""Parse MTGJSON price files (AllPrices backfill + daily snapshots) to parquet.

Streams each input JSON (plain or .gz) with ijson — the 90-day AllPrices file
is too large to json.load comfortably — and emits one tidy long-format parquet
per source file under <data>/processed/prices/:

    uuid | price_date | medium | provider | list_type | finish | price | currency

Defaults keep only paper retail prices (all providers); widen with flags.

Idempotent and incremental: a source file whose output parquet already exists
is skipped, so re-running after each daily snapshot only parses the new file.

Note on overlaps: the backfill and a same-day snapshot can both contain the
same (uuid, date) observations. Dedupe is deferred to the analysis layer —
drop_duplicates on the key columns after concatenating — because analysis
filters to a card subset first, where the operation is cheap.

Usage:
    python src/parse_prices.py                    # scan default locations
    python src/parse_prices.py data/AllPrices_backfill_2026-07-10.json
    python src/parse_prices.py --force            # re-parse everything
    python src/parse_prices.py --list-types retail buylist --media paper mtgo
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import os
import sys
import time
from pathlib import Path

import ijson
import pyarrow as pa
import pyarrow.parquet as pq
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]

BATCH_ROWS = 1_000_000

SCHEMA = pa.schema(
    [
        ("uuid", pa.string()),
        ("price_date", pa.date32()),
        ("medium", pa.string()),      # paper | mtgo
        ("provider", pa.string()),    # tcgplayer, cardmarket, cardkingdom, ...
        ("list_type", pa.string()),   # retail | buylist
        ("finish", pa.string()),      # normal | foil | etched
        ("price", pa.float64()),
        ("currency", pa.string()),
    ]
)


def data_dir(cli_dir: str | None) -> Path:
    """Resolve data directory: CLI arg > DATA_DIR env > ./data (repo-anchored)."""
    load_dotenv(PROJECT_ROOT / ".env")
    base = Path(cli_dir or os.getenv("DATA_DIR", "./data"))
    if not base.is_absolute():
        base = PROJECT_ROOT / base
    return base


def open_json(path: Path):
    """Open plain or gzipped JSON as a binary file object for ijson."""
    if path.name.endswith(".gz"):
        return gzip.open(path, "rb")
    return open(path, "rb")


def out_path_for(src: Path, out_dir: Path) -> Path:
    stem = src.name
    for suffix in (".json.gz", ".json"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return out_dir / f"{stem}.parquet"


def iter_rows(fobj, media: set[str], providers: set[str], list_types: set[str]):
    """Yield tidy rows from an MTGJSON price file's `data` object.

    Structure: data[uuid][medium][provider][list_type][finish][date] = price,
    with `currency` sitting at the provider level. Empty filter sets mean
    "keep everything" at that level.
    """
    for uuid, media_obj in ijson.kvitems(fobj, "data"):
        for medium, provider_map in media_obj.items():
            if media and medium not in media:
                continue
            for provider, pdata in provider_map.items():
                if providers and provider not in providers:
                    continue
                currency = pdata.get("currency")
                for list_type, finish_map in pdata.items():
                    if list_type == "currency":
                        continue
                    if list_types and list_type not in list_types:
                        continue
                    for finish, series in finish_map.items():
                        for date_str, price in series.items():
                            yield (
                                uuid,
                                dt.date.fromisoformat(date_str),
                                medium,
                                provider,
                                list_type,
                                finish,
                                float(price),
                                currency,
                            )


def parse_file(src: Path, dest: Path, media, providers, list_types) -> int:
    """Parse one price file to parquet in batches. Returns row count."""
    start = time.monotonic()
    total = 0
    columns: list[list] = [[] for _ in SCHEMA.names]

    def flush(writer):
        nonlocal total, columns
        if not columns[0]:
            return
        batch = pa.record_batch(
            [pa.array(col, type=field.type) for col, field in zip(columns, SCHEMA)],
            schema=SCHEMA,
        )
        writer.write_batch(batch)
        total += batch.num_rows
        columns = [[] for _ in SCHEMA.names]

    tmp_dest = dest.with_suffix(".parquet.tmp")
    with open_json(src) as fobj, pq.ParquetWriter(tmp_dest, SCHEMA) as writer:
        try:
            for row in iter_rows(fobj, media, providers, list_types):
                for col, value in zip(columns, row):
                    col.append(value)
                if len(columns[0]) >= BATCH_ROWS:
                    flush(writer)
            flush(writer)
        except Exception:
            writer.close()
            tmp_dest.unlink(missing_ok=True)
            raise
    tmp_dest.replace(dest)

    elapsed = time.monotonic() - start
    print(f"  {src.name}: {total:,} rows -> {dest.name} ({elapsed:.0f}s)")
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", help="Specific price files; default scans data/ and data/snapshots/")
    parser.add_argument("--data-dir", help="Override data directory (default: DATA_DIR or ./data)")
    parser.add_argument("--force", action="store_true", help="Re-parse files whose parquet already exists")
    parser.add_argument("--media", nargs="+", default=["paper"], help="paper mtgo (default: paper)")
    parser.add_argument("--providers", nargs="+", default=[], help="Filter providers (default: all)")
    parser.add_argument("--list-types", nargs="+", default=["retail"], help="retail buylist (default: retail)")
    args = parser.parse_args()

    ddir = data_dir(args.data_dir)
    out_dir = ddir / "processed" / "prices"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.files:
        sources = [Path(f) if Path(f).is_absolute() else PROJECT_ROOT / f for f in args.files]
    else:
        sources = sorted(ddir.glob("AllPrices*.json")) + sorted(ddir.glob("AllPrices*.json.gz"))
        sources = [s for s in sources if not s.name.startswith("AllPricesToday")]
        sources += sorted((ddir / "snapshots").glob("*.json.gz"))

    if not sources:
        print("No price files found. Expected AllPrices* under data/ or snapshots under data/snapshots/.")
        return 1

    media, providers, list_types = set(args.media), set(args.providers), set(args.list_types)
    parsed = skipped = 0
    for src in sources:
        dest = out_path_for(src, out_dir)
        if dest.exists() and not args.force:
            skipped += 1
            continue
        parse_file(src, dest, media, providers, list_types)
        parsed += 1

    print(f"Done: {parsed} parsed, {skipped} skipped (already processed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())