# Line-by-line verification report

Generated 2026-08-30 18:53 UTC · target filing year 2026

- Deterministic checks passed: **390**
- Checks failed: **0**

## Method

- **trades**: Every row parsed from official SEC Form 3/4/5 XML fields; SEC filing URL emitted per row for manual review.
- **entry_rule**: Entry = open of the first regular session strictly after the SEC filing date; gap must be 1-10 calendar days on a weekday.
- **exit_rule**: Backtest exit = verified close of the +252 session (delisted names: last observed close, flagged); exit only from sessions that have actually printed.
- **arithmetic**: shares=10000/entry_px; mtm=shares*last_px; pnl=mtm-10000; roi=mtm/10000-1; exit_pnl=shares*exit_px-10000; all recomputed independently here.
- **prices**: Yahoo Finance / Stooq / Nasdaq daily OHLC; independently re-fetchable via the emitted chart-API URLs. Split-straddling ROIs are withheld (roi_review) until the share basis is confirmed.

## Paper book — every position was checked; sample below.

| Ticker | Filed | Entry | Entry px | Shares | Mark | ROI | Entry rule | Price src | SEC filing |
|---|---|---|---|---|---|---|---|---|---|
| ABSI | 2026-07-06 | 2026-07-07 | 11.85 | 843.8819 | 8.71 | -26.50% | verified | nasdaq | [filing](https://www.sec.gov/Archives/edgar/data/1672688/000167268826000124/0001672688-26-000124-index.htm) |
| AGIG | 2026-06-15 | 2026-06-16 | 1.15 | 8695.6522 | 1.01 | -12.17% | verified | nasdaq | [filing](https://www.sec.gov/Archives/edgar/data/1156041/000149315226028685/0001493152-26-028685-index.htm) |
| CTSO | 2026-06-15 | 2026-06-16 | 0.49 | 20408.1633 | 0.3356 | -31.51% | verified | nasdaq | [filing](https://www.sec.gov/Archives/edgar/data/1175151/000110465926074164/0001104659-26-074164-index.htm) |
| DBGI | 2026-06-09 | 2026-06-10 | 43.6 | 229.3578 | 6.73 | — | verified | nasdaq | [filing](https://www.sec.gov/Archives/edgar/data/1668010/000149315226027810/0001493152-26-027810-index.htm) |
| AGIG | 2026-05-20 | 2026-05-21 | 1.18 | 8474.5763 | 1.01 | -14.41% | verified | nasdaq | [filing](https://www.sec.gov/Archives/edgar/data/1156041/000149315226024597/0001493152-26-024597-index.htm) |
| ACCS | 2026-05-19 | 2026-05-20 | 6.26 | 1597.4441 | 5.71 | -8.79% | verified | nasdaq | [filing](https://www.sec.gov/Archives/edgar/data/843006/000106299326002799/0001062993-26-002799-index.htm) |
| AGIG | 2026-05-15 | 2026-05-18 | 1.12 | 8928.5714 | 1.01 | -9.82% | verified | nasdaq | [filing](https://www.sec.gov/Archives/edgar/data/1156041/000149315226023264/0001493152-26-023264-index.htm) |
| AGIG | 2026-05-14 | 2026-05-15 | 1.2 | 8333.3333 | 1.01 | -15.83% | verified | nasdaq | [filing](https://www.sec.gov/Archives/edgar/data/1156041/000149315226023155/0001493152-26-023155-index.htm) |
| ALKT | 2026-05-14 | 2026-05-15 | 16.9 | 591.716 | 20.22 | +19.64% | verified | nasdaq | [filing](https://www.sec.gov/Archives/edgar/data/1529274/000095014226001403/0000950142-26-001403-index.htm) |
| VEEE | 2026-03-19 | — | — | — | — | — | no_price | none | [filing](https://www.sec.gov/Archives/edgar/data/1855509/000173112226000462/0001731122-26-000462-index.htm) |
| MYO | 2026-03-17 | — | — | — | — | — | no_price | none | [filing](https://www.sec.gov/Archives/edgar/data/1369290/000119312526111290/0001193125-26-111290-index.htm) |
| ABSI | 2026-03-16 | 2026-03-17 | 2.61 | 3831.4176 | 8.71 | +233.72% | verified | nasdaq | [filing](https://www.sec.gov/Archives/edgar/data/1672688/000167268826000049/0001672688-26-000049-index.htm) |
| VEEE | 2026-03-13 | — | — | — | — | — | no_price | none | [filing](https://www.sec.gov/Archives/edgar/data/1855509/000173112226000395/0001731122-26-000395-index.htm) |
| ADMA | 2026-03-09 | 2026-03-10 | 15.69 | 637.3486 | 9.34 | -40.47% | verified | nasdaq | [filing](https://www.sec.gov/Archives/edgar/data/1368514/000114036126008363/0001140361-26-008363-index.htm) |
| ICFI | 2026-03-06 | — | — | — | — | — | no_price | none | [filing](https://www.sec.gov/Archives/edgar/data/1362004/000122520826003385/0001225208-26-003385-index.htm) |

Full machine-readable results: `data/verification.json` (15 paper rows, 28 activity rows, 29 sampled trade rows with SEC links).
