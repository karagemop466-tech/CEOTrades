#!/usr/bin/env python3
"""
CEOTrades bulk backfill — downloads the SEC "Insider Transactions Data Sets"
(quarterly ZIPs, January 2006 -> current quarter) and converts them into the
canonical CEOTrades trade store.

Source (official, verified):
  https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets

  Quarterly ZIP naming (two hosting paths are in use by the SEC):
    https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{YYYY}q{Q}_form345.zip
    https://www.sec.gov/files/datastandardsinnovation/data/insider-transactions-data-sets/{YYYY}q{Q}_form345.zip

  Each ZIP contains tab-delimited UTF-8 files. We consume three of them:
    SUBMISSION.tsv     one row per filing   (key ACCESSION_NUMBER)
    REPORTINGOWNER.tsv one row per owner    (key ACCESSION_NUMBER + RPTOWNERCIK)
    NONDERIV_TRANS.tsv Table I transactions (key ACCESSION_NUMBER + NONDERIV_TRANS_SK)
    DERIV_TRANS.tsv    Table II transactions(key ACCESSION_NUMBER + DERIV_TRANS_SK)

  Column names below are taken verbatim from the SEC readme
  (https://www.sec.gov/files/insider_transactions_readme.pdf, sections 5.1-5.6).
  Nothing is inferred: any column we reference is looked up by name in the
  file's own header row, and a missing column yields an empty value rather
  than a positional guess.

Output: collector/data/trades-YYYY.csv.gz — one gzipped CSV per calendar year
of FILING date, using the canonical CEOTrades column set (see FIELDS).

Standard library only.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

UA = os.environ.get(
    "SEC_UA",
    "CEOTrades Insider-Trade Research https://github.com/karagemop466-tech/CEOTrades "
    "karagemop466-tech@users.noreply.github.com",
)

ZIP_HOSTS = (
    "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets",
    "https://www.sec.gov/files/datastandardsinnovation/data/insider-transactions-data-sets",
)

FIRST_YEAR = 2006  # SEC scope: "XML data submitted from January 2006 through current period"

# Official Form 3/4/5 transaction codes (readme appendix 6.2).
CODE_TEXT = {
    "P": "Open market or private purchase of securities",
    "S": "Open market or private sale of securities",
    "A": "Grant, award or other acquisition pursuant to Rule 16b-3(d)",
    "D": "Disposition to the issuer pursuant to Rule 16b-3(e)",
    "F": "Payment of exercise price or tax liability by delivering or withholding securities",
    "I": "Discretionary transaction under Rule 16b-3(f)",
    "M": "Exercise or conversion of derivative security exempt under Rule 16b-3",
    "C": "Conversion of derivative security",
    "E": "Expiration of short derivative position",
    "H": "Expiration or cancellation of long derivative position with value received",
    "O": "Exercise of out-of-the-money derivative security",
    "X": "Exercise of in-the-money or at-the-money derivative security",
    "G": "Bona fide gift",
    "L": "Small acquisition under Rule 16a-6",
    "W": "Acquisition or disposition by will or the laws of descent and distribution",
    "Z": "Deposit into or withdrawal from voting trust",
    "J": "Other acquisition or disposition",
    "K": "Transaction in equity swap or instrument with similar characteristics",
    "U": "Disposition pursuant to a tender of shares in a change of control transaction",
}

SIDE = {
    "P": "buy", "S": "sell",
    "M": "exercise", "C": "exercise", "X": "exercise", "O": "exercise",
    "A": "grant",
    "F": "withholding", "D": "to_issuer",
    "E": "expiration", "H": "expiration",
    "G": "gift",
    "J": "other", "K": "other", "L": "other", "U": "other", "V": "other",
    "W": "other", "Z": "other", "I": "other",
}

# Canonical CEOTrades columns for the on-disk store.
FIELDS = [
    "fd",        # filing date       YYYY-MM-DD  (SUBMISSION.FILING_DATE)
    "td",        # transaction date  YYYY-MM-DD  (*_TRANS.TRANS_DATE)
    "period",    # period of report              (SUBMISSION.PERIOD_OF_REPORT)
    "form",      # 3 | 4 | 5                     (SUBMISSION.DOCUMENT_TYPE root)
    "amend",     # 1 when the document type ends in /A
    "acc",       # accession number
    "co",        # issuer name
    "tk",        # issuer trading symbol
    "icik",      # issuer CIK
    "in",        # reporting owner name (first, "+n joint" when multiple)
    "pcik",      # reporting owner CIK
    "own_n",     # number of reporting owners on the filing
    "rel",       # Director / Officer / 10% Owner / Other
    "title",     # officer title
    "code",      # transaction code
    "ct",        # transaction code description
    "side",      # buy / sell / grant / ...
    "sec",       # security title
    "sh",        # transaction shares
    "px",        # transaction price per share
    "val",       # sh * px when both present
    "ad",        # A (acquired) / D (disposed)
    "af",        # shares owned following the transaction
    "di",        # D (direct) / I (indirect)
    "nature",    # nature of indirect ownership
    "der",       # 1 for Table II (derivative) rows
    "under",     # underlying security title (derivatives)
    "under_sh",  # underlying shares (derivatives)
    "xp",        # conversion or exercise price (derivatives)
    "exp",       # expiration date (derivatives)
    "timely",    # E early / L late / "" on time
    "swap",      # equity swap involved flag
]


def log(msg: str):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Throttle:
    def __init__(self, rate_per_sec: float):
        self.min_interval = 1.0 / rate_per_sec
        self._next = 0.0

    def wait(self):
        now = time.monotonic()
        d = self._next - now
        if d > 0:
            time.sleep(d)
        self._next = max(now, self._next) + self.min_interval


THROTTLE = Throttle(5.0)


def http_get(url: str, retries: int = 4, timeout: int = 300) -> bytes | None:
    """GET honoring SEC fair-access. Returns bytes, or None on 403/404/exhaustion."""
    for attempt in range(retries + 1):
        THROTTLE.wait()
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept-Encoding": "gzip, deflate",
            "Host": "www.sec.gov",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return None
            last = f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001 - network variability
            last = str(e)
        if attempt < retries:
            time.sleep(2 ** attempt)
    log(f"   ! giving up on {url} ({last})")
    return None


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}


def parse_date(s: str) -> str:
    """SEC dates are 'DD-MON-YYYY'. Some vintages already use ISO. Returns '' if unparseable."""
    s = (s or "").strip()
    if not s:
        return ""
    m = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$", s)
    if m:
        mo = MONTHS.get(m.group(2).upper())
        if mo:
            return f"{int(m.group(3)):04d}-{mo:02d}-{int(m.group(1)):02d}"
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    # A few rows carry a timestamp suffix, e.g. "15-AUG-2025 00:00:00".
    m = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})\s", s)
    if m:
        mo = MONTHS.get(m.group(2).upper())
        if mo:
            return f"{int(m.group(3)):04d}-{mo:02d}-{int(m.group(1)):02d}"
    return ""


def fnum(v):
    """Float or None. Never raises; blank/garbage -> None."""
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s or s.upper() in ("NULL", "NONE", "NA", "N/A"):
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def rel_label(rel_raw: str, txt: str) -> str:
    """REPORTINGOWNER.RPTOWNER_RELATIONSHIP is a delimited list of
    OFFICER / DIRECTOR / TENPERCENTOWNER / OTHER."""
    u = (rel_raw or "").upper()
    out = []
    if "DIRECTOR" in u:
        out.append("Director")
    if "OFFICER" in u:
        out.append("Officer")
    if "TENPERCENT" in u or "10PERCENT" in u:
        out.append("10% Owner")
    if "OTHER" in u:
        out.append("Other")
    return "/".join(out) or ("Other" if txt else "Unknown")


def read_tsv(zf: zipfile.ZipFile, name: str):
    """Yield dict rows from a tab-delimited member, keyed by its own header row.

    Returns an empty iterator when the member is absent from this quarter's ZIP.
    """
    member = None
    for n in zf.namelist():
        if os.path.basename(n).upper() == name.upper():
            member = n
            break
    if member is None:
        log(f"   ! {name} not present in archive")
        return
    with zf.open(member) as fh:
        text = io.TextIOWrapper(fh, encoding="utf-8", errors="replace", newline="")
        rdr = csv.DictReader(text, delimiter="\t")
        for row in rdr:
            yield row


def g(row: dict, key: str) -> str:
    v = row.get(key)
    return "" if v is None else str(v).strip()


# ---------------------------------------------------------------------------
# Quarter -> rows
# ---------------------------------------------------------------------------

def quarter_rows(raw: bytes, quarter: str) -> list[dict]:
    """Join SUBMISSION + REPORTINGOWNER + NONDERIV_TRANS + DERIV_TRANS."""
    zf = zipfile.ZipFile(io.BytesIO(raw))

    subs: dict[str, dict] = {}
    for r in read_tsv(zf, "SUBMISSION.tsv"):
        acc = g(r, "ACCESSION_NUMBER")
        if not acc:
            continue
        doc = g(r, "DOCUMENT_TYPE").upper()
        subs[acc] = {
            "fd": parse_date(g(r, "FILING_DATE")),
            "period": parse_date(g(r, "PERIOD_OF_REPORT")),
            "form": doc.replace("/A", "").strip() or "4",
            "amend": 1 if doc.endswith("/A") else 0,
            "icik": g(r, "ISSUERCIK").lstrip("0") or g(r, "ISSUERCIK"),
            "co": g(r, "ISSUERNAME"),
            "tk": g(r, "ISSUERTRADINGSYMBOL").upper(),
        }

    owners: dict[str, list[dict]] = {}
    for r in read_tsv(zf, "REPORTINGOWNER.tsv"):
        acc = g(r, "ACCESSION_NUMBER")
        if not acc:
            continue
        owners.setdefault(acc, []).append({
            "cik": g(r, "RPTOWNERCIK").lstrip("0") or g(r, "RPTOWNERCIK"),
            "name": g(r, "RPTOWNERNAME"),
            "rel": g(r, "RPTOWNER_RELATIONSHIP"),
            "title": g(r, "RPTOWNER_TITLE"),
            "txt": g(r, "RPTOWNER_TXT"),
        })

    def owner_fields(acc: str) -> dict:
        os_ = owners.get(acc) or []
        if not os_:
            return {"in": "", "pcik": "", "own_n": 0, "rel": "Unknown", "title": ""}
        names = [o["name"] for o in os_ if o["name"]]
        name = names[0] if names else ""
        if len(names) > 1:
            name += f" (+{len(names) - 1} joint)"
        rels, title = [], ""
        for o in os_:
            lbl = rel_label(o["rel"], o["txt"])
            for part in lbl.split("/"):
                if part and part not in rels:
                    rels.append(part)
            if not title and o["title"]:
                title = o["title"]
        order = ["Director", "Officer", "10% Owner", "Other", "Unknown"]
        rels.sort(key=lambda x: order.index(x) if x in order else 99)
        return {"in": name, "pcik": os_[0]["cik"], "own_n": len(os_),
                "rel": "/".join(rels) or "Unknown", "title": title}

    rows: list[dict] = []
    missing_sub = 0

    def base(acc: str):
        nonlocal missing_sub
        s = subs.get(acc)
        if s is None:
            missing_sub += 1
            return None
        b = dict(s)
        b["acc"] = acc
        b.update(owner_fields(acc))
        return b

    # ---- Table I: non-derivative transactions ----
    for r in read_tsv(zf, "NONDERIV_TRANS.tsv"):
        acc = g(r, "ACCESSION_NUMBER")
        b = base(acc)
        if b is None:
            continue
        code = g(r, "TRANS_CODE").upper()[:1]
        sh = fnum(g(r, "TRANS_SHARES"))
        px = fnum(g(r, "TRANS_PRICEPERSHARE"))
        row = dict(b)
        row.update({
            "td": parse_date(g(r, "TRANS_DATE")) or parse_date(g(r, "DEEMED_EXECUTION_DATE")),
            "code": code,
            "ct": CODE_TEXT.get(code, f"Unknown code ({code})" if code else "Not reported"),
            "side": SIDE.get(code, "other"),
            "sec": g(r, "SECURITY_TITLE"),
            "sh": sh,
            "px": px,
            "val": round(sh * px, 2) if (sh is not None and px is not None) else None,
            "ad": g(r, "TRANS_ACQUIRED_DISP_CD").upper()[:1],
            "af": fnum(g(r, "SHRS_OWND_FOLWNG_TRANS")),
            "di": g(r, "DIRECT_INDIRECT_OWNERSHIP").upper()[:1],
            "nature": g(r, "NATURE_OF_OWNERSHIP"),
            "der": 0,
            "under": "", "under_sh": None, "xp": None, "exp": "",
            "timely": g(r, "TRANS_TIMELINESS").upper()[:1],
            "swap": g(r, "EQUITY_SWAP_INVOLVED").upper()[:1],
        })
        rows.append(row)

    # ---- Table II: derivative transactions ----
    for r in read_tsv(zf, "DERIV_TRANS.tsv"):
        acc = g(r, "ACCESSION_NUMBER")
        b = base(acc)
        if b is None:
            continue
        code = g(r, "TRANS_CODE").upper()[:1]
        sh = fnum(g(r, "TRANS_SHARES"))
        px = fnum(g(r, "TRANS_PRICEPERSHARE"))
        total = fnum(g(r, "TRANS_TOTAL_VALUE"))
        row = dict(b)
        row.update({
            "td": parse_date(g(r, "TRANS_DATE")) or parse_date(g(r, "DEEMED_EXECUTION_DATE")),
            "code": code,
            "ct": CODE_TEXT.get(code, f"Unknown code ({code})" if code else "Not reported"),
            "side": SIDE.get(code, "other"),
            "sec": g(r, "SECURITY_TITLE"),
            "sh": sh,
            "px": px,
            "val": (round(sh * px, 2) if (sh is not None and px is not None) else total),
            "ad": g(r, "TRANS_ACQUIRED_DISP_CD").upper()[:1],
            "af": fnum(g(r, "SHRS_OWND_FOLWNG_TRANS")),
            "di": g(r, "DIRECT_INDIRECT_OWNERSHIP").upper()[:1],
            "nature": g(r, "NATURE_OF_OWNERSHIP"),
            "der": 1,
            # The readme spells the underlying columns UNDLYING_* in DERIV_TRANS
            # and UNDLYNG_* in DERIV_HOLDING; accept either so no data is lost.
            "under": g(r, "UNDLYING_SEC_TITLE") or g(r, "UNDLYNG_SEC_TITLE"),
            "under_sh": fnum(g(r, "UNDLYING_SEC_SHARES") or g(r, "UNDLYNG_SEC_SHARES")),
            "xp": fnum(g(r, "CONV_EXERCISE_PRICE")),
            "exp": parse_date(g(r, "EXPIRATION_DATE")),
            "timely": g(r, "TRANS_TIMELINESS").upper()[:1],
            "swap": g(r, "EQUITY_SWAP_INVOLVED").upper()[:1],
        })
        rows.append(row)

    if missing_sub:
        log(f"   note: {missing_sub} transaction rows had no SUBMISSION parent in {quarter}")
    return rows


# ---------------------------------------------------------------------------
# Year shard store
# ---------------------------------------------------------------------------

SHARD_RE = re.compile(r"^trades-(\d{4})\.csv\.gz$")


def shard_path(year: str) -> str:
    return os.path.join(DATA, f"trades-{year}.csv.gz")


def load_shard(year: str) -> dict[str, dict]:
    """Existing rows for a year, keyed by a stable row identity."""
    path = shard_path(year)
    out: dict[str, dict] = {}
    if not os.path.exists(path):
        return out
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            out[row_key(r)] = r
    return out


def _kv(v) -> str:
    """Render a value exactly the way csv.DictWriter will, so that a row keyed
    in memory and the same row keyed after a gzip/CSV round-trip collide.
    (`or ""` must NOT be used here: it would erase legitimate 0 values.)"""
    return "" if v is None else str(v)


def row_key(r: dict) -> str:
    return "|".join(_kv(r.get(k)) for k in
                    ("acc", "der", "td", "code", "sec", "sh", "px", "ad", "pcik"))


def write_shard(year: str, rows: list[dict]):
    rows.sort(key=lambda r: (str(r.get("fd") or ""), str(r.get("td") or ""),
                             str(r.get("acc") or ""), str(r.get("der") or ""),
                             str(r.get("sec") or ""), str(r.get("sh") or "")))
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as gz:
        text = io.TextIOWrapper(gz, encoding="utf-8", newline="")
        w = csv.DictWriter(text, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
        text.flush()
        text.detach()
    with open(shard_path(year), "wb") as f:
        f.write(buf.getvalue())


def merge_into_store(new_rows: list[dict]) -> dict:
    """Merge rows into per-year shards. Idempotent: re-running changes nothing."""
    by_year: dict[str, list[dict]] = {}
    undated = 0
    for r in new_rows:
        fd = str(r.get("fd") or "")
        if len(fd) < 4 or not fd[:4].isdigit():
            undated += 1
            continue
        by_year.setdefault(fd[:4], []).append(r)

    added = 0
    for year, rows in sorted(by_year.items()):
        existing = load_shard(year)
        before = len(existing)
        for r in rows:
            existing[row_key(r)] = r
        write_shard(year, list(existing.values()))
        added += len(existing) - before
        log(f"   shard {year}: {before} -> {len(existing)} rows")
    return {"added": added, "undated": undated}


def quarters(start_year: int, end: date):
    for y in range(start_year, end.year + 1):
        for q in range(1, 5):
            if y == end.year and (q - 1) * 3 + 1 > end.month:
                break
            yield y, q


def fetch_quarter(y: int, q: int) -> bytes | None:
    name = f"{y}q{q}_form345.zip"
    for host in ZIP_HOSTS:
        raw = http_get(f"{host}/{name}")
        if raw and raw[:2] == b"PK":
            return raw
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill all SEC insider transactions.")
    ap.add_argument("--from-year", type=int, default=FIRST_YEAR)
    ap.add_argument("--to-year", type=int, default=date.today().year)
    ap.add_argument("--only", default="", help="single quarter, e.g. 2025q3")
    args = ap.parse_args()

    os.makedirs(DATA, exist_ok=True)
    today = date.today()

    if args.only:
        m = re.match(r"^(\d{4})q([1-4])$", args.only.lower())
        if not m:
            log(f"bad --only value: {args.only}")
            return 2
        qs = [(int(m.group(1)), int(m.group(2)))]
    else:
        qs = [(y, q) for y, q in quarters(max(FIRST_YEAR, args.from_year), today)
              if y <= args.to_year]

    log(f"CEOTrades bulk backfill: {len(qs)} quarters "
        f"({qs[0][0]}Q{qs[0][1]} .. {qs[-1][0]}Q{qs[-1][1]})")

    total, missing = 0, []
    for y, q in qs:
        label = f"{y}Q{q}"
        log(f"== {label}")
        raw = fetch_quarter(y, q)
        if not raw:
            log("   ! archive unavailable (not yet published or withdrawn)")
            missing.append(label)
            continue
        log(f"   downloaded {len(raw) / 1e6:.1f} MB")
        try:
            rows = quarter_rows(raw, label)
        except (zipfile.BadZipFile, csv.Error) as e:
            log(f"   ! parse failure: {e}")
            missing.append(label)
            continue
        log(f"   parsed {len(rows)} transaction rows")
        info = merge_into_store(rows)
        log(f"   merged: +{info['added']} new"
            + (f", {info['undated']} skipped (no filing date)" if info["undated"] else ""))
        total += len(rows)

    log(f"\nDone. {total} rows processed across {len(qs) - len(missing)} quarters.")
    if missing:
        log(f"Quarters unavailable: {', '.join(missing)}")
    sizes = sorted(fn for fn in os.listdir(DATA) if SHARD_RE.match(fn))
    for fn in sizes:
        log(f"  {fn}  {os.path.getsize(os.path.join(DATA, fn)) / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
