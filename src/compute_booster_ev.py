"""Compute booster expected value (EV) from MTGJSON collation configs.

Method (per set, per booster variant):
  A booster variant defines `sheets` (weighted lists of card uuids, each sheet
  flagged foil or not) and `boosters` (weighted configurations, each naming how
  many picks come from each sheet). EV is:

      EV = sum over booster-configs  ( config_weight / boostersTotalWeight )
             * sum over sheet-slots  ( num_picks * sheet_expected_value )

  where a sheet's expected value is the WEIGHT-AVERAGED latest price of the
  cards on it (print sheets weight cards unequally), priced against the finish
  implied by the sheet's `foil` flag.

Prices come from the parsed parquet(s): the latest observed price per
(uuid, finish) for the chosen provider/list_type.

Coverage is tracked, not hidden: cards on a sheet with no matching price
contribute 0 to the numerator but their sheet weight is reported as
`unpriced_weight_share`, so the methodology footer can state exactly how much
of each booster's value basis was observed vs. missing.

Outputs (under <data>/processed/):
  booster_ev.csv          one row per (set, variant): EV, price basis, coverage
  booster_ev_by_slot.csv  EV decomposition per sheet/slot (for the Tableau
                          rarity-slot breakdown)

Usage:
    python src/compute_booster_ev.py
    python src/compute_booster_ev.py --provider tcgplayer --since 2022-01-01
    python src/compute_booster_ev.py --set DFT --verbose
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from pathlib import Path

# Manually curated: licensed Universes Beyond premier sets in the analysis
# window. These carry IP-licensing dynamics that distort singles pricing, so we
# flag them for separate comparison rather than pooling with in-universe
# Standard sets. MTGJSON exposes no reliable native UB flag (all are
# type=expansion), so this is a static, auditable list — the script prints the
# full classification each run for review. If MTGJSON later adds a UB flag,
# replace this with a data-driven lookup.
UNIVERSES_BEYOND = frozenset({
    "FIN",  # Final Fantasy
    "MSH",  # Marvel Super Heroes
    "SPM",  # Marvel's Spider-Man
    "TLA",  # Avatar: The Last Airbender
    "TMT",  # Teenage Mutant Ninja Turtles
})


def set_category(code: str) -> str:
    return "universes_beyond" if code.upper() in UNIVERSES_BEYOND else "standard"

import pandas as pd
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# MTGJSON sheet foil flag -> price finish. Etched sheets, when flagged, map to
# 'etched'; the inspector will reveal whether any in-scope set uses them.
def sheet_finish(sheet: dict) -> str:
    if sheet.get("foil"):
        return "foil"
    return "normal"


def data_dir() -> Path:
    load_dotenv(PROJECT_ROOT / ".env")
    base = Path(os.getenv("DATA_DIR", "./data"))
    if not base.is_absolute():
        base = PROJECT_ROOT / base
    return base


def load_allprintings(ddir: Path) -> dict:
    plain, gz = ddir / "AllPrintings.json", ddir / "AllPrintings.json.gz"
    if plain.exists():
        with open(plain, encoding="utf-8") as f:
            return json.load(f)["data"]
    if gz.exists():
        with gzip.open(gz, "rt", encoding="utf-8") as f:
            return json.load(f)["data"]
    sys.exit(f"AllPrintings.json[.gz] not found in {ddir}")


def latest_prices(ddir: Path, provider: str, list_type: str) -> dict[tuple[str, str], float]:
    """Return {(uuid, finish): latest_price} for the chosen provider/list_type.

    Concatenates all parsed parquet files, filters, and keeps the most recent
    price_date per (uuid, finish).
    """
    pdir = ddir / "processed" / "prices"
    files = sorted(pdir.glob("*.parquet"))
    if not files:
        sys.exit(f"No parsed price parquet in {pdir}. Run parse_prices.py first.")
    frames = []
    for f in files:
        df = pd.read_parquet(f, columns=["uuid", "price_date", "provider", "list_type", "finish", "price"])
        df = df[(df["provider"] == provider) & (df["list_type"] == list_type)]
        frames.append(df)
    allp = pd.concat(frames, ignore_index=True)
    # Latest observation per (uuid, finish).
    allp = allp.sort_values("price_date").drop_duplicates(["uuid", "finish"], keep="last")
    print(f"  price basis: {provider}/{list_type} | {len(allp):,} (uuid,finish) latest points "
          f"| dates {allp['price_date'].min()}..{allp['price_date'].max()}")
    return {(r.uuid, r.finish): r.price for r in allp.itertuples(index=False)}


def sheet_expected_value(sheet: dict, prices: dict, finish: str,
                         bulk_threshold: float, bulk_rate: float):
    """Weight-averaged price of cards on a sheet, plus coverage.

    Returns (ev_nominal, ev_realizable, priced_weight, total_weight).

    ev_nominal credits every priced card at market price. ev_realizable applies
    a bulk floor: any card priced below `bulk_threshold` is treated as worth
    `bulk_rate` instead (modelling that sub-threshold commons/uncommons can't be
    liquidated at market — only at bulk-lot rates). The floor is applied per
    card, uniformly across finishes and sheets: what matters is the card's own
    price, not which slot it fills.

    Unpriced cards contribute 0 to both sums but count toward total_weight, so
    coverage = priced_weight / total_weight.
    """
    cards = sheet.get("cards", {})  # {uuid: weight}
    total_w = priced_w = value_nom = value_real = 0.0
    for uuid, w in cards.items():
        w = float(w)
        total_w += w
        price = prices.get((uuid, finish))
        if price is not None:
            priced_w += w
            value_nom += w * price
            realizable = price if price >= bulk_threshold else bulk_rate
            value_real += w * realizable
    ev_nom = (value_nom / total_w) if total_w else 0.0
    ev_real = (value_real / total_w) if total_w else 0.0
    return ev_nom, ev_real, priced_w, total_w


def compute_set(code: str, setobj: dict, prices: dict, verbose: bool,
                bulk_threshold: float, bulk_rate: float):
    """Compute EV rows + slot rows for every booster variant in a set."""
    ev_rows, slot_rows = [], []
    name = setobj.get("name")
    release = setobj.get("releaseDate")
    booster = setobj.get("booster") or {}

    for vname, v in booster.items():
        boosters = v.get("boosters", [])
        sheets = v.get("sheets", {})
        total_config_w = float(v.get("boostersTotalWeight") or sum(float(b.get("weight", 1)) for b in boosters) or 1)

        # Precompute each sheet's nominal + realizable EV and coverage once.
        sheet_ev = {}
        for sname, sh in sheets.items():
            fin = sheet_finish(sh)
            sheet_ev[sname] = (fin, *sheet_expected_value(sh, prices, fin, bulk_threshold, bulk_rate))

        variant_ev = variant_ev_real = 0.0
        variant_priced_w = variant_total_w = 0.0
        agg_picks: dict[str, float] = {}

        for b in boosters:
            cfg_w = float(b.get("weight", 1)) / total_config_w
            contents = b.get("contents", {})  # {sheet_name: num_picks}
            for sname, picks in contents.items():
                picks = float(picks)
                agg_picks[sname] = agg_picks.get(sname, 0.0) + cfg_w * picks
                if sname not in sheet_ev:
                    if verbose:
                        print(f"    !! {code}/{vname}: content sheet '{sname}' missing from sheets")
                    continue
                fin, ev, ev_real, pw, tw = sheet_ev[sname]
                variant_ev += cfg_w * picks * ev
                variant_ev_real += cfg_w * picks * ev_real
                variant_priced_w += cfg_w * picks * pw
                variant_total_w += cfg_w * picks * tw

        coverage = (variant_priced_w / variant_total_w) if variant_total_w else 0.0

        ev_rows.append({
            "set_code": code, "set_name": name, "release_date": release,
            "set_category": set_category(code),
            "variant": vname,
            "ev": round(variant_ev, 4),
            "ev_realizable": round(variant_ev_real, 4),
            "priced_coverage": round(coverage, 4),
            "unpriced_weight_share": round(1 - coverage, 4),
            "bulk_threshold": bulk_threshold, "bulk_rate": bulk_rate,
            "n_sheets": len(sheets), "n_booster_configs": len(boosters),
        })

        for sname, exp_picks in sorted(agg_picks.items()):
            fin, ev, ev_real, pw, tw = sheet_ev.get(sname, ("normal", 0.0, 0.0, 0.0, 0.0))
            slot_rows.append({
                "set_code": code, "set_name": name,
                "set_category": set_category(code), "variant": vname,
                "sheet": sname, "finish": fin,
                "expected_picks": round(exp_picks, 4),
                "sheet_ev": round(ev, 4),
                "sheet_ev_realizable": round(ev_real, 4),
                "slot_ev_contribution": round(exp_picks * ev, 4),
                "slot_ev_realizable_contribution": round(exp_picks * ev_real, 4),
                "sheet_coverage": round((pw / tw) if tw else 0.0, 4),
            })

        if verbose:
            print(f"    {code}/{vname}: EV=${variant_ev:.2f} realizable=${variant_ev_real:.2f} "
                  f"coverage={coverage:.1%} sheets={len(sheets)} configs={len(boosters)}")

    return ev_rows, slot_rows


def is_premier(setobj: dict) -> bool:
    return setobj.get("type") in {"core", "expansion"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--provider", default="tcgplayer")
    ap.add_argument("--list-type", default="retail")
    ap.add_argument("--since", default="2022-01-01")
    ap.add_argument("--set", help="Restrict to a single set code")
    ap.add_argument("--bulk-threshold", type=float, default=0.25,
                    help="Cards priced below this ($) are floored to --bulk-rate "
                         "for realizable EV (default 0.25)")
    ap.add_argument("--bulk-rate", type=float, default=0.02,
                    help="Realizable value ($) assigned to sub-threshold bulk cards (default 0.02)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    ddir = data_dir()
    print("Loading AllPrintings ...")
    data = load_allprintings(ddir)
    print("Loading prices ...")
    prices = latest_prices(ddir, args.provider, args.list_type)

    if args.set:
        codes = [args.set.upper()]
    else:
        codes = sorted(
            code for code, s in data.items()
            if is_premier(s) and (s.get("releaseDate") or "0000") >= args.since and s.get("booster")
        )
    print(f"Computing EV for {len(codes)} set(s) ...")

    all_ev, all_slot = [], []
    for code in codes:
        s = data.get(code)
        if not s:
            print(f"  {code}: not found"); continue
        ev_rows, slot_rows = compute_set(code, s, prices, args.verbose,
                                         args.bulk_threshold, args.bulk_rate)
        all_ev.extend(ev_rows)
        all_slot.extend(slot_rows)

    if not all_ev:
        print("No EV rows produced — check --since / --set filters.")
        return 1

    out = ddir / "processed"
    out.mkdir(parents=True, exist_ok=True)
    ev_df = pd.DataFrame(all_ev).sort_values(["release_date", "set_code", "variant"])
    slot_df = pd.DataFrame(all_slot)
    ev_df.to_csv(out / "booster_ev.csv", index=False)
    slot_df.to_csv(out / "booster_ev_by_slot.csv", index=False)

    print(f"\nWrote {out / 'booster_ev.csv'} ({len(ev_df)} rows)")
    print(f"Wrote {out / 'booster_ev_by_slot.csv'} ({len(slot_df)} rows)")

    # Auditable classification table — review that UB flags are correct.
    cls = ev_df[["set_code", "release_date", "set_category", "set_name"]].drop_duplicates("set_code")
    ub = cls[cls["set_category"] == "universes_beyond"]
    std = cls[cls["set_category"] == "standard"]
    print(f"\nSet classification for review — {len(std)} standard, {len(ub)} universes_beyond:")
    print("  universes_beyond:", ", ".join(sorted(ub["set_code"])) or "(none)")
    print("  standard:        ", ", ".join(sorted(std["set_code"])))
    print(f"\nEV summary (default/play variants) — bulk floor: "
          f"<${args.bulk_threshold:.2f} -> ${args.bulk_rate:.2f}")
    show = ev_df[ev_df["variant"].isin(["default", "play"])] if "variant" in ev_df else ev_df
    cols = ["set_code", "release_date", "variant", "ev", "ev_realizable", "priced_coverage"]
    print(show[cols].to_string(index=False) if not show.empty else ev_df[cols].to_string(index=False))
    low = ev_df[ev_df["priced_coverage"] < 0.9]
    if not low.empty:
        print(f"\n⚠ {len(low)} variant(s) below 90% price coverage — review before publishing:")
        print(low[["set_code", "variant", "priced_coverage"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())