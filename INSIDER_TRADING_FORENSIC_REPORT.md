# CEOTrades Data Integrity & Verification Report

**Generated:** 2026-08-28T23:59:00Z
**Target year:** 2025
**Status:** **INCOMPLETE / UNPROVEN**

This report is generated from the local canonical store. It is intentionally conservative: when the store does not prove full year-to-date coverage, the report says so instead of claiming a complete list.

## Official sources

- [SEC Insider Transactions Data Sets](https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets) — Quarterly bulk source for Forms 3/4/5 ownership XML-derived TSV tables.
- [SEC EDGAR Daily Index](https://www.sec.gov/Archives/edgar/daily-index/) — Daily source for filings not yet included in a quarterly bulk archive.
- [SEC EDGAR filing archive](https://www.sec.gov/Archives/edgar/data/) — Primary source for each filing accession's full ownership XML submission.

## Local store summary

- Rows: **66** (0 in target year)
- Filings/accessions: **32** (0 in target year)
- Issuers: **21**
- Reporting owners: **28**
- Rows with ticker: **66**; without ticker: **0**
- Filing-date range observed: **2026-03-06 → 2026-08-27**
- Target-year observed range: **none → none**
- Line-by-line ledger: `data/csv/trades-YYYY.csv.gz` (all stored rows by filing year) and `data/recent.json` (UI tape).

## Completeness assessment

- **Blocker:** No rows are present for the requested target year.
- **Blocker:** No target-year rows were observed in month(s): 2025-01, 2025-02, 2025-03, 2025-04, 2025-05, 2025-06, 2025-07, 2025-08, 2025-09, 2025-10, 2025-11, 2025-12.
- **Blocker:** No collector/data/source_manifest.json from collect_ytd.py is present, so official source coverage is not proven.

## Row-level integrity checks

- Deterministic row hash: `a9c954ebbd2864d1eed9a46756ac0513f08fd0d0c20a218435041de6f01c6118`
- Row issues detected: **0**
  - None detected by the local mechanical checks.

## No-hallucination controls

- Production collectors read SEC XML/TSV columns by name. Missing source fields stay blank/null.
- Paper-trade entries use market bars only when a real price source/cache is available; absent prices are marked `no_price` rather than estimated.
- Hard-coded trade lists and synthetic price paths are prohibited by the audit guard.

## Rebuild command for the 2025 target

```bash
python3 collector/collect_ytd.py --year 2025 --replace-year
python3 collector/build_data.py --year 2025 --audit-year 2025
python3 collector/build_pages.py
python3 collector/selftest.py && python3 collector/test_bulk.py && python3 collector/test_paper.py && python3 collector/test_site.py
```

If the SEC or market-data endpoints are unavailable, the build must fail or mark gaps explicitly; it must not fabricate rows or prices.
