# CEOTrades — Insider Trade Intelligence

**Every insider trade at publicly traded companies, collected, organized,
paper-traded and analyzed automatically. Zero manual input.**

CEOTrades continuously pulls **all** SEC Form 4 (Statement of Changes in
Beneficial Ownership) and Form 5 (annual catch-all) filings — including
amendments (4/A, 5/A) — from [SEC EDGAR](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany),
parses every transaction, and **forward-tests every open-market insider buy**
as a simulated **$10,000** long filled at the **next regular-session open
after the filing is public** — using real Yahoo / Stooq / Nasdaq daily bars,
never the insider’s own fill. The static dashboard is published on GitHub Pages:

📊 [https://karagemop466-tech.github.io/CEOTrades/](https://karagemop466-tech.github.io/CEOTrades/)

Under Section 16 of the Exchange Act, every director, executive officer and
>10% shareholder of a public company must disclose purchases, sales, grants,
option exercises, gifts and other transactions within **two business days** —
Form 4 is the richest real-time dataset of what company insiders do with
their own money. This project records those filings **and** what a follower
could actually have earned by buying after they became public.

## Pages

| Page | What it shows |
|---|---|
| [Dashboard](https://karagemop466-tech.github.io/CEOTrades/) | Paper-book P&L, equity curve, findings, then the Form 4 tape |
| [Paper book](https://karagemop466-tech.github.io/CEOTrades/paper.html) | Every $10k simulated fill: insider amount, our entry, gap, ROI, CSV |
| [Trades](https://karagemop466-tech.github.io/CEOTrades/trades.html) | Every transaction row — searchable, filterable, sortable, CSV export |
| [Companies](https://karagemop466-tech.github.io/CEOTrades/companies.html) | Aggregated insider activity per public company |
| [Insiders](https://karagemop466-tech.github.io/CEOTrades/insiders.html) | Directors, officers, 10%+ owners and their trades |
| [Findings](https://karagemop466-tech.github.io/CEOTrades/analysis.html) | Paper ROI by role/size/horizon, plus Form 4 code mix and net flow |
| [About](https://karagemop466-tech.github.io/CEOTrades/about.html) | Methodology, data dictionary, caveats, automation status |

## How the automation works (no manual input)

```
 ┌─────────────┐   nightly 04:00 UTC   ┌──────────────────┐
 │ GitHub      │ ────────────────────► │ collector/       │
 │ Actions cron│                       │ collect.py       │
 └─────────────┘                       └────────┬─────────┘
                                                │ 1. list every Form 4/4A/5/5A
                                                │    filed per day via the SEC
                                                │    daily master index:
                                                │    daily-index/YYYY/QTRq/
                                                │      master.YYYYMMDD.idx
                                                │ 2. download each filing's full
                                                │    submission and extract the
                                                │    ownership XML (throttled to
                                                │    8 req/s, SEC fair-access)
                                                │ 3. parse every non-derivative
                                                │    and derivative transaction
                                                │ 4. merge into the dataset
                                                │    (amendments supersede) and
                                                │    auto-backfill older history
                                                │    until the run's time budget
                                                ▼
                                   ┌──────────────────┐
                                   │ collector/       │
                                   │ paper_trade.py   │
                                   └────────┬─────────┘
                                            │ 5. every code-P common-equity
                                            │    buy → $10,000 paper long
                                            │ 6. fill = next session OPEN
                                            │    after the filing date
                                            │    (Yahoo, then Stooq, then
                                            │    Nasdaq — never the insider
                                            │    fill, no lookahead)
                                            │ 7. mark to latest close; ROI,
                                            │    gap, +1/+5/+21/+63 returns
                                            ▼
                                   ┌──────────────────┐
                                   │ collector/       │
                                   │ build_site.py    │
                                   └────────┬─────────┘
                                            │ regenerate data + HTML pages
                                            ▼
                          commit & push  ──►  GitHub Pages publishes
```

## Paper-trading rule (forward test)

Exactly one rule, applied the same way every night:

1. **Signal** — a Form 4/5 non-derivative transaction with code **P**
   (open-market or private purchase) of common equity or an ADR, with a
   valid ticker, shares > 0 and price > 0. Multiple P lots in the same
   filing are VWAP-aggregated into one signal. Preferred, warrants, RSUs
   and options are excluded.
2. **Size** — **$10,000** notional per signal (fractional shares). The
   insider’s own share count and dollar amount are stored next to our
   fill so conviction and the delay gap are visible; they do not change
   the $10k size.
3. **Entry** — regular-session **open of the first trading day strictly
   after the SEC filing date**. Filing-day prices are never used: a Form 4
   may be accepted after that day’s open (or after the close). If the next
   session has not printed yet, the row stays `awaiting_entry`.
4. **Mark** — latest regular-session close from Yahoo Finance daily bars,
   with Stooq then Nasdaq as fallbacks. A ticker none of them price stays
   `no_price` and is never silently filled.
5. **Exit** — none. Positions stay open so this is a growing forward-test
   collection, not a round-trip backtest. Horizon returns (entry-day close,
   +1 / +5 / +21 / +63 sessions) fill in as those closes print.
6. **Costs** — none modelled beyond using the next open (no commission,
   no bid/ask).

`gap = entry_px / insider_px − 1` is the move between the insider’s fill
and the first open a public follower could consistently obtain.

- **Enumeration** — `https://www.sec.gov/Archives/edgar/daily-index/{YYYY}/QTR{q}/master.{YYYYMMDD}.idx`
  lists every filing accepted each business day (pipe-delimited:
  `CIK|Company Name|Form Type|Date Filed|File Name`). Weekends/holidays have
  no index (404) and are skipped automatically.
- **Extraction** — each filing's full submission
  (`https://www.sec.gov/Archives/edgar/data/{CIK}/{ACCESSION}.txt`) embeds the
  structured `<ownershipDocument>` XML; issuer, insider(s), relationship, and
  all non-derivative + derivative transactions are parsed (dates, codes,
  share counts, prices, post-transaction holdings).
- **Storage** — `collector/data/trades-YYYY-MM.json.gz` shards hold the
  growing, de-duplicated dataset (keyed by accession; the most recently filed
  document wins per issuer/insider/period, so 4/A amendments supersede).
  Shard writes are deterministic — unchanged months produce no git churn.
- **Backfill** — after covering the recent window, each nightly run walks
  further into the past until its time budget (default 80 min, fits the 120-min Actions job) is spent, so
  historical coverage deepens automatically every night.
- **Paper book** — `collector/paper_trade.py` turns every qualifying code-P
  buy into a $10k long at the next open after `fd`, marks it to the latest
  close, and writes `data/paper/positions.json`, `summary.json`, `equity.json`
  and `positions.csv`.
- **Publishing** — `collector/build_site.py` regenerates `data/*.json`,
  `data/trades.csv` (recent), per-month `data/csv/YYYY-MM.csv`, the paper
  book pages and all HTML; the workflow commits and pushes, GitHub Pages
  serves it.

## Running it yourself

```bash
python3 collector/selftest.py           # offline parser tests
python3 collector/paper_test.py         # offline paper-engine tests (no lookahead)
python3 collector/collect.py --days 3 --no-backfill   # collect recent days
python3 collector/paper_trade.py        # $10k paper fills at real next-open prices
python3 collector/build_site.py         # regenerate the site
```

Python 3.10+ with the **standard library only** — no dependencies, no API keys.
The collector is idempotent and incremental: re-running a day replaces that
day's rows; previously collected days are skipped (use `--force` to redo).

Useful flags: `--start 2026-01-01 --end 2026-01-31` (explicit window),
`--rate 8` (max req/s; SEC allows 10), `--budget-min 240` (time budget),
`--no-backfill`, `--data-dir <dir>`.

## Data dictionary (per trade row)

| Field | Meaning |
|---|---|
| `acc` | SEC accession number (links to the filing on EDGAR) |
| `form`, `amend` | Root form (`4` or `5`); `amend=1` for 4/A, 5/A |
| `fd` / `td` | Filing date (daily index) / transaction date |
| `co`, `tk`, `icik` | Issuer company, ticker, CIK |
| `in`, `pcik`, `own_n` | Insider (first reporting person), CIK, owner count |
| `rel`, `title` | Relationship (Director / Officer / 10% Owner / Other), officer title |
| `code`, `ct`, `side` | Official transaction code, its text, dashboard side |
| `sec`, `der`, `under` | Security title, derivative flag, underlying security |
| `sh`, `px`, `val` | Shares, price/share, value = sh×px (blank when unpriced) |
| `xp`, `exp` | Conversion/exercise price and expiration (derivatives) |
| `ad`, `af` | Acquired(A)/Disposed(D), shares owned after |
| `di` | Direct(D) / indirect(I) ownership |

### Transaction codes (official, SEC instructions)

`P` open market/private purchase · `S` open market/private sale · `A` grant, award or
other acquisition · `M` exercise or conversion of derivative · `C` conversion of
derivative · `F` payment of exercise price or tax by delivering/withholding
securities · `D` sale back to the issuer · `G` bona fide gift · `J` other (footnoted) ·
`I` discretionary · `O`/`X` exercise of out-of-/in-the-money derivative ·
`E`/`H` expiration of short/long derivative · `L` small acquisition · `V` voluntary
early report · `W` will/descent · `Z` voting trust · `K` equity swap ·
`U` change-of-control tender.

**Net flow** on the dashboard = value(code P) − value(code S) — open-market flow
only. Grants, exercises and tax withholding are shown but not netted.

## Caveats

- Sales under pre-arranged **10b5-1 plans** appear just like discretionary
  sales; footnotes on EDGAR (linked from every row) explain the plan.
- Grants/gifts and exercise legs are often reported without a price — value
  is blank, not zero.
- Amendments (`4/A`, `5/A`) supersede the original; the most recent version
  per issuer/insider/period is kept.
- Filers for issuers without a U.S. ticker are included (ticker shown as —).
- Filings accepted after EDGAR's nightly index cutoff appear the next business
  day — the 3-day rolling window in the daily job guarantees they're captured.
- Paper fills use the **next** session’s open after the filing date. That is
  deliberately conservative (no lookahead). Private purchases are still code P
  on Form 4 and are treated like open-market buys because the form does not
  split them.
- Tickers that Yahoo, Stooq and Nasdaq all miss stay `no_price` — they are
  listed, never invented.

## Repository layout

```
index.html paper.html trades.html companies.html
insiders.html analysis.html about.html   static dashboard (GitHub Pages, root)
css/ js/ data/                           site assets + generated data
data/paper/                              paper-book positions, summary, equity, CSV
collector/
  collect.py                             SEC EDGAR collector (stdlib only)
  paper_trade.py                         $10k next-open forward test (stdlib only)
  build_site.py                          static site generator (stdlib only)
  selftest.py / paper_test.py            offline tests (no network)
  data/trades-YYYY-MM.json.gz            the growing dataset (committed, sharded)
  data/stats.json                        per-run collection statistics
.github/workflows/collect.yml            daily automation
```

## Disclaimer

All data is public SEC filing data, presented as filed. CEOTrades is not
affiliated with the SEC. Nothing here is investment advice.
