# MTG Commercial Analytics

Pricing, product, and revenue analytics for *Magic: The Gathering*, built
entirely on public data. Two analytical stories, one executive-facing Tableau
dashboard.

**Live viz:** _(link pending publish)_

_(screenshot pending publish)_

---

## Problem

Wizards of the Coast prices and sequences sealed *Magic* product against a
secondary market it doesn't control. This project asks two commercial
questions a WotC analyst would recognize:

1. **Pricing / profitability** — How does the expected singles value (EV) of a
   booster compare to its street price, by set and rarity slot? Where does the
   value proposition of sealed product sit, and how has it trended?
2. **Release cadence / revenue** — How does Hasbro's reported Wizards segment
   revenue relate to the number and type of *Magic* set releases per quarter?
   Framed as a scenario-exploration model, not a forecast.

## Data sources & fetch method

| Source | Content | Fetch |
|---|---|---|
| [Scryfall bulk data](https://scryfall.com/docs/api/bulk-data) | Card attributes: set, rarity, finishes, current prices | `src/` fetch script (no API key) |
| [MTGJSON](https://mtgjson.com/) `AllPricesToday` / `AllPrices` | Daily card prices (TCGplayer, Cardmarket, Card Kingdom); rolling ~90-day history | `src/fetch_price_snapshot.py`, run daily to accumulate history |
| Hasbro SEC filings (10-Q / 10-K) | Wizards of the Coast & Digital Gaming segment revenue by quarter | Manual extraction from EDGAR, documented in `data/README.md` |

Raw pulls live under `data/` and are **not** committed. See `data/README.md`
for exact fetch commands and file expectations.

## Method

- **Story 1 — Booster EV vs. price.** Model expected singles value per booster
  as collation slot probabilities × rarity-level average singles prices, by
  set. Compare against street price of sealed product.
- **Story 2 — Release-cadence revenue model.** Simple explanatory regression of
  quarterly Wizards segment revenue on *Magic* release-cadence features
  (premier sets, Universes Beyond, premium/remastered product per quarter).
  Fitted values reconcile exactly to reported segment figures.
- **Delivery.** Executive-style Tableau dashboard: KPI BAN row, EV-vs-price
  scatter, EV decomposition by rarity slot, indexed cadence/revenue panel,
  stated-findings annotation layer.

## Methodology & limitations

- Booster EV depends on **assumed collation/pull rates**; serialized and
  ultra-chase variants are excluded. Assumptions are tabulated in the
  methodology notes, and results are sensitivity-checked against them.
- Secondary-market prices are a **proxy**, not WotC's internal cost or sales
  data. No claims are made about actual margins or unit sales.
- Hasbro segment revenue includes D&D and digital licensing; the cadence model
  is **directional attribution, not causal isolation**, over a small number of
  quarters. Model complexity is deliberately capped accordingly.
- Price history from MTGJSON is a rolling ~90-day window; longer series are
  accumulated via daily snapshots and are therefore left-truncated at the
  project's collection start date.
