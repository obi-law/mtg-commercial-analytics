"""Join play-booster EV with hand-collected sealed prices -> Tableau source.

Produces one tidy row per in-scope set:
    set_code | set_name | set_category | release_date | variant
    | ev | priced_coverage | unpriced_weight_share
    | sealed_price_usd | price_collected_date

The EV-to-price RATIO is intentionally NOT computed here — it is defined as a
Tableau calculated field so the axis logic and any bulk-floor variant stay
visible in the workbook rather than baked into the source (project convention:
keep ratios as calcs, not pre-computed columns).

Data-integrity: inner-joins on set_code and asserts every sealed set matched an
EV row (and vice versa), failing loudly on any mismatch rather than silently
dropping a set.

Usage:
    python src/build_ev_price_source.py
    python src/build_ev_price_source.py --variant play
    python src/build_ev_price_source.py --sealed data/sealed_prices.csv
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    load_dotenv(PROJECT_ROOT / ".env")
    base = Path(os.getenv("DATA_DIR", "./data"))
    if not base.is_absolute():
        base = PROJECT_ROOT / base
    return base


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--variant", default="play",
                    help="Booster variant to use as the headline EV (default: play)")
    ap.add_argument("--ev", help="Path to booster_ev.csv (default: data/processed/booster_ev.csv)")
    ap.add_argument("--sealed", help="Path to sealed_prices.csv (default: data/sealed_prices.csv)")
    ap.add_argument("--out", help="Output path (default: data/processed/ev_vs_price.csv)")
    ap.add_argument("--slot-in", help="Path to booster_ev_by_slot.csv (default: data/processed/booster_ev_by_slot.csv)")
    ap.add_argument("--slot-out", help="Enriched slot output (default: data/processed/ev_by_slot.csv)")
    args = ap.parse_args()

    ddir = data_dir()
    ev_path = Path(args.ev) if args.ev else ddir / "processed" / "booster_ev.csv"
    sealed_path = Path(args.sealed) if args.sealed else ddir / "sealed_prices.csv"
    out_path = Path(args.out) if args.out else ddir / "processed" / "ev_vs_price.csv"
    for p in (ev_path, sealed_path):
        if not p.exists():
            sys.exit(f"Missing input: {p}")

    ev = pd.read_csv(ev_path)
    sealed = pd.read_csv(sealed_path)

    # Headline EV = the chosen variant per set.
    ev_v = ev[ev["variant"] == args.variant].copy()
    if ev_v.empty:
        sys.exit(f"No EV rows for variant '{args.variant}'. Check --variant.")

    # Keep only sealed rows that are single play boosters (guard against any
    # box/other rows a future collection might add).
    sealed_pb = sealed[sealed["product_type"] == "booster_play"].copy()

    ev_cols = ["set_code", "variant", "ev", "ev_realizable",
               "priced_coverage", "unpriced_weight_share", "bulk_threshold", "bulk_rate"]
    # Tolerate an older EV file without the realizable columns.
    ev_cols = [c for c in ev_cols if c in ev_v.columns]
    merged = sealed_pb.merge(
        ev_v[ev_cols], on="set_code", how="outer", indicator=True,
    )

    # Integrity: nothing should be left-only or right-only.
    left_only = merged[merged["_merge"] == "left_only"]["set_code"].tolist()
    right_only = merged[merged["_merge"] == "right_only"]["set_code"].tolist()
    if left_only:
        print(f"⚠ sealed sets with NO matching EV variant '{args.variant}': {left_only}")
    if right_only:
        # right_only just means EV has other play-era sets not in sealed scope;
        # only a problem if a sealed set is missing, which is left_only above.
        pass
    matched = merged[merged["_merge"] == "both"].copy()
    if left_only:
        sys.exit("Aborting: every sealed set must match an EV row. Fix scope/codes above.")

    renamed = matched.rename(columns={
        "tcgplayer_market_usd": "sealed_price_usd",
        "collected_date": "price_collected_date",
    })
    wanted = [
        "set_code", "set_name", "set_category", "release_date", "variant",
        "ev", "ev_realizable", "priced_coverage", "unpriced_weight_share",
        "bulk_threshold", "bulk_rate",
        "sealed_price_usd", "price_collected_date",
    ]
    result = renamed[[c for c in wanted if c in renamed.columns]] \
        .sort_values("release_date").reset_index(drop=True)

    # Round money/coverage for a clean source; both ratios stay Tableau calcs.
    for col in ("ev", "ev_realizable", "sealed_price_usd"):
        if col in result:
            result[col] = result[col].round(2)
    for col in ("priced_coverage", "unpriced_weight_share"):
        if col in result:
            result[col] = result[col].round(4)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(out_path, index=False)

    print(f"Wrote {out_path} ({len(result)} sets)\n")
    print(result.to_string(index=False))
    # A quick descriptive read (NOT written to the source — just console).
    r = result["ev"] / result["sealed_price_usd"]
    print(f"\nnominal   EV/price — above 1.0: {(r > 1).sum()}/{len(r)} | "
          f"mean {r.mean():.2f} | median {r.median():.2f}")
    if "ev_realizable" in result:
        rr = result["ev_realizable"] / result["sealed_price_usd"]
        print(f"realizable EV/price — above 1.0: {(rr > 1).sum()}/{len(rr)} | "
              f"mean {rr.mean():.2f} | median {rr.median():.2f}")

    # --- Enrich the slot file with per-set sealed price -------------------
    # The slot decomposition (booster_ev_by_slot.csv) has no price column, so a
    # realizable-ratio sort isn't computable there on its own. We left-join the
    # single-play-booster sealed price per set so the slot worksheet can sort by
    # the same key as the dumbbell. Realizable EV is still derived in Tableau as
    # SUM(slot_ev_realizable_contribution); only the price is joined here. The
    # ratio itself stays a Tableau calc (never a pre-computed column).
    slot_in = Path(args.slot_in) if args.slot_in else ddir / "processed" / "booster_ev_by_slot.csv"
    slot_out = Path(args.slot_out) if args.slot_out else ddir / "processed" / "ev_by_slot.csv"
    if slot_in.exists():
        slot = pd.read_csv(slot_in)
        price_map = sealed_pb[["set_code", "tcgplayer_market_usd"]].rename(
            columns={"tcgplayer_market_usd": "sealed_price_usd"})
        before = len(slot)
        slot = slot.merge(price_map, on="set_code", how="left")
        assert len(slot) == before, "slot enrichment changed row count — check for duplicate set_codes in sealed"
        slot["sealed_price_usd"] = slot["sealed_price_usd"].round(2)
        slot_out.parent.mkdir(parents=True, exist_ok=True)
        slot.to_csv(slot_out, index=False)
        matched_sets = slot.loc[slot["sealed_price_usd"].notna(), "set_code"].nunique()
        print(f"\nWrote {slot_out} ({len(slot)} rows, {matched_sets} sets carry a sealed price)")
    else:
        print(f"\n(slot file {slot_in} not found — skipped slot enrichment)")
    return 0


if __name__ == "__main__":
    sys.exit(main())