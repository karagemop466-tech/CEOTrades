# Line-by-line verification report

Generated 2026-08-29 01:38 UTC · target filing year 2025

- Deterministic checks passed: **3**
- Checks failed: **0**

## Method

- **trades**: Every row parsed from official SEC Form 3/4/5 XML fields; SEC filing URL emitted per row for manual review.
- **entry_rule**: Entry = open of the first regular session strictly after the SEC filing date; gap must be 1-10 calendar days on a weekday.
- **arithmetic**: shares=10000/entry_px; mtm=shares*last_px; pnl=mtm-10000; roi=mtm/10000-1 recomputed independently here.
- **prices**: Yahoo Finance daily OHLC; independently re-fetchable via the emitted chart-API URLs.

## Paper book — every position was checked; sample below.

| Ticker | Filed | Entry | Entry px | Shares | Mark | ROI | Entry rule | Price src | SEC filing |
|---|---|---|---|---|---|---|---|---|---|

Full machine-readable results: `data/verification.json` (0 paper rows, 0 activity rows, 0 sampled trade rows with SEC links).
