# Paper book — line-by-line review (official SEC filings + Yahoo bars)

**Reviewed through 2026-08-28 close** (Yahoo `query1` daily bars, `events=split`).
**Rule:** $10,000 at the **open of the first regular session strictly after** the Form 4/5 filing date. Insider VWAP is never the fill.

**Method.** Every row below was checked by hand against two official sources:
1. the raw EDGAR submission text (the structured ownership XML filed with the SEC), and
2. the Yahoo daily chart for the entry session.

15 signals → 15 open positions. **Entry-rule failures: 0. Arithmetic failures: 0.**
All 15 Form 4 filings verified field-by-field (filing date, form, code P, shares, price/VWAP
range, held-after, insider identity, issuer CIK) — see the SEC link per row. Entry opens were
spot-verified from Yahoo chart JSON (exact matches; sample links below).

SEC raw filing pattern (manual review):
`https://www.sec.gov/Archives/edgar/data/{issuerCIK}/{acc-no-dashes}/{acc}.txt`
Yahoo bars (manual review):
`https://query1.finance.yahoo.com/v8/finance/chart/{TICKER}?interval=1d&period1=…&period2=…&events=split`

| # | Filed | Entry | Ticker | Insider px (filed) | Entry open | Last 8/28 | ROI | SEC filing (verified) | Yahoo history |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 2026-03-06 | 2026-03-09 | ICFI | 74.3000 (1,100 sh) | 75.95 | 90.22 | +18.79% | [0001225208-26-003385](https://www.sec.gov/Archives/edgar/data/1362004/000122520826003385/0001225208-26-003385.txt) | [hist](https://finance.yahoo.com/quote/ICFI/history) |
| 2 | 2026-03-09 | 2026-03-10 | ADMA | 15.53 VWAP (7,000@15.67 + 7,000@15.39) | 15.69 | 9.34 | −40.47% | [0001140361-26-008363](https://www.sec.gov/Archives/edgar/data/1368514/000114036126008363/0001140361-26-008363.txt) | [hist](https://finance.yahoo.com/quote/ADMA/history) |
| 3 | 2026-03-13 | 2026-03-16 | VEEE | 0.42 (50,000 sh) | 14.504 | 10.36 | −28.57% ⚠ | [0001731122-26-000395](https://www.sec.gov/Archives/edgar/data/1855509/000173112226000395/0001731122-26-000395.txt) | [hist](https://finance.yahoo.com/quote/VEEE/history) |
| 4 | 2026-03-16 | 2026-03-17 | ABSI | 2.29 (100,000 sh) | 2.61 | 8.71 | +233.72% | [0001672688-26-000049](https://www.sec.gov/Archives/edgar/data/1672688/000167268826000049/0001672688-26-000049.txt) | [hist](https://finance.yahoo.com/quote/ABSI/history) |
| 5 | 2026-03-17 | 2026-03-18 | MYO | 0.6986 (20,000 sh) | 0.72 | 1.60 | +122.22% | [0001193125-26-111290](https://www.sec.gov/Archives/edgar/data/1369290/000119312526111290/0001193125-26-111290.txt) | [hist](https://finance.yahoo.com/quote/MYO/history) |
| 6 | 2026-03-19 | 2026-03-20 | VEEE | 0.4066 (25,000 sh) | 14.948 | 10.36 | −30.69% ⚠ | [0001731122-26-000462](https://www.sec.gov/Archives/edgar/data/1855509/000173112226000462/0001731122-26-000462.txt) | [hist](https://finance.yahoo.com/quote/VEEE/history) |
| 7 | 2026-05-14 | 2026-05-15 | AGIG | 1.1846 VWAP (8,220@1.185 + 10,000@1.22 + 13,000@1.157) | 1.20 | 1.01 | −15.83% | [0001493152-26-023155](https://www.sec.gov/Archives/edgar/data/1156041/000149315226023155/0001493152-26-023155.txt) | [hist](https://finance.yahoo.com/quote/AGIG/history) |
| 8 | 2026-05-14 | 2026-05-15 | ALKT | 16.6733 VWAP (750k+550k+675k sh, group filing) | 16.90 | 20.22 | +19.64% | [0000950142-26-001403](https://www.sec.gov/Archives/edgar/data/1529274/000095014226001403/0000950142-26-001403.txt) | [hist](https://finance.yahoo.com/quote/ALKT/history) |
| 9 | 2026-05-15 | 2026-05-18 | AGIG | 1.1994 VWAP (1,050@1.195 + 8,950@1.1999) | 1.12 | 1.01 | −9.82% | [0001493152-26-023264](https://www.sec.gov/Archives/edgar/data/1156041/000149315226023264/0001493152-26-023264.txt) | [hist](https://finance.yahoo.com/quote/AGIG/history) |
| 10 | 2026-05-19 | 2026-05-20 | ACCS | 6.8036 VWAP (6,956@6.8678 + 7,877@6.7469) | 6.26 | 5.71 | −8.79% | [0001062993-26-002799](https://www.sec.gov/Archives/edgar/data/843006/000106299326002799/0001062993-26-002799.txt) | [hist](https://finance.yahoo.com/quote/ACCS/history) |
| 11 | 2026-05-20 | 2026-05-21 | AGIG | 1.15 (14,990 sh) | 1.18 | 1.01 | −14.41% | [0001493152-26-024597](https://www.sec.gov/Archives/edgar/data/1156041/000149315226024597/0001493152-26-024597.txt) | [hist](https://finance.yahoo.com/quote/AGIG/history) |
| 12 | 2026-06-09 | 2026-06-10 | DBGI | 0.7001 (70,127.0287 sh) | 1.09 | 6.73 | +517.43% ⚠ | [0001493152-26-027810](https://www.sec.gov/Archives/edgar/data/1668010/000149315226027810/0001493152-26-027810.txt) | [hist](https://finance.yahoo.com/quote/DBGI/history) |
| 13 | 2026-06-15 | 2026-06-16 | AGIG | 1.18 (11,000 sh) | 1.15 | 1.01 | −12.17% | [0001493152-26-028685](https://www.sec.gov/Archives/edgar/data/1156041/000149315226028685/0001493152-26-028685.txt) | [hist](https://finance.yahoo.com/quote/AGIG/history) |
| 14 | 2026-06-15 | 2026-06-16 | CTSO | 0.4012 VWAP (251,136@0.40 + 10,333@0.43) | 0.49 | 0.336 | −31.43% | [0001104659-26-074164](https://www.sec.gov/Archives/edgar/data/1175151/000110465926074164/0001104659-26-074164.txt) | [hist](https://finance.yahoo.com/quote/CTSO/history) |
| 15 | 2026-07-06 | 2026-07-07 | ABSI | 11.54 (12,900 sh) | 11.85 | 8.71 | −26.50% | [0001672688-26-000124](https://www.sec.gov/Archives/edgar/data/1672688/000167268826000124/0001672688-26-000124.txt) | [hist](https://finance.yahoo.com/quote/ABSI/history) |

⚠ = split-flagged (see below). Entry-price spot checks from Yahoo chart JSON:
ABSI 2026-07-07 open **11.85** ✓ · ICFI 2026-03-09 open **75.95** ✓ · DBGI 2026-06-10 open **1.09** ✓ ·
VEEE 2026-03-20 open **14.948** ✓ · VEEE 2026-03-16 open **14.504** ✓.

## Split findings (verified from official filings + Yahoo event metadata)

| Ticker | Event (SEC source) | Yahoo `events.splits` | Series | Effect on paper row |
|---|---|---|---|---|
| **DBGI** | 1-for-40 reverse split effective 2026-07-24 12:01 ET; ~23M → ~575k shares; new CUSIP 25401N 606. [8-K filed 2026-07-22](https://www.sec.gov/Archives/edgar/data/1668010/000149315226034260/0001493152-26-034260-index.htm), press release EX-99.1 | 1:40 on 2026-07-24 | **unadjusted** (pre-split ~1.09 entry, post-split ~6.73 mark; ~40× jump) | The +517.43% mixes share classes. Split-adjusted: 9,174.31 sh → 229.36 sh → MTM $1,543.58 → **ROI −84.56%** on the $10,000 stake. |
| **VEEE** | 1-for-37 reverse stock split purported executed 2026-04-30 under a Nevada reincorporation **later found defective/invalid under Delaware law**; stockholder ratification sought (preliminary proxy 14A filed 2026-08-04). [8-K filed 2026-08-05](https://www.sec.gov/Archives/edgar/data/1855509/000173112226001019/0001731122-26-001019-index.htm), Items 3.03/5.03/7.01 | 1:37 on 2026-05-01 | **fully adjusted** (no jump at the event; March entry open 14.948 = raw 0.404 × 37; insider paid 0.4066 on 03-19) | The −30.69% / −28.57% returns are valid adjusted-series (and equivalent cash) returns. The old "+3576% / +3353%" gap column was an adjustment artifact and is now blanked. ⚠ Keep flagged: the 1:37 split's validity is **disputed and pending stockholder ratification** — if it is unwound, share counts (and therefore MTM) change. |

**Engine change (now live):** the build captures Yahoo split events with the bars and, per position,
classifies the series as **mixed** (P&L/ROI recomputed on split-adjusted shares), **adjusted**
(series return kept, gap blanked), or **indeterminate** (excluded from headline stats, flagged
for manual review with the issuer's SEC 8-K link). Split-flagged rows are excluded from headline
ROI/win-rate statistics and counted in the `split` block of `paper/summary.json`.

## Flags

| Item | Why |
|---|---|
| **VEEE ×2** | 1:37 reverse split (2026-04-30) executed under a reincorporation since found defective; ratification pending. Return shown on the adjusted series; share counts contingent on ratification. Manual review: [8-K 2026-08-05](https://www.sec.gov/Archives/edgar/data/1855509/000173112226001019/0001731122-26-001019-index.htm) |
| **DBGI** | Entry 2026-06-10 pre-split, mark post-split (1:40, 2026-07-24). Corrected ROI −84.56%. Manual review: [8-K 2026-07-22](https://www.sec.gov/Archives/edgar/data/1668010/000149315226034260/0001493152-26-034260-index.htm) |
| Buy/sell overlap | **0** same-insider same-issuer P and S in this 2026 slice (as filed to date). |
| Portfolio size | `data/insider_portfolios.json` — latest SEC held-after shares; only paper tickers with market bars are marked. Not a brokerage statement. |
| Coverage | Local store is **not** yet a full EDGAR year (audit `incomplete_or_unproven`); the nightly backfill + quarterly archives are filling the gaps. |
