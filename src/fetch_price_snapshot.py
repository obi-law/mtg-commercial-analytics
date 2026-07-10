"""Fetch today's MTGJSON price snapshot and store it date-stamped.

MTGJSON's AllPrices file keeps only a rolling ~90-day window, so we accumulate
our own history: one compressed AllPricesToday snapshot per day.

Usage:
    python src/fetch_price_snapshot.py
    python src/fetch_price_snapshot.py --out-dir D:/some/other/location

Idempotent: exits early if today's snapshot already exists.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import os
import shutil
import sys
import tempfile
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_URL = "https://mtgjson.com/api/v5"
FILENAME = "AllPricesToday"
CHUNK = 1 << 20  # 1 MiB

PROJECT_ROOT = Path(__file__).resolve().parents[1]

def snapshot_dir(cli_out_dir: str | None) -> Path:
    """Resolve output directory: CLI arg > DATA_DIR env > ./data.

    Relative paths are anchored to the repo root, not the process cwd,
    so the script behaves identically from PyCharm, terminal, or Task
    Scheduler.
    """
    load_dotenv(PROJECT_ROOT / ".env")
    base = Path(cli_out_dir or os.getenv("DATA_DIR", "./data"))
    if not base.is_absolute():
        base = PROJECT_ROOT / base
    out = base / "snapshots"
    out.mkdir(parents=True, exist_ok=True)
    return out


def download(url: str, dest: Path, compress: bool) -> None:
    """Stream-download url to dest; gzip on the fly if compress is True."""
    with requests.get(url, stream=True, timeout=300) as resp:
        resp.raise_for_status()
        # Write to a temp file first so a failed download never leaves a
        # partial file that the idempotency check would mistake for complete.
        with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            try:
                if compress:
                    with gzip.open(tmp, "wb") as gz:
                        for chunk in resp.iter_content(chunk_size=CHUNK):
                            gz.write(chunk)
                else:
                    for chunk in resp.iter_content(chunk_size=CHUNK):
                        tmp.write(chunk)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise
    shutil.move(tmp_path, dest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", help="Override data directory (default: DATA_DIR or ./data)")
    args = parser.parse_args()

    today = dt.date.today().isoformat()
    dest = snapshot_dir(args.out_dir) / f"allpricestoday_{today}.json.gz"

    if dest.exists():
        print(f"Snapshot already exists, nothing to do: {dest}")
        return 0

    # Prefer the pre-gzipped file; fall back to plain JSON gzipped locally.
    try:
        print(f"Downloading {FILENAME}.json.gz ...")
        download(f"{BASE_URL}/{FILENAME}.json.gz", dest, compress=False)
    except requests.HTTPError:
        print("Pre-gzipped file unavailable; falling back to plain JSON.")
        download(f"{BASE_URL}/{FILENAME}.json", dest, compress=True)

    size_mb = dest.stat().st_size / (1 << 20)
    print(f"Saved {dest} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
