#!/usr/bin/env python3
"""
CEOTrades collector — pulls ALL insider trades (Form 4/4A and Form 5/5A) filed
on U.S. publicly traded companies from SEC EDGAR, with no manual input.

Data sources (official SEC endpoints, all verified live):

  1. Daily master index (enumeration) — one file per business day:
       https://www.sec.gov/Archives/edgar/daily-index/{YYYY}/QTR{q}/master.{YYYYMMDD}.idx
     Pipe-delimited lines:
       CIK|Company Name|Form Type|Date Filed|File Name
     Each filing appears once per associated entity (issuer AND each reporting
     owner), so rows must be de-duplicated by accession number.
     The file exists only for business days (404 on weekends/holidays).

  2. Full filing submission (extraction):
       https://www.sec.gov/Archives/{File Name}   e.g.
       https://www.sec.gov/Archives/edgar/data/1001250/0001189770-26-000008.txt
     The submission text contains the structured ownership XML inside an
     <XML>...</XML> block (root element <ownershipDocument>).

Rules (SEC "Fair Access" policy):
  - Declared User-Agent identifying the caller (required).
  - Max 10 requests/second; we throttle to 8/s by default.
  - Daily indexes are built nightly ~10 PM ET; filings accepted after the
    cutoff appear the next business day — the 3-day rolling window in the
    scheduled job re-checks recent days automatically.

The dataset is incremental: each run merges new filings into
collector/data/trades.json (keyed by accession number); amendments (4/A, 5/A)
supersede the original filing for the same issuer/insider/period.

Standard library only — no third-party dependencies.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import runlog

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

UA = os.environ.get(
    "SEC_UA",
    "CEOTrades Insider-Trade Collector https://github.com/karagemop466-tech/CEOTrades "
    "karagemop466-tech@users.noreply.github.com",
)
ARCHIVES = "https://www.sec.gov/Archives"
DAILY_INDEX = ARCHIVES + "/edgar/daily-index"

# Form types to collect (as they appear in the master index).
FORM_TYPES = {"4": ("4", 0), "4/A": ("4", 1), "5": ("5", 0), "5/A": ("5", 1)}

# Official Form 4/5 transaction code table (General Instructions, Table I/II).
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
# Distinguishes a genuine 404 (weekend/holiday/not-yet-published index) from
# transport/server failure. Treating every failed request as a holiday would
# permanently create silent holes in the historical archive.
LAST_HTTP_FAILED = False


def http_get(url: str, retries: int = 3, timeout: int = 30):
    """GET a URL honoring SEC fair-access rules. Returns bytes or None.

    None is returned for 404 (resource missing) or after exhausted retries.
    """
    global LAST_HTTP_FAILED
    LAST_HTTP_FAILED = False
    last_err = None
    for attempt in range(retries + 1):
        THROTTLE.wait()
        STATS["requests"] += 1
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept-Encoding": "gzip, deflate",
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
        # Backoff: 6s, 18s, 54s with ±30% jitter (SEC may temporarily
        # rate-limit or block a source IP; jitter avoids lockstep re-hits).
        if attempt < retries:
            delay = 6 * (3 ** attempt)
            time.sleep(delay * (0.7 + 0.6 * random.random()))
    LAST_HTTP_FAILED = True
    log(f"  ! giving up: {last_err}")
    STATS["errors"].append(last_err)
    return None


def log(msg: str):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# XML parsing (ownership documents: Form 4 / Form 5)
# Element paths verified against live EDGAR filings (schema X0306..X0609).
# ---------------------------------------------------------------------------

def _strip_ns(root: ET.Element) -> ET.Element:
    """Remove XML namespaces in-place (rarely present, but be safe)."""
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _txt(el, path: str) -> str:
    if el is None:
        return ""
    node = el.find(path)
    if node is None or node.text is None:
        return ""
    return (node.text or "").strip()


def _num(el, path: str):
    s = _txt(el, path)
    if s == "":
        return None
    s = s.replace(",", "").replace("$", "").strip()
    try:
        f = float(s)
        return int(f) if f.is_integer() else f
    except ValueError:
        return None


def _flag(el, path: str) -> bool:
    return _txt(el, path) in ("1", "true")


def _rels(rel_el) -> tuple[list[str], str]:
    rels = []
    if _flag(rel_el, "isDirector"):
        rels.append("Director")
    if _flag(rel_el, "isOfficer"):
        rels.append("Officer")
    if _flag(rel_el, "isTenPercentOwner"):
        rels.append("10% Owner")
    if _flag(rel_el, "isOther"):
        rels.append("Other")
    title = _txt(rel_el, "officerTitle")
    return rels, title


def _norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _parse_tx(tx: ET.Element, derivative: bool) -> dict:
    """Parse one nonDerivativeTransaction / derivativeTransaction element.

    Real schema (verified):
      securityTitle/value, transactionDate/value,
      transactionCoding/transactionCode, transactionCoding/equitySwapInvolved,
      transactionAmounts/transactionShares/value,
      transactionAmounts/transactionPricePerShare/value,
      transactionAmounts/transactionAcquiredDisposedCode/value,
      postTransactionAmounts/sharesOwnedFollowingTransaction/value,
      ownershipNature/directOrIndirectOwnership/value
    Derivative extras:
      conversionOrExercisePrice/value, expirationDate/value,
      underlyingSecurity/underlyingSecurityTitle/value
    """
    t = {
        "sec": _norm_name(_txt(tx, "securityTitle/value")),
        "td": _txt(tx, "transactionDate/value"),
        "code": _txt(tx, "transactionCoding/transactionCode"),
        "swap": _txt(tx, "transactionCoding/equitySwapInvolved") in ("1", "true"),
        "sh": _num(tx, "transactionAmounts/transactionShares/value"),
        "px": _num(tx, "transactionAmounts/transactionPricePerShare/value"),
        "ad": _txt(tx, "transactionAmounts/transactionAcquiredDisposedCode/value"),
        "af": _num(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"),
        "di": _txt(tx, "ownershipNature/directOrIndirectOwnership/value"),
        "der": 1 if derivative else 0,
        "under": "",
        "exp": "",
    }
    if derivative:
        t["under"] = _norm_name(_txt(tx, "underlyingSecurity/underlyingSecurityTitle/value"))
        t["exp"] = _txt(tx, "expirationDate/value")
        # For M/C exercises the transaction price is usually footnoted; the
        # conversion/exercise price is reported separately. Keep px as the
        # actual transaction price; fall back to exercise price only for
        # valuation when px is absent is intentionally NOT done (no guessing).
        t["xp"] = _num(tx, "conversionOrExercisePrice/value")
    return t


def _value(t: dict):
    if t.get("sh") is None or t.get("px") is None:
        return None
    try:
        v = float(t["sh"]) * float(t["px"])
        return int(v) if v == int(v) else round(v, 2)
    except (TypeError, ValueError):
        return None


def parse_ownership(xml_bytes: bytes) -> dict | None:
    """Parse a Form 4/5 ownership XML document. None if not parseable."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    root = _strip_ns(root)
    if root.tag != "ownershipDocument":
        return None

    owners = []
    for ro in root.findall("reportingOwner"):
        rels, title = _rels(ro.find("reportingOwnerRelationship"))
        owners.append({
            "cik": _txt(ro, "reportingOwnerId/rptOwnerCik").lstrip("0") or "0",
            "name": _norm_name(_txt(ro, "reportingOwnerId/rptOwnerName")),
            "rels": rels,
            "title": _norm_name(title),
        })

    out = {
        "schema": _txt(root, "schemaVersion"),
        "docType": _txt(root, "documentType"),
        "period": _txt(root, "periodOfReport"),
        "icik": _txt(root, "issuer/issuerCik").lstrip("0") or "0",
        "iname": _norm_name(_txt(root, "issuer/issuerName")),
        "iticker": _txt(root, "issuer/issuerTradingSymbol"),
        "owners": owners,
        "trades": [],
    }

    for tx in root.iter("nonDerivativeTransaction"):
        t = _parse_tx(tx, derivative=False)
        t["val"] = _value(t)
        out["trades"].append(t)
    for tx in root.iter("derivativeTransaction"):
        t = _parse_tx(tx, derivative=True)
        t["val"] = _value(t)
        out["trades"].append(t)
    return out


def extract_ownership_xml(submission: bytes) -> bytes | None:
    """Extract the ownership XML block from a full-submission .txt file.

    Submissions wrap each document in <DOCUMENT>..<TEXT>..<XML>..</XML>.
    We take the first <XML> block whose content contains <ownershipDocument.
    """
    text = submission.decode("utf-8", "replace")
    pos = 0
    while True:
        start = text.find("<XML>", pos)
        if start == -1:
            return None
        end = text.find("</XML>", start)
        if end == -1:
            return None
        block = text[start + 5:end].strip()
        if "<ownershipDocument" in block:
            # Strip anything before the XML declaration / root element.
            i = block.find("<?xml")
            if i == -1:
                i = block.find("<ownershipDocument")
            return block[i:].encode("utf-8")
        pos = end + 6
    return None


# ---------------------------------------------------------------------------
# Enumeration: daily master index
# ---------------------------------------------------------------------------

ACC_RE = re.compile(r"(\d{10}-\d{2}-\d{6})\.txt$")


def quarter(d: date) -> int:
    return (d.month - 1) // 3 + 1


def parse_master_idx(raw: bytes) -> list[dict]:
    """Parse a master.YYYYMMDD.idx file into Form 4/5 filing records.

    Line format (verified): CIK|Company Name|Form Type|Date Filed|File Name
    The same accession appears once per associated entity — dedupe by acc.
    """
    records, seen = [], set()
    for line in raw.decode("latin-1").splitlines():
        parts = line.split("|")
        if len(parts) != 5:
            continue
        cik, _name, form, filed, path = (p.strip() for p in parts)
        if form not in FORM_TYPES or not path.endswith(".txt"):
            continue
        m = ACC_RE.search(path)
        if not m:
            continue
        acc = m.group(1)
        if acc in seen:
            continue
        seen.add(acc)
        root_form, amend = FORM_TYPES[form]
        fd = f"{filed[0:4]}-{filed[4:6]}-{filed[6:8]}" if len(filed) == 8 and filed.isdigit() else filed
        records.append({"acc": acc, "path": path, "form": root_form,
                        "amend": amend, "fd": fd})
    return records


def parse_efts_hits(raw: bytes) -> list[dict]:
    """Parse an EFTS search-index JSON page into the same rec shape as master.idx.

    Hit shape (verified against efts.sec.gov/LATEST/search-index):
      _id typically \"{accession}:{form}\"
      _source.adsh | file_date | form_type | ciks[]
    Filing text path: edgar/data/{cik}/{adsh_nodash}/{adsh}.txt
    """
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    hits = ((doc or {}).get("hits") or {}).get("hits") or []
    records, seen = [], set()
    for h in hits:
        src = h.get("_source") or {}
        acc = (src.get("adsh") or "").strip()
        if not acc:
            hid = str(h.get("_id") or "")
            acc = hid.split(":")[0].strip()
        if not ACC_RE.search(acc + ".txt"):
            continue
        if acc in seen:
            continue
        form = (src.get("form_type") or src.get("file_type") or "").strip()
        if form not in FORM_TYPES:
            continue
        seen.add(acc)
        root_form, amend = FORM_TYPES[form]
        filed = (src.get("file_date") or "").strip()
        if len(filed) == 8 and filed.isdigit():
            fd = f"{filed[0:4]}-{filed[4:6]}-{filed[6:8]}"
        else:
            fd = filed[:10]
        ciks = src.get("ciks") or []
        cik = str(ciks[0]).lstrip("0") or "0" if ciks else acc.split("-")[0].lstrip("0")
        nodash = acc.replace("-", "")
        path = f"edgar/data/{cik}/{nodash}/{acc}.txt"
        records.append({"acc": acc, "path": path, "form": root_form,
                        "amend": amend, "fd": fd})
    return records


def enum_day_efts(day: date) -> tuple[list[dict] | None, str]:
    """Fallback enumeration via the SEC full-text search API (EFTS)."""
    ds = day.isoformat()
    records, seen = [], set()
    from_ = 0
    page_size = 100
    while True:
        url = (
            "https://efts.sec.gov/LATEST/search-index"
            f"?dateRange=custom&startdt={ds}&enddt={ds}"
            f"&forms=4%2C4%2FA%2C5%2C5%2FA&from={from_}&size={page_size}"
        )
        raw = http_get(url)
        if raw is None:
            if not records:
                return None, "EFTS search unavailable"
            break
        page = parse_efts_hits(raw)
        n_new = 0
        for rec in page:
            if rec["acc"] in seen:
                continue
            seen.add(rec["acc"])
            records.append(rec)
            n_new += 1
        if n_new < page_size:
            break
        from_ += page_size
        if from_ > 5000:
            break
    return records, f"EFTS: {len(records)} Form 4/5 filings"


def enum_day(day: date) -> tuple[list[dict] | None, str]:
    """List Form 4/5 filings for `day` via the daily master index.

    Returns (records, note); records is None when the index file does not
    exist (weekend/holiday/not yet published) and [] only on parse trouble.
    Falls back to EFTS when the master index cannot be fetched.
    """
    url = (f"{DAILY_INDEX}/{day.year:04d}/QTR{quarter(day)}/"
           f"master.{day.strftime('%Y%m%d')}.idx")
    raw = http_get(url)
    if raw is None:
        master_failed = LAST_HTTP_FAILED
        recs, note = enum_day_efts(day)
        efts_failed = LAST_HTTP_FAILED
        if recs is None:
            if master_failed or efts_failed:
                return None, "SOURCE UNAVAILABLE — retry required; " + note
            return None, "no index published (weekend/holiday or not yet built); " + note
        return recs, "master.idx missing; " + note
    records = parse_master_idx(raw)
    return records, f"master.idx: {len(records)} Form 4/5 filings"


# ---------------------------------------------------------------------------
# Row production
# ---------------------------------------------------------------------------

def rows_from_filing(rec: dict) -> list[dict]:
    url = f"{ARCHIVES}/{rec['path']}"
    raw = http_get(url)
    if raw is None:
        STATS["errors"].append(f"submission 404: {url}")
        return []
    xml = extract_ownership_xml(raw)
    if xml is None:
        STATS["errors"].append(f"no ownership XML in: {url}")
        return []
    parsed = parse_ownership(xml)
    if parsed is None:
        STATS["errors"].append(f"unparseable XML: {url}")
        return []

    owners = parsed["owners"] or [{"cik": "", "name": "", "rels": [], "title": ""}]
    first = owners[0]
    names = [o["name"] for o in owners if o["name"]]
    in_name = names[0] if names else ""
    if len(names) > 1:
        in_name += f" (+{len(names) - 1} joint)"
    all_rels = sorted({r for o in owners for r in o["rels"]},
                      key=["Director", "Officer", "10% Owner", "Other"].index)
    rels = "/".join(all_rels) or "Unknown"
    title = next((o["title"] for o in owners if o["title"]), "")

    rows = []
    for t in parsed["trades"]:
        rows.append({
            "acc": rec["acc"],
            "form": rec["form"],          # root form: "4" or "5"
            "amend": rec["amend"],        # 1 when 4/A or 5/A
            "fd": rec["fd"],              # filing date (from daily index)
            "td": t["td"],                # transaction date
            "period": parsed["period"],
            "icik": parsed["icik"],
            "co": parsed["iname"],
            "tk": (parsed["iticker"] or "").strip().upper(),
            "pcik": first["cik"],
            "in": in_name,
            "own_n": len(owners),
            "rel": rels,
            "title": title,
            "code": t["code"],
            "ct": CODE_TEXT.get(t["code"], f"Unknown code ({t['code']})"),
            "side": code_side(t["code"]),
            "sec": t["sec"],
            "sh": t["sh"],
            "px": t["px"],
            "val": t["val"],
            "ad": t["ad"],
            "af": t["af"],
            "di": t["di"],
            "der": t["der"],
            "under": t["under"],
            "exp": t["exp"],
            "xp": t.get("xp"),          # conversion/exercise price (derivatives)
        })
    return rows


def collect_day(day: date) -> tuple[list[dict], bool]:
    """Collect one day. Returns (rows, index_found)."""
    log(f"== {day.isoformat()}")
    records, note = enum_day(day)
    log(f"   enumeration: {note}")
    if records is None:
        STATS["days"][day.isoformat()] = {
            "note": note, "filings": 0, "trades": 0, "index": False,
            "retry": note.startswith("SOURCE UNAVAILABLE"),
        }
        return [], False
    rows = []
    for i, rec in enumerate(records, 1):
        if i % 200 == 0:
            log(f"   ... {i}/{len(records)} filings, {len(rows)} trades so far")
        rows.extend(rows_from_filing(rec))
    STATS["days"][day.isoformat()] = {"note": note, "filings": len(records),
                                      "trades": len(rows), "index": True}
    log(f"   -> {len(records)} filings, {len(rows)} trades")
    return rows, True


# ---------------------------------------------------------------------------
# Merge (incremental dataset + amendment de-duplication)
# ---------------------------------------------------------------------------

def merge_dataset(existing: list[dict], new_rows: list[dict]) -> list[dict]:
    """Merge new rows into the dataset.

    Key: accession number (one filing may contribute several trade rows).
    Amendment rule: for the same (issuer, insider, periodOfReport) the most
    recently filed document wins; amendments outrank originals on ties.
    """
    by_acc: dict[str, list[dict]] = {}
    for r in existing:
        by_acc.setdefault(r["acc"], []).append(r)
    for r in new_rows:
        # a re-collected filing fully replaces its previous rows
        if r["acc"] in by_acc and by_acc[r["acc"]] and \
                by_acc[r["acc"]][0].get("_new") is not True:
            by_acc[r["acc"]] = []
        r["_new"] = True
        by_acc.setdefault(r["acc"], []).append(r)

    groups: dict[tuple, list] = {}
    for acc, rs in by_acc.items():
        if not rs:
            continue
        r0 = rs[0]
        key = (r0["icik"], r0["pcik"], r0.get("period", ""))
        groups.setdefault(key, []).append(
            (r0["fd"], r0.get("amend", 0), acc, rs))

    merged = []
    for lst in groups.values():
        lst.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        merged.extend(lst[0][3])

    for r in merged:
        r.pop("_new", None)
    merged.sort(key=lambda r: (r["fd"], r["td"] or "", r["co"], r["in"]),
                reverse=True)
    return merged


# ---------------------------------------------------------------------------
# Dataset persistence (sharded by filing month, gzip-compressed)
#
# Month sharding keeps every committed file small forever AND minimizes git
# churn: a nightly run only rewrites the shards of the months it touched.
# ---------------------------------------------------------------------------

SHARD_RE = re.compile(r"^trades-(\d{4}-\d{2})\.json\.gz$")


def load_dataset(data_dir: str) -> list[dict]:
    rows: list[dict] = []
    for fn in sorted(os.listdir(data_dir)):
        if SHARD_RE.match(fn):
            try:
                with gzip.open(os.path.join(data_dir, fn), "rt", encoding="utf-8") as f:
                    rows.extend(json.load(f))
            except (json.JSONDecodeError, OSError) as e:
                log(f"! could not load shard {fn} ({e})")
    # Legacy single-file datasets (pre-sharding) are absorbed once.
    for legacy, opener in (("trades.json.gz", lambda p: gzip.open(p, "rt", encoding="utf-8")),
                           ("trades.json", lambda p: open(p, "r", encoding="utf-8"))):
        path = os.path.join(data_dir, legacy)
        if not rows and os.path.exists(path):
            try:
                with opener(path) as f:
                    rows = json.load(f)
                log(f"Loaded legacy dataset {legacy}: {len(rows)} trades")
            except (json.JSONDecodeError, OSError) as e:
                log(f"! could not load legacy dataset ({e})")
    return rows


def save_dataset(data_dir: str, rows: list[dict]):
    shards: dict[str, list[dict]] = {}
    for r in rows:
        month = (r.get("fd") or "0000-00")[:7]
        shards.setdefault(month, []).append(r)
    for month, rs in shards.items():
        # Deterministic order -> byte-identical gzip when content unchanged
        # (mtime=0), so untouched months never show up as git diffs.
        rs.sort(key=lambda r: (r["fd"], r["td"] or "", r["acc"], r.get("sec", ""),
                               str(r.get("sh"))))
        path = os.path.join(data_dir, f"trades-{month}.json.gz")
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as gz:
            gz.write(json.dumps(rs, ensure_ascii=False,
                                separators=(",", ":")).encode("utf-8"))
        with open(path, "wb") as f:
            f.write(buf.getvalue())
    # Remove shards for months that no longer have rows, and legacy files.
    for fn in os.listdir(data_dir):
        m = SHARD_RE.match(fn)
        if m and m.group(1) not in shards:
            os.remove(os.path.join(data_dir, fn))
    for legacy in ("trades.json.gz", "trades.json"):
        p = os.path.join(data_dir, legacy)
        if os.path.exists(p):
            os.remove(p)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    runlog.start("collect")
    ap = argparse.ArgumentParser(description="Collect SEC Form 4/5 insider trades")
    ap.add_argument("--days", type=int, default=3,
                    help="collect this many calendar days ending yesterday")
    ap.add_argument("--start", help="explicit start date YYYY-MM-DD (overrides --days)")
    ap.add_argument("--end", help="explicit end date YYYY-MM-DD (default yesterday)")
    ap.add_argument("--rate", type=float, default=8.0,
                    help="max requests per second (SEC limit: 10)")
    ap.add_argument("--force", action="store_true",
                    help="re-collect days already marked complete")
    ap.add_argument("--budget-min", type=float,
                    default=float(os.environ.get("CEOTRADES_BUDGET_MIN", "15")),
                    help="stop collecting new days after this many minutes; "
                         "remaining days are picked up by the next nightly run. "
                         "The nightly workflow gives this step a small slice; "
                         "history is backfilled efficiently by build_site.py "
                         "from the SEC quarterly archives instead.")
    ap.add_argument("--no-backfill", action="store_true",
                    help="disable automatic history backfill before the window")
    ap.add_argument("--data-dir", default=os.path.join(os.path.dirname(__file__), "data"))
    args = ap.parse_args()

    global THROTTLE
    THROTTLE = Throttle(min(args.rate, 10.0))

    os.makedirs(args.data_dir, exist_ok=True)
    stats_path = os.path.join(args.data_dir, "stats.json")

    # Dataset is sharded by filing year (trades-YYYY-MM.json.gz) so no single
    # committed file can grow past Git/Pages limits as history accumulates.
    dataset = load_dataset(args.data_dir)
    if dataset:
        log(f"Loaded existing dataset: {len(dataset)} trades")

    stats = {"runs": [], "last_updated": None}
    if os.path.exists(stats_path):
        try:
            with open(stats_path, "r", encoding="utf-8") as f:
                stats = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    done_days = set(stats.get("days_collected", {}).keys())

    today_utc = datetime.now(timezone.utc).date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end \
        else today_utc - timedelta(days=1)
    if args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
    else:
        start = end - timedelta(days=args.days - 1)
    log(f"Window: {start.isoformat()} .. {end.isoformat()}")

    # Build the day worklist: the requested window (newest first) plus an
    # automatic backfill that walks further into the past on every run —
    # so history grows nightly with zero manual input.
    worklist: list[date] = []
    day = end
    while day >= start:
        worklist.append(day)
        day -= timedelta(days=1)
    if not args.force and not args.no_backfill:
        day = start - timedelta(days=1)
        floor = date(2004, 1, 1)  # ownership XML era on EDGAR
        while day >= floor:
            worklist.append(day)
            day -= timedelta(days=1)

    t0 = time.monotonic()
    budget_s = args.budget_min * 60.0
    new_rows = []
    consecutive_source_failures = 0
    for day in worklist:
        if day.isoformat() in done_days and not args.force:
            continue
        if time.monotonic() - t0 > budget_s:
            log(f"Time budget reached ({args.budget_min:.0f} min); "
                f"stopping at {day.isoformat()} — next run continues backfill.")
            break
        rows, _found = collect_day(day)
        new_rows.extend(rows)
        info = STATS["days"].get(day.isoformat(), {})
        if info.get("retry"):
            consecutive_source_failures += 1
            # A network-wide SEC outage should not turn an 80-minute run into
            # dozens of identical retries. Leave every day unmarked so the
            # next scheduled run resumes safely.
            if consecutive_source_failures >= 2:
                log("SEC sources unavailable for 2 consecutive days; "
                    "stopping early without marking either day complete.")
                break
        else:
            consecutive_source_failures = 0

    merged = merge_dataset(dataset, new_rows)

    run_info = {
        "window": [start.isoformat(), end.isoformat()],
        "new_filings_trades": len(new_rows),
        "dataset_size_after": len(merged),
        "requests": STATS["requests"],
        "errors": STATS["errors"][-50:],
        "error_count": len(STATS["errors"]),
        "days": STATS["days"],
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    stats.setdefault("runs", []).append(run_info)
    stats["runs"] = stats["runs"][-60:]
    stats["last_updated"] = run_info["at"]
    stats.setdefault("days_collected", {})
    recent_cut = (today_utc - timedelta(days=5)).isoformat()
    for d, info in STATS["days"].items():
        # A processed index marks the day done. A 404 also marks the day done
        # when it is safely in the past (weekend/holiday — the index will
        # never appear); recent 404s stay retryable (index not yet built).
        if info.get("index") or (d < recent_cut and not info.get("retry")):
            stats["days_collected"][d] = info

    save_dataset(args.data_dir, merged)
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)

    log(f"Dataset: {len(merged)} trades -> {args.data_dir}/trades-YYYY-MM.json.gz")
    log(f"Run added {len(new_rows)} trade rows; "
        f"{STATS['requests']} HTTP requests; {len(STATS['errors'])} errors.")
    if not merged:
        log("WARNING: daily JSON dataset is empty after this run. "
            "Continuing so the target-year/bulk collector can run next; "
            "build_site.py and the audit will fail or flag incomplete output if official data remains unavailable.")


if __name__ == "__main__":
    main()
