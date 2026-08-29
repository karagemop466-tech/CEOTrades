# CEOTrades — Insider Trade Intelligence

**Goal:** every SEC-reported insider trade at every U.S. public company — collected,
organised, paper-traded against real market prices when available, and analysed.
Zero manual input; no fabricated rows or prices.

📊 **[karagemop466-tech.github.io/CEOTrades](https://karagemop466-tech.github.io/CEOTrades/)**

Under Section 16 of the Securities Exchange Act of 1934, every director,
executive officer and >10% shareholder of a public company must report their
transactions to the SEC — Form 4 within **two business days**. That makes
Forms 3/4/5 the richest public record of what corporate insiders do with their
own money. CEOTrades is designed to collect all of it, then answer the follow-up
question: **could a member of the public actually have made money copying them?**

---

## The forward test

Every open-market insider purchase becomes one simulated position:

| Rule | Value |
|---|---|
| **Signal** | Form 4/5 non-derivative transaction code `P` (open-market or private purchase) of common stock or an ADR, with a reported share count and price |
| **Size** | Fixed **$10,000** notional, fractional shares allowed |
| **Entry** | Regular-session **open of the first trading day _strictly after_ the filing date** |
| **Exit** | None — positions stay open; this is a forward test |
| **Mark** | Latest available regular-session close |
| **Horizons** | 1, 5, 21, 63 and 252 sessions after entry |
| **Costs** | None modelled (no commission, spread or slippage) |
| **Prices** | Yahoo Finance daily bars, Stooq fallback |

Two properties are enforced in code and covered by tests:

- **No lookahead.** The fill is never a price at or before the filing date, and
  never the insider's own fill price. The earliest usable price is the next
  session's open — the first moment a follower could realistically have acted.
- **No fabrication.** A horizon that has not elapsed is reported blank, not
  estimated. A ticker with no price history is reported as `no_price` and
  counted, not silently dropped. `open + awaiting_entry + no_price` always
  equals the total signal count.

The **gap** column shows what following costs: our entry price versus the
insider's average price. A positive gap means the public follower paid more.
Every paper row also carries an entry-rule verification status, market-data
source, SEC accession/issuer CIK and EDGAR review link so the entry date, entry
open, latest close, P&L and ROI can be audited line by line.

---

## Data sources

| Source | Role |
|---|---|
| [SEC Insider Transactions Data Sets](https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets) | Quarterly archives, **Jan 2006 → present**. The full historical backfill. |
| [EDGAR daily index](https://www.sec.gov/Archives/edgar/daily-index/) | New filings each night, ahead of the quarterly publication. |

Every column is resolved **by name** against the header row, following the
[official schema documentation](https://www.sec.gov/files/insider_transactions_readme.pdf)
(SUBMISSION, REPORTINGOWNER, NONDERIV_TRANS, DERIV_TRANS). A column that is
absent yields an empty value — never a positional guess. Rows arriving from
both the nightly and quarterly paths are de-duplicated on a stable identity
key, with the SEC-published copy winning.

Data is captured in full when present in the official source: filing and
transaction dates, company name, ticker, issuer CIK, insider name and CIK,
relationship and officer title, transaction code and its official description,
security title, shares, price per share, total value, acquired/disposed flag,
shares held after, direct/indirect ownership and its nature, plus derivative
detail (underlying security, underlying shares, conversion or exercise price,
expiration) and filing timeliness. Missing source fields remain blank/null;
they are not guessed.

## 2025 target-year collection and audit

For the current-year task, run the dedicated YTD orchestrator. It uses SEC
quarterly bulk ZIPs for completed quarters and EDGAR daily-index/XML filings for
the current quarter:

```bash
python3 collector/collect_ytd.py --year 2025 --replace-year
python3 collector/build_data.py --year 2025 --audit-year 2025
python3 collector/build_pages.py
```

The collection run records `collector/data/source_manifest.json`; the build then
emits `data/audit.json` and regenerates `INSIDER_TRADING_FORENSIC_REPORT.md`.
If the local store plus source manifest do not prove full coverage from the
beginning of 2025 through year-end 2025, the audit marks
the dataset `incomplete_or_unproven` and the site displays that warning. The
audit also blocks known manual/synthetic data generators and recomputes every
row's share × price arithmetic.

`collector/populate_verified_data.py` is intentionally disabled. Production data
must be collected from official SEC endpoints only. Price caches must come from
real market data sources; absent prices remain `no_price` in the paper book.

---

## Pages

| Page | What it shows |
|---|---|
| [Dashboard](https://karagemop466-tech.github.io/CEOTrades/) | Headline counts, paper-book P&L, and the recent filing tape |
| [Paper book](https://karagemop466-tech.github.io/CEOTrades/paper.html) | Every simulated position: insider's price, our entry, gap, P&L, ROI |
| [Trades](https://karagemop466-tech.github.io/CEOTrades/trades.html) | Searchable transaction tape + full history download per year |
| [Companies](https://karagemop466-tech.github.io/CEOTrades/companies.html) | Insider activity aggregated per issuer, with drill-down |
| [Insiders](https://karagemop466-tech.github.io/CEOTrades/insiders.html) | Directors, officers and 10% owners ranked by activity |
| [Insider flows](https://karagemop466-tech.github.io/CEOTrades/activity.html) | Same-insider buy/sell overlap plus reported held-after share balances and review links |
| [Findings](https://karagemop466-tech.github.io/CEOTrades/analysis.html) | ROI by role, purchase size, holding period and year |
| [Irregularities](https://karagemop466-tech.github.io/CEOTrades/irregularities.html) | Automated review flags and audit coverage warnings |
| [About](https://karagemop466-tech.github.io/CEOTrades/about.html) | Methodology, data dictionary, caveats |

---

## Architecture

```
collector/
  bulk_backfill.py   SEC quarterly archives  -> trades-YYYY.csv.gz
  collect.py         EDGAR daily index/XML    -> trades-YYYY-MM.json.gz
  collect_ytd.py     current-year orchestrator (bulk + daily, no manual input)
  store.py           streaming reader over both formats, de-duplicating
  audit.py           row-level integrity/completeness audit -> data/audit.json
  irregularities.py  deterministic review flags -> data/irregularities.json
  build_data.py      aggregation + $10k paper simulation + insider-flow/holding analysis -> data/*.json
  build_site.py      orchestrator: resumable backfill, then build
  build_pages.py     static HTML generator
  test_bulk.py       parser tests      (offline)
  test_paper.py      simulation tests  (offline)
  test_site.py       published-output contract tests
```

The corpus is on the order of **15 million rows**, so it is never loaded into
memory: `store.iter_rows()` streams it, and price bars are released as soon as
each ticker's positions are simulated.

**The backfill is resumable.** Each nightly run ingests as many
not-yet-collected quarters as fit in its time budget, newest first, recording
progress in `collector/data/backfill.json`. The history therefore deepens
automatically night after night with no manual step, while the two most recent
quarters are always refreshed so amendments and late filings are absorbed.

### Running it yourself

```bash
python3 collector/collect_ytd.py --year 2025 --replace-year          # target 2025 full year
python3 collector/bulk_backfill.py --from-year 2006 --to-year 2025  # optional through-2025 history
python3 collector/collect.py --days 5 --no-backfill                 # new filings only
python3 collector/build_data.py --year 2025 --audit-year 2025      # simulate + audit + publish 2025 data
python3 collector/build_pages.py                                    # regenerate HTML
```

Standard library only — no third-party dependencies.

---

## Verification

Offline tests and generated audits run before publication:

```bash
python3 collector/selftest.py     # ownership XML parser + fixtures
python3 collector/test_bulk.py    # parsing, joint filers, amendments, idempotency
python3 collector/test_paper.py   # simulation arithmetic + no-lookahead proofs
python3 collector/test_site.py    # every field the UI reads exists; P&L recomputed
python3 collector/audit.py --year 2025  # completeness + row-integrity report
```

`test_paper.py` recomputes the arithmetic by hand ($10,000 at an open of
$12.50 must be exactly 800 shares) and asserts that a simulation run as of the
filing date produces **no** entry price. It also checks same-insider buy/sell
overlap and latest SEC held-after balances. `test_site.py` independently
re-derives `shares`, `mtm`, `pnl` and `roi` from the published JSON and
verifies `entry_d > fd` plus zero entry-rule/arithmetic failures.

Two real defects were caught this way: an identity key using `or ""` that
corrupted legitimate `0` values and broke gzip idempotency, and an incorrect
test expectation where the engine was right to refuse to report a horizon
return it lacked data for.

---

## Caveats

- **Not investment advice.** A research record, not a recommendation.
- **Ticker reuse.** Symbols are recycled across two decades; price series are
  matched on the ticker as filed, so old positions in reused symbols can be wrong.
- **Split-adjustment mismatch.** Insider prices are as-filed while market bars
  are split-adjusted, so `gap` can look extreme around historical splits.
- **Delisted names** often have no retrievable history — surfaced as `no_price`.
- **Open positions only.** Aggregate ROI is a buy-and-hold figure mixing
  positions of very different ages, and is not annualised.
- **Reported holdings are not full brokerage portfolios.** The Insider flows
  page uses SEC post-transaction common-share balances for the issuer only; it
  does not infer unfiled trades, outside accounts or non-issuer assets.
- **As-filed data.** The SEC does not correct filer errors, and neither do we.

---

Data courtesy of the U.S. Securities and Exchange Commission (public domain).
Prices via Yahoo Finance and Stooq. Educational use only.
