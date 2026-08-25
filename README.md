# CEOTrades — Insider Trade Intelligence

**Every insider trade at publicly traded companies, collected, organized and
analyzed automatically. Zero manual input.**

CEOTrades continuously pulls **all** SEC Form 4 (Statement of Changes in
Beneficial Ownership) and Form 5 (annual catch-all) filings — including
amendments (4/A, 5/A) — from [SEC EDGAR](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany),
parses every transaction, and publishes a clean static dashboard on GitHub Pages:

📊 [https://karagemop466-tech.github.io/CEOTrades/](https://karagemop466-tech.github.io/CEOTrades/)

Under Section 16 of the Exchange Act, every director, executive officer and
>10% shareholder of a public company must disclose purchases, sales, grants,
option exercises, gifts and other transactions within **two business days** —
Form 4 is the richest real-time dataset of what company insiders do with
their own money.

## Pages

| Page | What it shows |
|---|---|
| [Dashboard](https://karagemop466-tech.github.io/CEOTrades/) | Totals, latest activity, most active companies, net flow, daily volume |
| [Trades](https://karagemop466-tech.github.io/CEOTrades/trades.html) | Every transaction row — searchable, filterable, sortable, CSV export |
| [Companies](https://karagemop466-tech.github.io/CEOTrades/companies.html) | Aggregated insider activity per public company |
| [Insiders](https://karagemop466-tech.github.io/CEOTrades/insiders.html) | Directors, officers, 10%+ owners and their trades |
| [Analysis](https://karagemop466-tech.github.io/CEOTrades/analysis.html) | Code mix, buy/sell value split, net buyers & sellers, daily rhythm |
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
                                   │ build_site.py    │
                                   └────────┬─────────┘
                                            │ regenerate data + HTML pages
                                            ▼
                          commit & push  ──►  GitHub Pages publishes
```

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
- **Publishing** — `collector/build_site.py` regenerates `data/*.json`,
  `data/trades.csv` (recent), per-month `data/csv/YYYY-MM.csv` and all HTML
  pages; the workflow commits and pushes, GitHub Pages serves it.

## Running it yourself

```bash
python3 collector/selftest.py           # offline parser tests (27 checks)
python3 collector/collect.py --days 3 --no-backfill   # collect recent days
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

## Repository layout

```
index.html trades.html companies.html insiders.html
analysis.html about.html            static dashboard (GitHub Pages, root)
css/ js/ data/                      site assets + generated data
collector/
  collect.py                        SEC EDGAR collector (stdlib only)
  build_site.py                     static site generator (stdlib only)
  selftest.py                       offline tests (fixtures = real filings)
  data/trades-YYYY-MM.json.gz       the growing dataset (committed, sharded)
  data/stats.json                   per-run collection statistics
.github/workflows/collect.yml       daily automation
```

## Disclaimer

All data is public SEC filing data, presented as filed. CEOTrades is not
affiliated with the SEC. Nothing here is investment advice.
