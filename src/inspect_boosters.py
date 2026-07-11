"""Inspect MTGJSON booster-config structure for in-scope sets.

Reads AllPrintings locally (no network) and reports, per candidate set:
  - whether a booster config exists and which variants (default/play/draft/...)
  - the sheets, their foil flag and totalWeight, card counts
  - the weighted booster contents and total weight
  - card-level fields the EV engine relies on (rarity, finishes)

Run this FIRST. Its output confirms the data shape the EV engine assumes.
Nothing is computed or written — this only prints.

Usage:
    python src/inspect_boosters.py
    python src/inspect_boosters.py --set DFT
    python src/inspect_boosters.py --since 2022-01-01
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    load_dotenv(PROJECT_ROOT / ".env")
    base = Path(os.getenv("DATA_DIR", "./data"))
    if not base.is_absolute():
        base = PROJECT_ROOT / base
    return base


def load_allprintings(ddir: Path) -> dict:
    """Load AllPrintings.json or .json.gz, whichever exists."""
    plain = ddir / "AllPrintings.json"
    gz = ddir / "AllPrintings.json.gz"
    if plain.exists():
        print(f"Loading {plain} ...")
        with open(plain, encoding="utf-8") as f:
            return json.load(f)["data"]
    if gz.exists():
        print(f"Loading {gz} ...")
        with gzip.open(gz, "rt", encoding="utf-8") as f:
            return json.load(f)["data"]
    sys.exit(f"AllPrintings.json[.gz] not found in {ddir}")


def is_premier(setobj: dict) -> bool:
    """Heuristic: core/expansion sets are the 'premier' releases we scope to."""
    return setobj.get("type") in {"core", "expansion"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--set", help="Inspect a single set code and dump full detail")
    ap.add_argument("--since", default="2022-01-01", help="Earliest release date (default 2022-01-01)")
    args = ap.parse_args()

    data = load_allprintings(data_dir())

    if args.set:
        codes = [args.set.upper()]
    else:
        codes = sorted(
            code for code, s in data.items()
            if is_premier(s)
            and (s.get("releaseDate") or "0000") >= args.since
            and s.get("booster")
        )
        print(f"\nIn-scope premier sets with booster configs since {args.since}: {len(codes)}")
        for code in codes:
            s = data[code]
            variants = list((s.get("booster") or {}).keys())
            print(f"  {code:6} {s.get('releaseDate')}  {s.get('name'):40} variants={variants}")
        print("\nRe-run with --set CODE to see one set's full booster structure.\n")
        # Still dump the first one so we see structure immediately.
        if codes:
            codes = codes[:1]
            print(f"--- auto-detail for {codes[0]} ---")

    for code in codes:
        s = data.get(code)
        if not s:
            print(f"{code}: not found"); continue
        print(f"\n==================== {code}: {s.get('name')} ====================")
        print(f"type={s.get('type')} releaseDate={s.get('releaseDate')} #cards={len(s.get('cards', []))}")
        booster = s.get("booster") or {}
        if not booster:
            print("  NO booster config."); continue

        for vname, v in booster.items():
            print(f"\n  ---- variant: {vname} ----")
            print(f"    keys: {list(v.keys())}")
            boosters = v.get("boosters", [])
            print(f"    #weighted booster configs: {len(boosters)}   boostersTotalWeight: {v.get('boostersTotalWeight')}")
            if boosters:
                print(f"    example config[0]: {json.dumps(boosters[0])[:600]}")
            sheets = v.get("sheets", {})
            print(f"    #sheets: {len(sheets)}")
            for sname, sh in sheets.items():
                meta = {k: sh.get(k) for k in ("totalWeight", "foil", "balanceColors", "fixed") if k in sh}
                ncards = len(sh.get("cards", {}))
                print(f"      sheet {sname:22} {meta}  #cards={ncards}")
            # Only fully expand the first variant to keep output readable.
            if not args.set:
                break

        # Card-level fields the EV engine needs.
        print("\n  -- card field availability --")
        cards = s.get("cards", [])
        with_rarity = sum(1 for c in cards if c.get("rarity"))
        finishes_seen = set()
        for c in cards:
            finishes_seen.update(c.get("finishes", []))
        print(f"    cards with rarity: {with_rarity}/{len(cards)}")
        print(f"    finishes seen in set: {sorted(finishes_seen)}")
        sample = cards[0] if cards else {}
        print(f"    sample card: uuid={sample.get('uuid')} rarity={sample.get('rarity')} finishes={sample.get('finishes')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())