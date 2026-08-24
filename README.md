# CEOTrades — Insider Trade Dashboard

A research dashboard that collects **every Section 16 insider transaction**
(Form 4 filings) at publicly traded companies from SEC EDGAR, organizes them by
company, sector, insider role and transaction type, and publishes them as a
clean static site on GitHub Pages.

**Live site:** `https://karagemop466-tech.github.io/CEOTrades/`

## Data source (verified, no estimates)

| Input | Endpoint | Used for |
|---|---|---|
| Filing index | `https://efts.sec.gov/LATEST/search-index` | Enumerating every Form 4 filed in the window (fallback: EDGAR daily index) |
| Raw filings | `https://www.sec.gov/Archives/edgar/data/...` | Machine-readable `ownershipDocument` XML of each filing |
| SIC titles | `https://www.sec.gov/search-filings/standard-industrial-classification-sic-code-list` | Industry titles on the sectors page |
| Code meanings | `https://www.sec.gov/files/form4.pdf` | Transaction-code descriptions (Form 4, Instruction 8) |

Every transaction row on the site links to the original SEC filing, so any
number can be checked line by line against the source document.

## What's collected

* **All Form 4 filings** in the configured window (default: last 7 days) —
  every report of insider holdings changes by directors, officers and 10% owners.
* **Every transaction line** from Table I (equity) and Table II (derivatives):
  code, acquisition/disposition flag, transaction date, shares, per-share price,
  post-transaction holdings, relationship (role + officer title), Rule 10b5-1(c)
  plan flag, and footnotes.

Not included (by design, documented on the site): Form 3, Form 5, Form 144,
13D/G, and it is not investment advice.

## Pipeline

```
scripts/fetch_insider_trades.py   # enumerate -> download -> parse -> aggregate
scripts/validate_data.py          # offline validation + writes validation.json
scripts/verify_sample.py          # re-downloads a random sample from SEC
scripts/build_sic_map.py          # official SEC SIC code -> title map
scripts/site_test.js              # Node tests for browser-side aggregations
```

The `.github/workflows/update-data.yml` workflow runs the pipeline daily
(03:23 UTC) or manually, then commits `data/*.json`; GitHub Pages serves the repository root.
Source code, scripts and data are all in this repository.

## Run locally

```bash
# 1) Serve the static site
python3 -m http.server 8000   # open http://localhost:8000

# 2) Refresh the data (needs direct access to SEC endpoints)
python3 scripts/build_sic_map.py
python3 scripts/fetch_insider_trades.py --days 7
python3 scripts/validate_data.py
python3 scripts/verify_sample.py --n 20
node scripts/site_test.js
```

## Verification

* `validate_data.py` independently recomputes every summary number from the
  raw transaction list; any mismatch fails the run.
* `verify_sample.py` re-downloads a deterministic random sample of filings
  from SEC, re-parses them, and confirms issuer CIK, owner CIK, period and the
  exact transaction row before results are written to `docs/data/verification.json`.
* Parse failures are written to `data/errors.json` and shown on the
  methodology page — nothing is silently dropped.

## Project layout

The site lives at the repository root (GitHub Pages is configured for `main /`):

```
index.html              # landing dashboard
trades.html             # searchable/filterable table of all trades
by-type.html            # transaction-code categories
by-role.html            # officer / director / 10% owner / other
by-company.html         # issuers with insider activity
by-sector.html          # SEC SIC industry groups
filings.html            # source filings, linked to EDGAR
methodology.html        # pipeline, caveats, validation + verification reports
assets/                 # dependency-free CSS/JS (system fonts, no CDN)
data/                   # generated JSON (committed by the workflow)
scripts/                # collection + validation pipeline (Python 3 stdlib)
misc/update-data.yml    # daily refresh workflow (see misc/README.md)
```
