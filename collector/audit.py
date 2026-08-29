#!/usr/bin/env python3
"""Deterministic data-integrity audit for CEOTrades.

This module does not create trades and does not infer missing facts. It reads the
canonical store, validates every stored row mechanically, and writes a compact
machine-readable audit artifact for the site.

The audit is deliberately conservative:
  * arithmetic is recomputed from the stored SEC fields;
  * dates, accession numbers, ticker symbols and transaction codes are checked;
  * filing delays are flagged for review, not adjudicated;
  * year-to-date completeness is reported as incomplete unless the observed
    local store plausibly spans the full requested year-to-date window.

Remote SEC re-fetching is optional and off by default because the normal data
collection pipeline is the source of truth. When enabled in an environment that
can reach sec.gov, it verifies accession-level availability against EDGAR.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import store  # noqa: E402

UA = os.environ.get(
    "SEC_UA",
    "CEOTrades integrity audit https://github.com/karagemop466-tech/CEOTrades "
    "karagemop466-tech@users.noreply.github.com",
)

ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-/]{0,15}$")
VALID_CODES = set("PSVADFIMCEHOXGLWZJKU")
VALUE_TOLERANCE = 0.05

OFFICIAL_SOURCES = [
    {
        "name": "SEC Insider Transactions Data Sets",
        "role": "Quarterly bulk source for Forms 3/4/5 ownership XML-derived TSV tables.",
        "url": "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets",
    },
    {
        "name": "SEC EDGAR Daily Index",
        "role": "Daily source for filings not yet included in a quarterly bulk archive.",
        "url": "https://www.sec.gov/Archives/edgar/daily-index/",
    },
    {
        "name": "SEC EDGAR filing archive",
        "role": "Primary source for each filing accession's full ownership XML submission.",
        "url": "https://www.sec.gov/Archives/edgar/data/",
    },
]


def asof_today() -> date:
    """Current date for audit windows, with an override for reproducible runs."""
    override = os.environ.get("CEOTRADES_ASOF_DATE")
    if override:
        try:
            return date.fromisoformat(override[:10])
        except ValueError:
            pass
    return datetime.now(timezone.utc).date()


def utc_stamp() -> str:
    override = os.environ.get("CEOTRADES_GENERATED_AT")
    if override:
        return override
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_day(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def fnum(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def business_days_after(start: date, end: date) -> int:
    """Business days after start through end, weekend-aware only.

    SEC holidays are not guessed here; therefore this is an approximate flag for
    review, not a legal timeliness determination.
    """
    if end <= start:
        return 0
    n = 0
    d = start + timedelta(days=1)
    while d <= end:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


def edgar_url(acc: str, issuer_cik: str | int | None = None) -> str:
    """Official SEC index URL for an accession.

    Ownership-form accession prefixes usually identify the reporting owner or
    filing agent, not necessarily the issuer. Always prefer the issuer CIK from
    the Form 4/5 XML/bulk SUBMISSION row when available.
    """
    a = re.sub(r"[^0-9-]", "", str(acc or ""))
    if not a:
        return ""
    plain = a.replace("-", "")
    cik = re.sub(r"\D", "", str(issuer_cik or "")).lstrip("0")
    if not cik:
        cik = plain[:10].lstrip("0")
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{plain}/{a}-index.htm"


def filing_txt_url(acc: str, issuer_cik: str | int | None = None) -> str:
    a = re.sub(r"[^0-9-]", "", str(acc or ""))
    if not a:
        return ""
    plain = a.replace("-", "")
    cik = re.sub(r"\D", "", str(issuer_cik or "")).lstrip("0")
    if not cik:
        cik = plain[:10].lstrip("0")
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{plain}/{a}.txt"


def row_identity(r: dict) -> str:
    return store.row_key(dict(r))


def row_issue(row: dict, row_number: int, target_year: int | None = None) -> list[dict]:
    issues: list[dict] = []

    def add(code: str, severity: str, message: str):
        issues.append({
            "code": code,
            "severity": severity,
            "row_number": row_number,
            "fd": row.get("fd") or "",
            "td": row.get("td") or "",
            "tk": row.get("tk") or "",
            "co": row.get("co") or "",
            "insider": row.get("in") or "",
            "acc": row.get("acc") or "",
            "icik": row.get("icik") or "",
            "message": message,
            "source_url": edgar_url(row.get("acc") or "", row.get("icik") or ""),
        })

    acc = str(row.get("acc") or "")
    if not acc:
        add("MISSING_ACCESSION", "high", "No SEC accession number is present.")
    elif not ACCESSION_RE.match(acc):
        add("BAD_ACCESSION_FORMAT", "medium", f"Accession has unexpected format: {acc}.")

    fd = parse_day(row.get("fd"))
    td = parse_day(row.get("td"))
    if fd is None:
        add("BAD_FILING_DATE", "high", "Filing date is missing or not ISO formatted.")
    if td is None and row.get("code"):
        add("BAD_TRANSACTION_DATE", "medium", "Transaction date is missing or not ISO formatted.")
    if fd and td and td > fd and str(row.get("form") or "") in {"4", "5"}:
        add("TRANSACTION_AFTER_FILING", "high", "Transaction date is after the filing date.")

    tk = str(row.get("tk") or "").strip().upper()
    if not tk:
        add("MISSING_TICKER", "medium", "Issuer trading symbol is blank; public-market status needs review.")
    elif not TICKER_RE.match(tk):
        add("SUSPICIOUS_TICKER", "low", f"Ticker symbol has unusual characters/length: {tk}.")

    code = str(row.get("code") or "").upper()[:1]
    if not code:
        add("MISSING_TRANSACTION_CODE", "high", "SEC transaction code is blank.")
    elif code not in VALID_CODES:
        add("UNKNOWN_TRANSACTION_CODE", "medium", f"Unknown SEC transaction code: {code}.")

    sh, px, val = fnum(row.get("sh")), fnum(row.get("px")), fnum(row.get("val"))
    if sh is not None and sh < 0:
        add("NEGATIVE_SHARES", "medium", "Transaction shares are negative.")
    if px is not None and px < 0:
        add("NEGATIVE_PRICE", "medium", "Transaction price is negative.")
    if code in {"P", "S"} and (sh is None or sh <= 0):
        add("MISSING_OPEN_MARKET_SHARES", "high", f"Code {code} row lacks positive share count.")
    if code in {"P", "S"} and (px is None or px < 0):
        add("MISSING_OPEN_MARKET_PRICE", "medium", f"Code {code} row lacks a reported price.")
    if sh is not None and px is not None and val is not None:
        calc = round(sh * px, 2)
        if abs(calc - val) > VALUE_TOLERANCE:
            add("VALUE_MISMATCH", "high", f"Reported value {val:.2f} does not equal shares×price {calc:.2f}.")

    if fd and td and str(row.get("form") or "") == "4":
        lag = business_days_after(td, fd)
        if lag > 2:
            add("POSSIBLE_LATE_FORM4", "medium", f"Form 4 filed about {lag} business days after transaction date.")

    return issues


def source_manifest(data_dir: str) -> dict | None:
    path = os.path.join(data_dir, "source_manifest.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"error": "source_manifest.json is unreadable"}


def shard_manifest(data_dir: str) -> list[dict]:
    out = []
    for path in store.shard_files(data_dir):
        try:
            raw = open(path, "rb").read()
        except OSError:
            continue
        out.append({
            "file": os.path.relpath(path, ROOT),
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
    return out


def repo_guard(root: str = ROOT) -> dict:
    """Look for known manual/fabricated-data generators that must not be used."""
    suspect_tokens = (
        "VERIFIED_TRADES = [",
        "make_bars(",
        "verified_sec_market_history",
        "Known price waypoints verified",
    )
    suspects = []
    for rel in ("collector/populate_verified_data.py",):
        path = os.path.join(root, rel)
        if not os.path.exists(path):
            continue
        try:
            text = open(path, encoding="utf-8").read()
        except OSError:
            continue
        hits = [tok for tok in suspect_tokens if tok in text]
        if hits:
            suspects.append({"file": rel, "tokens": hits})
    return {
        "manual_or_synthetic_generators_detected": bool(suspects),
        "suspects": suspects,
        "policy": "Production data must be produced only by SEC collectors; hard-coded trades or synthetic prices are prohibited.",
    }


def _first_last_business_days(year: int, asof: date) -> tuple[date, date]:
    start = date(year, 1, 1)
    end = min(asof - timedelta(days=1), date(year, 12, 31))
    while start.weekday() >= 5:
        start += timedelta(days=1)
    while end.weekday() >= 5:
        end -= timedelta(days=1)
    return start, end


def scan(data_dir: str = store.DATA, target_year: int | None = None,
         asof: date | None = None, max_examples: int = 500) -> dict:
    asof = asof or asof_today()
    target_year = target_year or asof.year
    target_start, target_end = _first_last_business_days(target_year, asof)

    rows = 0
    target_rows = 0
    accs: set[str] = set()
    target_accs: set[str] = set()
    companies: set[str] = set()
    insiders: set[str] = set()
    by_code: Counter[str] = Counter()
    by_issue: Counter[str] = Counter()
    severity_counts: Counter[str] = Counter()
    issue_examples: list[dict] = []
    row_hash = hashlib.sha256()
    dates: list[date] = []
    target_dates: list[date] = []
    month_counts: Counter[str] = Counter()
    target_month_counts: Counter[str] = Counter()
    rows_with_ticker = 0
    rows_without_ticker = 0
    rows_with_price = 0
    derivative_rows = 0
    value_total = 0.0
    buy_value = 0.0
    sell_value = 0.0

    for i, row in enumerate(store.iter_rows(data_dir), 1):
        rows += 1
        fd = parse_day(row.get("fd"))
        acc = row.get("acc") or ""
        if acc:
            accs.add(acc)
        if row.get("icik") or row.get("tk") or row.get("co"):
            companies.add(str(row.get("icik") or row.get("tk") or row.get("co")))
        if row.get("pcik") or row.get("in"):
            insiders.add(str(row.get("pcik") or row.get("in")))
        code = str(row.get("code") or "?").upper()[:1] or "?"
        by_code[code] += 1
        if row.get("tk"):
            rows_with_ticker += 1
        else:
            rows_without_ticker += 1
        if fnum(row.get("px")) is not None:
            rows_with_price += 1
        if row.get("der"):
            derivative_rows += 1
        val = fnum(row.get("val"))
        if val is not None:
            value_total += abs(val)
            if code == "P":
                buy_value += abs(val)
            elif code == "S":
                sell_value += abs(val)
        if fd:
            dates.append(fd)
            month_counts[fd.strftime("%Y-%m")] += 1
            if fd.year == target_year:
                target_rows += 1
                target_dates.append(fd)
                target_month_counts[fd.strftime("%Y-%m")] += 1
                if acc:
                    target_accs.add(acc)

        row_hash.update((row_identity(row) + "\n").encode("utf-8", "replace"))
        # Integrity findings are target-year findings. Rows from other years can
        # coexist in the local store but should not pollute a 2025 audit.
        if target_year is None or fd is None or fd.year == target_year:
            for issue in row_issue(row, i, target_year=target_year):
                by_issue[issue["code"]] += 1
                severity_counts[issue["severity"]] += 1
                if len(issue_examples) < max_examples:
                    issue_examples.append(issue)

    observed_from = min(dates).isoformat() if dates else ""
    observed_to = max(dates).isoformat() if dates else ""
    target_from = min(target_dates).isoformat() if target_dates else ""
    target_to = max(target_dates).isoformat() if target_dates else ""

    expected_months = []
    d = date(target_year, 1, 1)
    while d <= target_end:
        m = d.strftime("%Y-%m")
        if m not in expected_months:
            expected_months.append(m)
        # jump to next month
        if d.month == 12:
            d = date(d.year + 1, 1, 1)
        else:
            d = date(d.year, d.month + 1, 1)
    missing_months = [m for m in expected_months if target_month_counts.get(m, 0) == 0]

    blockers = []
    if target_rows == 0:
        blockers.append("No rows are present for the requested target year.")
    if target_dates:
        if min(target_dates) > target_start + timedelta(days=14):
            blockers.append(
                f"First observed {target_year} filing is {min(target_dates).isoformat()}, "
                f"after the expected year-to-date start window around {target_start.isoformat()}."
            )
        if max(target_dates) < target_end - timedelta(days=4):
            blockers.append(
                f"Last observed {target_year} filing is {max(target_dates).isoformat()}, "
                f"before the expected current EDGAR window ending around {target_end.isoformat()}."
            )
    if missing_months:
        blockers.append("No target-year rows were observed in month(s): " + ", ".join(missing_months) + ".")
    guard = repo_guard(ROOT)
    if guard["manual_or_synthetic_generators_detected"]:
        blockers.append("Manual or synthetic data generator code is present and must be removed/disabled.")
    src_manifest = source_manifest(data_dir)
    if src_manifest is None:
        blockers.append("No collector/data/source_manifest.json from collect_ytd.py is present, so official source coverage is not proven.")
    elif src_manifest.get("error"):
        blockers.append("collector/data/source_manifest.json is unreadable, so official source coverage is not proven.")
    else:
        if src_manifest.get("target_year") != target_year:
            blockers.append("Source manifest target year does not match the audit target year.")
        tw = src_manifest.get("target_window") or []
        if len(tw) == 2:
            if tw[0] > date(target_year, 1, 1).isoformat() or tw[1] < target_end.isoformat():
                blockers.append("Source manifest target window does not cover the full audit year-to-date window.")
        else:
            blockers.append("Source manifest has no target window.")
        unavailable_q = [q.get("label", "?") for q in (src_manifest.get("quarters") or [])
                         if q.get("status") != "ok"]
        if unavailable_q:
            blockers.append("Source manifest has unavailable quarterly archive(s): " + ", ".join(unavailable_q) + ".")
        daily = src_manifest.get("daily") or {}
        if daily and daily.get("status") not in {"ok", "skipped"}:
            blockers.append("Source manifest daily EDGAR collection did not complete successfully.")

    # Missing individual business days are informative but not a completeness
    # proof: some holidays are weekdays, and a very small market day could in
    # theory have no Section 16 transaction rows.
    observed_day_counts = Counter(d.isoformat() for d in target_dates)
    candidate_missing_days = []
    d = target_start
    while d <= target_end:
        if d.weekday() < 5 and observed_day_counts.get(d.isoformat(), 0) == 0:
            candidate_missing_days.append(d.isoformat())
        d += timedelta(days=1)

    return {
        "generated": utc_stamp(),
        "target_year": target_year,
        "asof": asof.isoformat(),
        "official_sources": OFFICIAL_SOURCES,
        "completeness": {
            "status": "complete_candidate" if not blockers else "incomplete_or_unproven",
            "complete": not blockers,
            "target_start": target_start.isoformat(),
            "target_end": target_end.isoformat(),
            "observed_from": observed_from,
            "observed_to": observed_to,
            "target_observed_from": target_from,
            "target_observed_to": target_to,
            "missing_months": missing_months,
            "candidate_missing_business_days": candidate_missing_days[:250],
            "candidate_missing_business_days_truncated": len(candidate_missing_days) > 250,
            "blockers": blockers,
            "note": "Completeness is based on the local store. A full proof requires a successful SEC bulk/daily collection run for the target window.",
        },
        "counts": {
            "rows": rows,
            "target_year_rows": target_rows,
            "filings": len(accs),
            "target_year_filings": len(target_accs),
            "companies": len(companies),
            "insiders": len(insiders),
            "with_ticker": rows_with_ticker,
            "without_ticker": rows_without_ticker,
            "with_price": rows_with_price,
            "derivative_rows": derivative_rows,
        },
        "value": {
            "total_abs_reported": round(value_total, 2),
            "code_p_buy": round(buy_value, 2),
            "code_s_sell": round(sell_value, 2),
            "net_p_minus_s": round(buy_value - sell_value, 2),
        },
        "by_code": [{"code": k, "n": v} for k, v in sorted(by_code.items(), key=lambda kv: (-kv[1], kv[0]))],
        "integrity": {
            "row_hash_sha256": row_hash.hexdigest(),
            "row_issues": sum(by_issue.values()),
            "by_issue": [{"code": k, "n": v} for k, v in sorted(by_issue.items(), key=lambda kv: (-kv[1], kv[0]))],
            "by_severity": [{"severity": k, "n": v} for k, v in sorted(severity_counts.items())],
            "issue_examples": issue_examples,
            "manual_data_guard": guard,
        },
        "source_manifest": src_manifest,
        "shards": shard_manifest(data_dir),
        "assurance": {
            "remote_refetch": "not_performed_by_default",
            "line_mapping": "Rows are parsed by named SEC XML/TSV fields. This audit recomputes every row's arithmetic and source URL deterministically.",
            "no_hallucination_policy": "If a field is absent from SEC source data it remains blank/null; values are not guessed or generated.",
        },
    }


def http_get(url: str, timeout: int = 30) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            return raw
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return None


def verify_accessions(rows: Iterable[dict], limit: int = 0, rate: float = 4.0) -> dict:
    """Optional EDGAR accession availability check.

    It intentionally verifies accession-level availability only; line-level
    comparisons are handled by the SEC parsers and row arithmetic audit.
    """
    seen = {}
    min_interval = 1.0 / max(0.1, min(rate, 8.0))
    next_at = 0.0
    checked = ok = failed = 0
    failures = []
    for r in rows:
        acc = r.get("acc") or ""
        if not acc or acc in seen:
            continue
        url = filing_txt_url(acc, r.get("icik") or "")
        now = time.monotonic()
        if now < next_at:
            time.sleep(next_at - now)
        next_at = max(now, next_at) + min_interval
        raw = http_get(url)
        checked += 1
        good = bool(raw and acc.encode() in raw[:5000])
        if good:
            ok += 1
        else:
            failed += 1
            if len(failures) < 100:
                failures.append({"acc": acc, "icik": r.get("icik") or "", "url": url})
        seen[acc] = good
        if limit and checked >= limit:
            break
    return {"checked": checked, "ok": ok, "failed": failed, "failures": failures}


def write_json(obj, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def report_markdown(audit: dict) -> str:
    c = audit["counts"]
    comp = audit["completeness"]
    integ = audit["integrity"]
    status = "COMPLETE CANDIDATE" if comp["complete"] else "INCOMPLETE / UNPROVEN"
    lines = [
        "# CEOTrades Data Integrity & Verification Report",
        "",
        f"**Generated:** {audit['generated']}",
        f"**Target year:** {audit['target_year']}",
        f"**Status:** **{status}**",
        "",
        "This report is generated from the local canonical store. It is intentionally conservative: "
        "when the store does not prove full year-to-date coverage, the report says so instead of "
        "claiming a complete list.",
        "",
        "## Official sources",
        "",
    ]
    for s in audit["official_sources"]:
        lines.append(f"- [{s['name']}]({s['url']}) — {s['role']}")
    lines += [
        "",
        "## Local store summary",
        "",
        f"- Rows: **{c['rows']:,}** ({c['target_year_rows']:,} in target year)",
        f"- Filings/accessions: **{c['filings']:,}** ({c['target_year_filings']:,} in target year)",
        f"- Issuers: **{c['companies']:,}**",
        f"- Reporting owners: **{c['insiders']:,}**",
        f"- Rows with ticker: **{c['with_ticker']:,}**; without ticker: **{c['without_ticker']:,}**",
        f"- Filing-date range observed: **{comp['observed_from'] or 'none'} → {comp['observed_to'] or 'none'}**",
        f"- Target-year observed range: **{comp['target_observed_from'] or 'none'} → {comp['target_observed_to'] or 'none'}**",
        "- Line-by-line ledger: `data/csv/trades-YYYY.csv.gz` (all stored rows by filing year) and `data/recent.json` (UI tape).",
        "",
        "## Completeness assessment",
        "",
    ]
    if comp["complete"]:
        lines.append("No local completeness blockers were detected. This is a candidate status, not a guarantee that the SEC source was reachable during this report build.")
    else:
        for b in comp["blockers"]:
            lines.append(f"- **Blocker:** {b}")
    lines += [
        "",
        "## Row-level integrity checks",
        "",
        f"- Deterministic row hash: `{integ['row_hash_sha256']}`",
        f"- Row issues detected: **{integ['row_issues']:,}**",
    ]
    for item in integ["by_issue"][:20]:
        lines.append(f"  - {item['code']}: {item['n']:,}")
    if not integ["by_issue"]:
        lines.append("  - None detected by the local mechanical checks.")
    lines += [
        "",
        "## No-hallucination controls",
        "",
        "- Production collectors read SEC XML/TSV columns by name. Missing source fields stay blank/null.",
        "- Paper-trade entries use market bars only when a real price source/cache is available; absent prices are marked `no_price` rather than estimated.",
        "- Hard-coded trade lists and synthetic price paths are prohibited by the audit guard.",
        "",
        f"## Rebuild command for the {audit['target_year']} target",
        "",
        "```bash",
        f"python3 collector/collect_ytd.py --year {audit['target_year']} --replace-year",
        f"python3 collector/build_data.py --year {audit['target_year']} --audit-year {audit['target_year']}",
        "python3 collector/build_pages.py",
        "python3 collector/selftest.py && python3 collector/test_bulk.py && python3 collector/test_paper.py && python3 collector/test_site.py",
        "```",
        "",
        "If the SEC or market-data endpoints are unavailable, the build must fail or mark gaps explicitly; it must not fabricate rows or prices.",
    ]
    return "\n".join(lines) + "\n"


def write_audit(data_dir: str = store.DATA, out_dir: str | None = None,
                target_year: int | None = None, report_path: str | None = None) -> dict:
    out_dir = out_dir or os.path.join(ROOT, "data")
    audit = scan(data_dir=data_dir, target_year=target_year)
    write_json(audit, os.path.join(out_dir, "audit.json"))
    if report_path:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_markdown(audit))
    return audit


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit the CEOTrades trade store without inventing data.")
    ap.add_argument("--data", default=store.DATA)
    ap.add_argument("--out", default=os.path.join(ROOT, "data"))
    ap.add_argument("--year", type=int, default=asof_today().year)
    ap.add_argument("--report", default=os.path.join(ROOT, "INSIDER_TRADING_FORENSIC_REPORT.md"))
    ap.add_argument("--verify-edgar", action="store_true", help="optional accession-level SEC refetch")
    ap.add_argument("--verify-limit", type=int, default=0, help="max unique accessions to refetch; 0 = all")
    args = ap.parse_args()

    audit = write_audit(args.data, args.out, args.year, args.report)
    if args.verify_edgar:
        rows = store.iter_rows(args.data)
        audit["assurance"]["remote_refetch"] = verify_accessions(rows, limit=args.verify_limit)
        write_json(audit, os.path.join(args.out, "audit.json"))
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(report_markdown(audit))
    comp = audit["completeness"]
    print(f"audit: {audit['counts']['rows']:,} rows; status={comp['status']}; issues={audit['integrity']['row_issues']:,}")
    if comp["blockers"]:
        print("completeness blockers:")
        for b in comp["blockers"]:
            print(" - " + b)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
