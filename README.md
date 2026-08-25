# CEOTrades — Insider Trade Intelligence

**Every insider trade at publicly traded companies, collected, organized and
analyzed automatically. Zero manual input.**

CEOTrades continuously pulls **all** SEC Form 4 (Statement of Changes in
Beneficial Ownership) and Form 5 (annual catch-all) filings from
[SEC EDGAR](https://www.sec.gov/edgar/searchedgar/companysearch), parses every
transaction, and publishes a clean static dashboard on GitHub Pages:

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
 └─────────────┘                       └────────┬─────────
                                                │ 1. list all Form 4/5 filed per day
                                                │    (SEC daily master index,
                                                │     FTS API as fallback)
                                                │ 2. download each filing's XML
                                                │    (throttled to 8 req/s, SEC
                                                │     fair-access compliant)
                                                │ 3. parse every transaction
                                                │ 4. merge into growing dataset
                                                │    (amendments supersede originals)
                                                ▼
                                   ┌──────────────────┐
                                   │ collector/       │
                                   │ build_site.py    │
                                   └────────┬─────────┘
                                            │ regenerate data + HTML pages
                                            ▼
                          commit & push  ──►  GitHub Pages publishes
```

- **Enumeration** — `https://www.sec.gov/Archives/edgar/daily-index/YYYY/MM/DD/master.json`
  lists every filing accepted each day; the EDGAR full-text search API
  (`https://efts.sec.gov/LATEST/search-index`) is the automatic fallback and
  cross-check.
- **Extraction** — each filing's structured XML is parsed (issuer, insider,
  relationship, and all non-derivative + derivative transactions with dates,
  codes, share counts and prices).
- **Storage** — `collector/data/trades.json` is a growing, de-duplicated
  dataset (keyed by accession; latest amendment wins per issuer/insider/period).
- **Publishing** — `collector/build_site.py` regenerates `data/*.json`, the
  full `data/trades.csv` and all HTML pages; the workflow commits and pushes,
  GitHub Pages serves it. Runs daily; the dataset only ever grows.

## Running it yourself

```bash
python3 collector/selftest.py        # offline parser tests
python3 collector/collect.py --days 3   # collect the last 3 days
python3 collector/build_site.py       # regenerate the site
```

Python 3.10+ with the **standard library only** — no dependencies, no API keys.
The collector is idempotent and incremental: re-running a day replaces that
day's rows; previously collected days are skipped (use `--force` to redo).

Useful flags: `--start 2026-01-01 --end 2026-01-31` (explicit window),
`--rate 8` (max req/s; SEC allows 10), `--data-dir <dir>`.

## Data dictionary (per trade row)

| Field | Meaning |
|---|---|
| `acc` | SEC accession number (links to the filing on EDGAR) |
| `form` | `4` or `5` |
| `fd` / `td` | Filing date (EDGAR acceptance) / transaction date |
| `co`, `tk`, `icik` | Issuer company, ticker, CIK |
| `in`, `pcik` | Insider (reporting person), CIK |
| `rel`, `title` | Relationship (Director / Officer / 10% Owner / Other), officer title |
| `code`, `ct`, `side` | Official transaction code, its text, dashboard side |
| `sec`, `der`, `under` | Security title, derivative flag, underlying security |
| `sh`, `px`, `val` | Shares, price/share, value = sh×px (blank when unpriced) |
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
- Grants/gifts are reported without a price — value is blank, not zero.
- Amendments (`4/A`) supersede the original; the most recent version is kept.
- Filers for issuers without a U.S. ticker are included (ticker shown as —).
- Filings submitted after the nightly index cutoff appear the next business day
  (EDGAR's own dissemination rule) — the 3-day overlap window in the daily job
  guarantees they are captured.

## Repository layout

```
index.html trades.html companies.html insiders.html
analysis.html about.html        static dashboard (GitHub Pages, root)
css/ js/ data/                  site assets + generated data
collector/
  collect.py                    SEC EDGAR collector (stdlib only)
  build_site.py                 static site generator (stdlib only)
  selftest.py                   offline parser tests
  data/trades.json              the growing dataset (committed)
  data/stats.json               per-run collection statistics
.github/workflows/collect.yml   daily automation
```

## Disclaimer

All data is public SEC filing data, presented as filed. CEOTrades is not
affiliated with the SEC. Nothing here is investment advice.
