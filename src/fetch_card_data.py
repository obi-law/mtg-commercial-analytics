"""Fetch MTGJSON AllPrintings: card attributes and booster collation configs.

AllPrintings carries name, set, rarity, and finishes for every printing in the
same uuid namespace as the price files, plus per-set booster configs (print
sheets and slot weights) used for booster-EV modeling.

Unlike price snapshots, this file is a refreshable dimension table, not a
time series — it is saved un-stamped and simply re-downloaded when stale.

Usage:
    python src/fetch_card_data.py
    python src/fetch_card_data.py --force        # re-download over existing
    python src/fetch_card_data.py --out-dir D:/elsewhere
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_URL = "https://mtgjson.com/api/v5"
FILENAME = "AllPrintings"
CHUNK = 1 << 20  # 1 MiB

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def data_dir(cli_out_dir: str | None) -> Path:
    """Resolve output directory: CLI arg > DATA_DIR env > ./data.

    Relative paths are anchored to the repo root, not the process cwd.
    """
    load_dotenv(PROJECT_ROOT / ".env")
    base = Path(cli_out_dir or os.getenv("DATA_DIR", "./data"))
    if not base.is_absolute():
        base = PROJECT_ROOT / base
    base.mkdir(parents=True, exist_ok=True)
    return base


def download(url: str, dest: Path) -> None:
    """Stream-download url to dest via a temp file (no partial files)."""
    with requests.get(url, stream=True, timeout=600) as resp:
        resp.raise_for_status()
        with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            try:
                for chunk in resp.iter_content(chunk_size=CHUNK):
                    tmp.write(chunk)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise
    shutil.move(tmp_path, dest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", help="Override data directory (default: DATA_DIR or ./data)")
    parser.add_argument("--force", action="store_true", help="Re-download even if the file exists")
    args = parser.parse_args()

    dest = data_dir(args.out_dir) / f"{FILENAME}.json.gz"

    if dest.exists() and not args.force:
        print(f"Already present (use --force to refresh): {dest}")
        return 0

    print(f"Downloading {FILENAME}.json.gz ...")
    download(f"{BASE_URL}/{FILENAME}.json.gz", dest)

    size_mb = dest.stat().st_size / (1 << 20)
    print(f"Saved {dest} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())