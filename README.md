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

### Corporate-action (split) guard

Insider prices are **as-filed** while market bars are **split-adjusted**, and a
reverse split or cash takeover inside a holding window puts the entry and the
mark on different share bases. The simulator detects this two ways — an
unadjusted ≥3× close-to-close jump inside the holding window, or an implausible
return (e.g. >200% within ~90 sessions or >500% at any horizon). When it fires,
the observed prices and dates are kept but the headline ROI/P&L is **withheld**
(`roi_review: true`, raw value retained in `roi_reported`) and a **High**
irregularity flag is raised. Example caught and verified: Digital Brands Group
(DBGI) did a 1-for-40 reverse split on 2026-07-24; an unadjusted series showed a
specious +517% return that a follower never earned (the share count collapsed by
40×). The flag is a request to confirm the share basis, never a silent guess.

---

## The historical backtest (verified entries AND exits)

The forward book leaves positions open; the **backtest** closes them so every
tracked insider buy gets a real, observed entry price/date **and** a real
observed exit price/date. It runs over the whole collected archive:

| Rule | Value |
|---|---|
| **Signal** | Same code-P common-equity aggregation — one $10,000 position per SEC accession+ticker |
| **Entry** | Open of the first trading day strictly after the filing date (no lookahead) |
| **Exit** | Regular-session **close 252 sessions later** (~one trading year) |
| **Delisted/M&A** | Mature names whose bar series ends early exit at the **last observed close** and are flagged `exit_last_observed` for corporate-action review |
| **Not yet mature** | Positions younger than 252 sessions stay `open`, exit left blank (never estimated) |
| **Prices** | Yahoo full-history (`range=max`) bars, Stooq then Nasdaq fallback, cached per ticker |

Every signal is accounted for in `data/backtest/coverage.json` (P-rows →
derivative / no-ticker / no-price / not-common → signals → verified entry →
exited / open / no-price), so **a tracked buy is never silently missing a
trade**. Outputs land in `data/backtest/` (`positions.json/.csv`, `summary.json`,
`winners.json`, `losers.json`, `coverage.json`) and on the [Backtest
page](https://karagemop466-tech.github.io/CEOTrades/backtest.html). The
`.github/workflows/backfill.yml` workflow downloads the multi-year SEC archives
and prices them (the nightly job keeps the current year fresh within its 2-hour
budget; the weekly job deepens history). `collector/test_backtest.py` proves the
entry/exit math, the no-lookahead rule, the delisting exit, and the split guard
offline.

The **gap** column shows what following costs: our entry price versus the
insider's average price. A positive gap means the public follower paid more.
Every paper row also carries an entry-rule verification status, market-data
source, SEC accession/issuer CIK and EDGAR review link so the entry date, entry
open, latest close, P&L and ROI can be audited line by line. Horizon exit
candidates (`r1`, `r5`, `r21`, `r63`, and `r252`) include the exact observed
session date and close; an unavailable horizon stays blank.

### Performance logs and coverage

Each build writes separate `data/paper/winners.json` and
`data/paper/losers.json` logs. A winner has verified mark-to-market P&L above
zero; a loser has verified P&L at or below zero. Positions without both a
verified entry and a verified close remain explicitly unclassified. The logs
include the same SEC EDGAR and market-history review links as the paper book. Each row also contains observed performance factors (entry gap, holding-session count, verified return, and data-quality review flags); these are diagnostics, not asserted causes.

Every qualifying code-P common-equity purchase is attempted as one aggregated
paper position per SEC accession and ticker. P rows that cannot support a
non-fabricated order (derivative, missing ticker/shares/price, non-common
security, or invalid symbol) are counted in `signal_coverage` rather than
silently discarded. This is why paper positions can be fewer than all P rows
without implying an order was missed.

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
  paper_trade.py     forward $10k simulation (open next open after filing)
  backtest.py        historical round-trip: verified entry + 252-session exit
                     for EVERY tracked buy, with per-signal coverage accounting
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
python3 collector/test_backtest.py # verified entry+252 exit math, delisting exit, split guard
python3 collector/test_site.py    # every field the UI reads exists; P&L recomputed
python3 collector/audit.py --year 2025  # completeness + row-integrity report
python3 collector/verify_lines.py # line-by-line: paper + backtest arithmetic/no-lookahead
```

Run the backtest over the collected store (use `--offline` to reproduce from the
committed price cache without network):

```bash
python3 collector/backtest.py --from-year 2006            # full history round trips
python3 collector/backtest.py --from-year 2024 --offline  # reproduce from cached bars
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
