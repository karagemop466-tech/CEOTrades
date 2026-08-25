#!/usr/bin/env python3
"""
CEOTrades collector — pulls ALL insider trades (Form 4 and Form 5) filed on
U.S. publicly traded companies from SEC EDGAR, with no manual input.

Data sources (official SEC endpoints only):
  1. Daily master index (primary enumeration):
     https://www.sec.gov/Archives/edgar/daily-index/YYYY/MM/DD/master.json
  2. EDGAR full-text search API (fallback enumeration, also used as cross-check):
     https://efts.sec.gov/LATEST/search-index?q="4"&forms=4&dateRange=custom&startdt=...&enddt=...&from=N
  3. Filing documents (XML):
     https://www.sec.gov/Archives/edgar/data/{CIK}/{ACCESSION_NO_DASHES}/{PRIMARY_DOC}

Rules (SEC "Fair Access" policy):
  - User-Agent header identifying the caller (required, else 403).
  - Max 10 requests/second; we stay at 8/s via a global throttle.
  - Indexes update nightly ~10:00 PM ET; ownership forms filed late are
    disseminated the next business day.

The dataset is incremental: each run merges new filings into collector/data/
trades.json (keyed by accession number), so the history grows over time.

Standard library only — no third-party dependencies.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

UA = "CEOTrades Insider-Trade Collector (github.com/karagemop466-tech/CEOTrades)"
ARCHIVE = "https://www.sec.gov/Archives"
DAILY_INDEX = ARCHIVE + "/edgar/daily-index"
DATA = ARCHIVE + "/edgar/data"
FTS = "https://efts.sec.gov/LATEST/search-index"

# Forms to collect: 4 = statement of changes in beneficial ownership (daily),
# 5 = annual statement (catches transactions not reported on Form 4).
FORMS = ("4", "5")

# Universal FTS text token per form: every Form N XML contains the literal
# <documentType>N</documentType>, so the token N appears in every filing.
FTS_TOKEN = {"4": "4", "5": "5"}

# Official Form 4/5 transaction code table (SEC instructions, Item 5).
CODE_TEXT = {
    "P": "Open market or private purchase of securities",
    "S": "Open market or private sale of securities",
    "V": "Transaction voluntarily reported earlier than required",
    "A": "Grant, award, or other acquisition",
    "D": "Sale (or disposition) back to the issuer",
    "F": "Payment of exercise price or tax liability by delivering/withholding securities",
    "I": "Discretionary transaction",
    "M": "Exercise or conversion of derivative security",
    "C": "Conversion of derivative security",
    "E": "Expiration of short derivative position",
    "H": "Expiration or cancellation of long derivative position with value received",
    "O": "Exercise of out-of-the-money derivative securities",
    "X": "Exercise of in-the-money or at-the-money derivative securities",
    "G": "Bona fide gift",
    "L": "Small acquisition",
    "W": "Acquisition or disposition by will or laws of descent and distribution",
    "Z": "Deposit into or withdrawal from voting trust",
    "J": "Other acquisition or disposition (described in footnotes)",
    "K": "Transaction in equity swap or similar instrument",
    "U": "Disposition due to a tender of shares in a change of control transaction",
}

# Dashboard side classification (open-market flow = P minus S).
SIDE = {
    "P": "buy",
    "S": "sell",
    "M": "exercise", "C": "exercise", "X": "exercise", "O": "exercise",
    "A": "grant",
    "F": "withholding", "D": "to_issuer", "E": "expiration", "H": "expiration",
    "G": "gift",
    "J": "other", "K": "other", "L": "other", "U": "other", "V": "other",
    "W": "other", "Z": "other",
}


def code_side(code: str) -> str:
    return SIDE.get(code, "other")


# ---------------------------------------------------------------------------
# HTTP layer (throttled, retrying, gzip-aware)
# ---------------------------------------------------------------------------

class Throttle:
    """Enforces a minimum interval between outbound requests."""

    def __init__(self, rate_per_sec: float):
        self.min_interval = 1.0 / rate_per_sec
        self._next = 0.0

    def wait(self):
        now = time.monotonic()
        delta = self._next - now
        if delta > 0:
            time.sleep(delta)
        self._next = max(now, self._next) + self.min_interval


THROTTLE = Throttle(8.0)
STATS = {
    "requests": 0,
    "errors": [],
    "days": {},
}


def http_get(url: str, retries: int = 3, timeout: int = 30):
    """GET a URL honoring SEC fair-access rules. Returns bytes or None.

    None is returned for 404 (resource missing) or after exhausted retries.
    """
    last_err = None
    for attempt in range(retries + 1):
        THROTTLE.wait()
        STATS["requests"] += 1
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept-Encoding": "gzip, deflate",
            "Host": url.split("/")[2],
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                return raw
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last_err = f"HTTP {e.code} for {url}"
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last_err = f"{type(e).__name__}: {e} for {url}"
        # Backoff: 15s, 45s, 120s (SEC may throttle for up to 10 minutes on 429)
        if attempt < retries:
            time.sleep(15 * (3 ** attempt))
    log(f"  ! giving up: {last_err}")
    STATS["errors"].append(last_err)
    return None


def log(msg: str):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# XML parsing (ownership documents: Form 4 / Form 5)
# ---------------------------------------------------------------------------

def _txt(el, path: str) -> str:
    """Text of el//path, whitespace-stripped, '' if absent."""
    if el is None:
        return ""
    node = el.find(path)
    if node is None or node.text is None:
        return ""
    return (node.text or "").strip()


def _num(el, path: str):
    """Numeric value at el//path (value element may be nested) or None."""
    s = _txt(el, path)
    if s == "":
        return None
    s = s.replace(",", "").replace("$", "").strip()
    try:
        f = float(s)
        return int(f) if f.is_integer() else f
    except ValueError:
        return None


def _rels(rel_el) -> tuple[list[str], str]:
    rels = []
    if _txt(rel_el, "isDirector") == "1":
        rels.append("Director")
    if _txt(rel_el, "isOfficer") == "1":
        rels.append("Officer")
    if _txt(rel_el, "isTenPercentOwner") == "1":
        rels.append("10% Owner")
    if _txt(rel_el, "isOther") == "1":
        rels.append("Other")
    title = _txt(rel_el, "officerTitle")
    return rels, title


def _parse_non_derivative(tx: ET.Element) -> dict:
    t = {
        "sec": _txt(tx, "securityTitle/value"),
        "td": _txt(tx, "transactionDate/value"),
        "code": _txt(tx, "transactionCoding/transactionCode"),
        "swap": _txt(tx, "transactionCoding/equitySwapInvolved") == "1",
        "sh": _num(tx, "sharesTransactioned/value"),
        "px": _num(tx, "pricePerShare/value"),
        "ad": _txt(tx, "acquisitionOrDispositionCode/value"),
        "af": _num(tx, "sharesOwnedFollowingTransaction/value"),
        "di": _txt(tx, "directOrIndirectOwnership/value"),
        "der": 0,
        "under": "",
        "putcall": "",
        "exp": "",
    }
    t["aod_sh"] = _num(tx, "sharesAcquiredOrDisposed/value")
    return t


def _parse_derivative(tx: ET.Element) -> dict:
    t = {
        "sec": _txt(tx, "securityTitle/value"),
        "td": _txt(tx, "transactionDate/value"),
        "code": _txt(tx, "transactionCoding/transactionCode"),
        "swap": _txt(tx, "transactionCoding/equitySwapInvolved") == "1",
        "sh": _num(tx, "sharesTransactioned/value"),
        "px": _num(tx, "conversionOrExercisePrice/value"),
        "ad": _txt(tx, "acquisitionOrDispositionCode/value"),
        "af": _num(tx, "sharesOwnedFollowingTransaction/value"),
        "di": _txt(tx, "directOrIndirectOwnership/value"),
        "der": 1,
        "under": _txt(tx, "underlyingSecurity/securityTitle/value"),
        "putcall": _txt(tx, "putCall/value"),
        "exp": _txt(tx, "expirationDate/value"),
    }
    t["aod_sh"] = _num(tx, "sharesAcquiredOrDisposed/value")
    return t


def parse_ownership(xml_bytes: bytes) -> dict | None:
    """Parse a Form 4/5 ownership XML document.

    Returns a dict:
      {schema, docType, period, icik, iname, iticker, pcik, pname,
       rels: [..], title, trades: [..]}
    or None if the document is not parseable ownership XML.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    if root.tag != "ownershipDocument":
        return None

    out = {
        "schema": _txt(root, "schemaVersion"),
        "docType": _txt(root, "documentType"),
        "period": _txt(root, "periodOfReporting"),
        "icik": _txt(root, "issuer/issuerCik"),
        "iname": _txt(root, "issuer/issuerName"),
        "iticker": _txt(root, "issuer/issuerTradingSymbol"),
        "pcik": _txt(root, "reportingOwner/reportingOwnerId/rptOwnerCik"),
        "pname": _norm_name(_txt(root, "reportingOwner/reportingOwnerId/rptOwnerName")),
        "rels": [],
        "title": "",
        "trades": [],
    }
    out["iname"] = _norm_name(out["iname"])
    rel_el = root.find("reportingOwner/reportingOwnerRelationship")
    out["rels"], out["title"] = _rels(rel_el)

    for tx in root.iter("nonDerivativeTransaction"):
        t = _parse_non_derivative(tx)
        t["val"] = _value(t)
        out["trades"].append(t)
    for tx in root.iter("derivativeTransaction"):
        t = _parse_derivative(tx)
        t["val"] = _value(t)
        out["trades"].append(t)
    return out


def _value(t: dict):
    if t.get("sh") is None or t.get("px") is None:
        return None
    try:
        v = float(t["sh"]) * float(t["px"])
        if v == int(v):
            return int(v)
        return round(v, 2)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Enumeration: list the Form 4/5 filings for a given date
# ---------------------------------------------------------------------------

def _norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def enum_master_json(day: date) -> tuple[list[dict], str]:
    """Enumerate Form 4/5 filings for `day` via the daily master index.

    Returns (records, note). Records: {acc, doc, form, fd}
    doc = primary document path (may include a subdirectory prefix such as
    'xslF345X06/form4.xml').
    """
    url = f"{DAILY_INDEX}/{day.year:04d}/{day.month:02d}/{day.day:02d}/master.json"
    raw = http_get(url)
    if raw is None:
        return [], f"master.json 404 for {day}"
    try:
        entries = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError as e:
        return [], f"master.json not JSON ({e}); first 200 bytes: {raw[:200]!r}"
    if not isinstance(entries, list):
        return [], f"master.json unexpected shape: {str(entries)[:200]}"

    records, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        adsh = str(entry.get("adsh") or entry.get("accessionNumber") or "")
        filings = entry.get("filings")
        if isinstance(filings, dict):  # tolerate a columnar layout
            filings = [filings]
        if not isinstance(filings, list):
            continue
        for f in filings:
            if not isinstance(f, dict):
                continue
            form = str(f.get("form") or "")
            if form not in FORMS:
                continue
            doc = str(f.get("primary_doc") or f.get("primaryDocument") or "")
            fd = str(f.get("filed") or f.get("filedAt") or day.isoformat())
            if not adsh or not doc or adsh in seen:
                continue
            seen.add(adsh)
            records.append({"acc": adsh, "doc": doc, "form": form, "fd": fd})
    if not records:
        note = "master.json fetched but 0 Form 4/5 parsed (schema mismatch?)"
        STATS["master_sample"] = raw[:600]
        return [], note
    return records, f"master.json: {len(records)} filings"


def enum_fts(day: date) -> tuple[list[dict], str]:
    """Enumerate Form 4/5 filings for `day` via the EDGAR full-text search API.

    Verified live: q="<form number>"&forms=<n>&dateRange=custom&startdt=D&enddt=D
    matches every filing of that form (the number appears in <documentType>).
    Returns (records, note) where records are {acc, doc, form, fd}.
    """
    records, seen = [], set()
    for form in FORMS:
        token = urllib.parse.quote(FTS_TOKEN[form])
        from_ = 0
        while from_ < 20000:
            url = (
                f"{FTS}?q={token}&forms={form}&dateRange=custom"
                f"&startdt={day.isoformat()}&enddt={day.isoformat()}&from={from_}"
            )
            raw = http_get(url)
            if raw is None:
                return records, f"FTS page failed for {form} at from={from_}"
            try:
                data = json.loads(raw.decode("utf-8", "replace"))
            except json.JSONDecodeError as e:
                return records, f"FTS not JSON ({e}); first 200 bytes: {raw[:200]!r}"
            hits = data.get("hits", {})
            total = (hits.get("total") or {}).get("value", 0)
            for hit in hits.get("hits", []):
                src = hit.get("_source", {})
                if str(src.get("form", "")).replace("/A", "") not in ("4", "5"):
                    continue
                file_type = str(src.get("file_type") or "")
                if file_type not in ("4", "5", "4/A", "5/A"):
                    continue
                hit_id = hit.get("_id", "")
                if ":" not in hit_id:
                    continue
                adsh, fname = hit_id.split(":", 1)
                if not fname.lower().endswith(".xml") or adsh in seen:
                    continue
                xsl = src.get("xsl")
                doc = f"{xsl}/{fname}" if xsl else fname
                seen.add(adsh)
                records.append({
                    "acc": adsh,
                    "doc": doc,
                    "form": "4" if file_type in ("4", "4/A") else "5",
                    "fd": str(src.get("file_date") or day.isoformat()),
                })
            if from_ + 100 >= total:
                break
            from_ += 100
    return records, f"FTS: {len(records)} filings"


def enum_day(day: date) -> tuple[list[dict], str]:
    records, note = enum_master_json(day)
    if not records:
        fts_records, fts_note = enum_fts(day)
        if fts_records:
            return fts_records, f"fallback: {note} -> {fts_note}"
        return [], f"{note} -> {fts_note}"
    return records, note


# ---------------------------------------------------------------------------
# Document fetching + row production
# ---------------------------------------------------------------------------

def accession_cik(acc: str) -> str:
    """CIK used in the archive path = filer CIK = first 10 digits of acc."""
    return str(int(acc.split("-")[0]))


def filing_url(acc: str, doc: str) -> str:
    return f"{DATA}/{accession_cik(acc)}/{acc.replace('-', '')}/{doc}"


def doc_url(acc: str, doc: str) -> str:
    return filing_url(acc, doc)


def rows_from_filing(acc: str, form: str, fd: str, doc: str) -> list[dict]:
    raw = http_get(doc_url(acc, doc))
    if raw is None:
        STATS["errors"].append(f"document 404: {doc_url(acc, doc)}")
        return []
    parsed = parse_ownership(raw)
    if parsed is None:
        STATS["errors"].append(f"unparseable: {doc_url(acc, doc)}")
        return []
    rels = "/".join(parsed["rels"]) or "Unknown"
    rows = []
    for t in parsed["trades"]:
        val = t.get("val")
        rows.append({
            "acc": acc,
            "form": form,
            "fd": fd,                 # filing date (EDGAR acceptance)
            "td": t["td"],            # transaction date
            "period": parsed["period"],
            "icik": parsed["icik"],
            "co": _norm_name(parsed["iname"]),
            "tk": (parsed["iticker"] or "").strip().upper(),
            "pcik": parsed["pcik"],
            "in": _norm_name(parsed["pname"]),
            "rel": rels,
            "title": _norm_name(parsed["title"]),
            "code": t["code"],
            "ct": CODE_TEXT.get(t["code"], f"Unknown code ({t['code']})"),
            "side": code_side(t["code"]),
            "sec": _norm_name(t["sec"]),
            "sh": t["sh"],
            "px": t["px"],
            "val": val,
            "ad": t["ad"],
            "af": t["af"],
            "di": t["di"],
            "der": t["der"],
            "under": _norm_name(t["under"]),
            "putcall": t["putcall"],
            "exp": t["exp"],
        })
    return rows


def collect_day(day: date) -> list[dict]:
    log(f"== {day.isoformat()}")
    records, note = enum_day(day)
    STATS["days"][day.isoformat()] = {"note": note, "filings": len(records), "trades": 0}
    log(f"   enumeration: {note}")
    rows = []
    for i, rec in enumerate(records, 1):
        if i % 100 == 0:
            log(f"   ... {i}/{len(records)} filings")
        got = rows_from_filing(rec["acc"], rec["form"], rec["fd"], rec["doc"])
        rows.extend(got)
    STATS["days"][day.isoformat()]["trades"] = len(rows)
    log(f"   -> {len(rows)} trades")
    return rows


# ---------------------------------------------------------------------------
# Merge (incremental dataset + amendment de-duplication)
# ---------------------------------------------------------------------------

def merge_dataset(existing: list[dict], new_rows: list[dict]) -> list[dict]:
    """Merge new rows into the dataset.

    Key: accession number (one filing may contribute several trade rows).
    Amendment rule: a 4/A (or later) filing supersedes an earlier filing for
    the same (issuer, insider, periodOfReporting) — keep the most recently
    filed set.
    """
    by_acc = {}
    for r in existing:
        by_acc.setdefault(r["acc"], []).append(r)
    for r in new_rows:
        by_acc[r["acc"]] = [r]

    # Group filings by (issuer, insider, period) to resolve amendments.
    groups: dict[tuple, list] = {}
    for acc, rs in by_acc.items():
        key = (rs[0]["icik"], rs[0]["pcik"], rs[0].get("period", ""))
        groups.setdefault(key, []).append((rs[0]["fd"], acc, rs))
    keep_accs = set()
    for key, lst in groups.items():
        if len(lst) == 1:
            keep_accs.add(lst[0][1])
            continue
        # Multiple filings for same issuer+insider+period:
        # keep the one with the latest filing date (amendment wins).
        lst.sort(key=lambda x: x[0], reverse=True)
        keep_accs.add(lst[0][1])

    merged = []
    for acc, rs in by_acc.items():
        if acc in keep_accs:
            merged.extend(rs)
    merged.sort(key=lambda r: (r["fd"], r["td"], r["co"], r["in"]), reverse=True)
    return merged


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Collect SEC Form 4/5 insider trades")
    ap.add_argument("--days", type=int, default=3,
                    help="collect this many calendar days ending yesterday")
    ap.add_argument("--start", help="explicit start date YYYY-MM-DD (overrides --days)")
    ap.add_argument("--end", help="explicit end date YYYY-MM-DD (default yesterday)")
    ap.add_argument("--rate", type=float, default=8.0,
                    help="max requests per second (SEC limit: 10)")
    ap.add_argument("--force", action="store_true",
                    help="re-collect days already marked complete")
    ap.add_argument("--data-dir", default=os.path.join(os.path.dirname(__file__), "data"))
    args = ap.parse_args()

    global THROTTLE
    THROTTLE = Throttle(args.rate)

    os.makedirs(args.data_dir, exist_ok=True)
    dataset_path = os.path.join(args.data_dir, "trades.json")
    stats_path = os.path.join(args.data_dir, "stats.json")

    dataset: list[dict] = []
    if os.path.exists(dataset_path):
        try:
            with open(dataset_path, "r", encoding="utf-8") as f:
                dataset = json.load(f)
            log(f"Loaded existing dataset: {len(dataset)} trades")
        except (json.JSONDecodeError, OSError) as e:
            log(f"! could not load existing dataset ({e}); starting fresh")
            dataset = []

    stats = {
        "runs": [],
        "last_updated": None,
    }
    if os.path.exists(stats_path):
        try:
            with open(stats_path, "r", encoding="utf-8") as f:
                stats = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    done_days = set(stats.get("days_collected", {}).keys())

    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end \
        else date.today() - timedelta(days=1)
    if args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
    else:
        start = end - timedelta(days=args.days - 1)
    log(f"Window: {start.isoformat()} .. {end.isoformat()}")

    new_rows = []
    day = start
    while day <= end:
        if day.isoformat() in done_days and not args.force:
            log(f"-- {day.isoformat()} already collected, skipping")
        else:
            new_rows.extend(collect_day(day))
        day += timedelta(days=1)

    merged = merge_dataset(dataset, new_rows)

    # Amendment de-dup may drop previously stored rows; stats reflect the run.
    run_info = {
        "window": [start.isoformat(), end.isoformat()],
        "new_filings_trades": len(new_rows),
        "dataset_size_after": len(merged),
        "requests": STATS["requests"],
        "errors": STATS["errors"][-50:],
        "days": STATS["days"],
        "master_sample": STATS.get("master_sample"),
        "at": datetime.utcnow().isoformat() + "Z",
    }
    stats.setdefault("runs", []).append(run_info)
    stats["runs"] = stats["runs"][-60:]
    stats["last_updated"] = run_info["at"]
    stats.setdefault("days_collected", {})
    for d, info in STATS["days"].items():
        stats["days_collected"][d] = info

    with open(dataset_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, separators=(",", ":"))
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)

    log(f"Dataset: {len(merged)} trades -> {dataset_path}")
    if new_rows:
        log(f"Run added {len(new_rows)} trades from the window.")
    else:
        log("Run added 0 trades.")
    # Fail loudly if the whole window produced nothing and there were errors,
    # or produced nothing with no errors at all (likely a systemic problem).
    if not new_rows and not merged:
        log("FATAL: empty dataset and 0 new trades — check SEC access.")
        sys.exit(2)


if __name__ == "__main__":
    main()
