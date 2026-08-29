#!/usr/bin/env python3
"""
CEOTrades data builder.

Streams the whole insider-transaction store (see store.py) and writes every
JSON/CSV artifact the static site consumes. Memory stays bounded: rows are
never all held at once, and per-ticker price bars are discarded as soon as
that ticker's paper positions are simulated.

Outputs (all under ./data):

  summary.json            global counts, flows, code mix, monthly series
  recent.json             the last RECENT_DAYS of filings (the "tape")
  companies.json          one aggregate row per issuer
  insiders.json           one aggregate row per reporting owner (top INSIDER_CAP)
  co/<bucket>.json        per-company recent transactions, bucketed by CIK
  csv/trades-YYYY.csv.gz  complete history, one gzipped CSV per year
  paper/summary.json      paper-book headline stats + findings
  paper/positions.json    browsable slice of the paper book
  paper/positions.csv.gz  every simulated position
  paper/equity.json       deployed-capital / value curve

Standard library only.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import os
import shutil
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_IN = os.path.join(HERE, "data")
SITE_DATA = os.path.join(ROOT, "data")

sys.path.insert(0, HERE)

import store  # noqa: E402
import audit as audit_mod  # noqa: E402
import irregularities as irregularities_mod  # noqa: E402

RECENT_DAYS = 120        # window for the front-page tape
RECENT_CAP = 6000        # hard cap on tape rows
COMPANY_CAP = 40         # recent transactions kept per company detail view
CO_BUCKETS = 64          # per-company files are bucketed to keep file count sane
INSIDER_CAP = 4000       # insiders published in the browsable table
PAPER_BROWSE_CAP = 8000  # positions published as JSON (full set is in the CSV)


def log(m):
    print(m, flush=True)


def r2(x):
    return None if x is None else round(x, 2)


def r4(x):
    return None if x is None else round(x, 4)


def bucket_of(cik: str) -> int:
    s = "".join(ch for ch in str(cik or "") if ch.isdigit())
    return (int(s) % CO_BUCKETS) if s else 0


# ---------------------------------------------------------------------------
# Pass 1 — stream every row, build aggregates, harvest paper-trade signals
# ---------------------------------------------------------------------------

def _new_co():
    return {"co": "", "tk": "", "cik": "", "n": 0, "buy_n": 0, "sell_n": 0,
            "buy_v": 0.0, "sell_v": 0.0, "val": 0.0, "insiders": set(),
            "first": "", "last": "", "recent": []}


def _new_ins():
    return {"in": "", "cik": "", "rel": "", "title": "", "n": 0, "buy_n": 0,
            "sell_n": 0, "buy_v": 0.0, "sell_v": 0.0, "cos": set(),
            "first": "", "last": ""}


def collect(data_dir: str, target_year: int | None = None):
    """Aggregate rows from the store.

    When target_year is supplied, only rows whose SEC filing date falls in that
    calendar year are published. Rows outside the target year are ignored rather
    than carried into the UI, which prevents a 2025 build from accidentally
    showing 2026 sample data.
    """
    companies: dict[str, dict] = {}
    insiders: dict[str, dict] = {}
    by_code = defaultdict(lambda: {"n": 0, "v": 0.0})
    by_rel = defaultdict(int)
    by_side = defaultdict(int)
    by_form = defaultdict(int)
    monthly = defaultdict(lambda: {"n": 0, "buy": 0.0, "sell": 0.0,
                                   "buy_n": 0, "sell_n": 0})
    yearly = defaultdict(lambda: {"n": 0, "buy_n": 0, "sell_n": 0,
                                  "buy": 0.0, "sell": 0.0})
    totals = {"n": 0, "buy": 0.0, "sell": 0.0, "val": 0.0,
              "with_ticker": 0, "with_price": 0, "deriv": 0}
    accs: set[str] = set()
    recent: list[dict] = []
    latest: list[dict] = []
    signals: dict[tuple, dict] = {}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%d")
    first_fd, last_fd = "9999-99-99", ""

    for r in store.iter_rows(data_dir):
        fd = r.get("fd") or ""
        if target_year and (len(fd) < 4 or fd[:4] != str(target_year)):
            continue
        totals["n"] += 1
        code = (r.get("code") or "?")
        side = r.get("side") or "other"
        v = r.get("val") or 0.0
        if v and v < 0:
            v = abs(v)
        tk = (r.get("tk") or "").strip().upper()
        cik = r.get("icik") or ""
        acc = r.get("acc") or ""
        if acc:
            accs.add(acc)

        if fd:
            if fd < first_fd:
                first_fd = fd
            if fd > last_fd:
                last_fd = fd
            m, y = fd[:7], fd[:4]
            monthly[m]["n"] += 1
            yearly[y]["n"] += 1

        by_code[code]["n"] += 1
        by_code[code]["v"] += v
        by_side[side] += 1
        by_form[r.get("form") or "?"] += 1
        if tk:
            totals["with_ticker"] += 1
        if r.get("px"):
            totals["with_price"] += 1
        if r.get("der"):
            totals["deriv"] += 1
        totals["val"] += v

        is_buy = code == "P"
        is_sell = code == "S"
        if is_buy:
            totals["buy"] += v
            if fd:
                monthly[fd[:7]]["buy"] += v
                monthly[fd[:7]]["buy_n"] += 1
                yearly[fd[:4]]["buy"] += v
                yearly[fd[:4]]["buy_n"] += 1
        elif is_sell:
            totals["sell"] += v
            if fd:
                monthly[fd[:7]]["sell"] += v
                monthly[fd[:7]]["sell_n"] += 1
                yearly[fd[:4]]["sell"] += v
                yearly[fd[:4]]["sell_n"] += 1

        for rel in (r.get("rel") or "").split("/"):
            rel = rel.strip()
            if rel:
                by_rel[rel] += 1

        # ---- per company ----
        ckey = cik or tk or (r.get("co") or "")
        if ckey:
            c = companies.get(ckey)
            if c is None:
                c = companies[ckey] = _new_co()
                c["cik"] = cik
            c["n"] += 1
            if r.get("co"):
                c["co"] = r["co"]
            if tk and not c["tk"]:
                c["tk"] = tk
            c["val"] += v
            if is_buy:
                c["buy_n"] += 1
                c["buy_v"] += v
            elif is_sell:
                c["sell_n"] += 1
                c["sell_v"] += v
            if r.get("in"):
                c["insiders"].add(r["in"])
            if fd:
                if not c["first"] or fd < c["first"]:
                    c["first"] = fd
                if fd > c["last"]:
                    c["last"] = fd
            # keep only the most recent COMPANY_CAP rows
            c["recent"].append(compact(r))
            if len(c["recent"]) > COMPANY_CAP * 4:
                c["recent"].sort(key=lambda x: x.get("fd") or "", reverse=True)
                del c["recent"][COMPANY_CAP:]

        # ---- per insider ----
        ikey = (r.get("pcik") or "") or (r.get("in") or "")
        if ikey and r.get("in"):
            p = insiders.get(ikey)
            if p is None:
                p = insiders[ikey] = _new_ins()
                p["cik"] = r.get("pcik") or ""
            p["in"] = r["in"]
            p["n"] += 1
            if r.get("rel") and r["rel"] != "Unknown":
                p["rel"] = r["rel"]
            if r.get("title") and not p["title"]:
                p["title"] = r["title"]
            if is_buy:
                p["buy_n"] += 1
                p["buy_v"] += v
            elif is_sell:
                p["sell_n"] += 1
                p["sell_v"] += v
            if r.get("co"):
                p["cos"].add(tk or r["co"])
            if fd:
                if not p["first"] or fd < p["first"]:
                    p["first"] = fd
                if fd > p["last"]:
                    p["last"] = fd

        # ---- tape ----
        # Rows inside the recent window, plus an always-populated fallback so
        # the tape is never empty for a dataset whose latest filing is old.
        if fd >= cutoff:
            recent.append(compact(r))
        else:
            latest.append(compact(r))
            if len(latest) > RECENT_CAP * 3:
                latest.sort(key=lambda x: x.get("fd") or "", reverse=True)
                del latest[RECENT_CAP:]

        # ---- paper-trade signal (code P, non-derivative, priced, has ticker) ----
        if is_buy and not r.get("der") and tk:
            sh, px = r.get("sh"), r.get("px")
            if sh and px and sh > 0 and px > 0 and is_common_equity(r.get("sec") or ""):
                key = (acc, tk)
                s = signals.get(key)
                if s is None:
                    s = signals[key] = {
                        "id": f"{acc}:{tk}", "acc": acc, "tk": tk,
                        "co": r.get("co") or "", "fd": fd, "td": r.get("td") or "",
                        "insider": r.get("in") or "", "rel": r.get("rel") or "",
                        "title": r.get("title") or "", "icik": cik,
                        "pcik": r.get("pcik") or "", "form": r.get("form") or "4",
                        "amend": r.get("amend") or 0, "sec": r.get("sec") or "",
                        "insider_sh": 0.0, "insider_val": 0.0, "lots": 0,
                    }
                s["insider_sh"] += sh
                s["insider_val"] += (r.get("val") if r.get("val") else sh * px)
                s["lots"] += 1
                td = r.get("td") or ""
                if td and (not s["td"] or td < s["td"]):
                    s["td"] = td

    for c in companies.values():
        c["recent"].sort(key=lambda x: x.get("fd") or "", reverse=True)
        del c["recent"][COMPANY_CAP:]

    if not recent:
        recent = latest
    recent.sort(key=lambda x: (x.get("fd") or "", x.get("td") or ""), reverse=True)
    del recent[RECENT_CAP:]

    return {
        "companies": companies, "insiders": insiders, "by_code": by_code,
        "by_rel": by_rel, "by_side": by_side, "by_form": by_form,
        "monthly": monthly, "yearly": yearly, "totals": totals,
        "filings": len(accs), "recent": recent, "signals": signals,
        "first_fd": "" if first_fd == "9999-99-99" else first_fd,
        "last_fd": last_fd,
    }


_BAD_SEC = ("option", "warrant", "right", "unit", "note", "debenture",
            "preferred", "restricted stock unit", "rsu", "phantom", "sar",
            "convertible", "deferred")
_OK_SEC = ("common", "ordinary", "class a", "class b", "adr",
           "american depositary", "share")


def is_common_equity(title: str) -> bool:
    """Conservative filter: only plain equity lines become paper trades."""
    t = (title or "").strip().lower()
    if not t:
        return True
    if any(b in t for b in _BAD_SEC):
        # "Common Stock" wins over an incidental word only if explicitly common
        if t.startswith("common stock") and "option" not in t and "unit" not in t:
            return True
        return False
    if any(o in t for o in _OK_SEC):
        return True
    return False


COMPACT_KEYS = ("fd", "td", "co", "tk", "in", "rel", "title", "code", "ct",
                "side", "sec", "sh", "px", "val", "ad", "af", "di", "der",
                "acc", "icik", "pcik", "form", "amend", "xp", "exp", "under")


def compact(r: dict) -> dict:
    return {k: r.get(k) for k in COMPACT_KEYS if r.get(k) not in (None, "", 0)}


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def jdump(obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def write_companies(agg, out_dir):
    rows = []
    buckets: dict[int, dict] = defaultdict(dict)
    for key, c in agg["companies"].items():
        cid = c["cik"] or key
        rows.append({
            "cik": cid, "co": c["co"] or key, "tk": c["tk"], "n": c["n"],
            "buy_n": c["buy_n"], "sell_n": c["sell_n"],
            "buy_v": r2(c["buy_v"]), "sell_v": r2(c["sell_v"]),
            "net_v": r2(c["buy_v"] - c["sell_v"]), "val": r2(c["val"]),
            "ins": len(c["insiders"]), "first": c["first"], "last": c["last"],
        })
        buckets[bucket_of(cid)][str(cid)] = c["recent"]
    rows.sort(key=lambda x: -x["n"])
    jdump(rows, os.path.join(out_dir, "companies.json"))

    cdir = os.path.join(out_dir, "co")
    if os.path.isdir(cdir):
        shutil.rmtree(cdir)
    os.makedirs(cdir, exist_ok=True)
    for b, payload in buckets.items():
        jdump(payload, os.path.join(cdir, f"{b}.json"))
    return len(rows), len(buckets)


def write_insiders(agg, out_dir):
    rows = []
    for key, p in agg["insiders"].items():
        rows.append({
            "cik": p["cik"] or key, "in": p["in"], "rel": p["rel"] or "Unknown",
            "title": p["title"], "n": p["n"], "buy_n": p["buy_n"],
            "sell_n": p["sell_n"], "buy_v": r2(p["buy_v"]),
            "sell_v": r2(p["sell_v"]), "net_v": r2(p["buy_v"] - p["sell_v"]),
            "cos": len(p["cos"]), "first": p["first"], "last": p["last"],
        })
    rows.sort(key=lambda x: -x["n"])
    total = len(rows)
    jdump(rows[:INSIDER_CAP], os.path.join(out_dir, "insiders.json"))
    return total


def write_year_csvs(data_dir, out_dir, target_year: int | None = None):
    """Publish gzipped CSV exports by filing year.

    A target-year build exports only that year; otherwise the complete local
    history is exported one file per year.
    """
    cdir = os.path.join(out_dir, "csv")
    os.makedirs(cdir, exist_ok=True)
    for fn in os.listdir(cdir):
        if fn.endswith((".csv", ".csv.gz")):
            os.remove(os.path.join(cdir, fn))

    handles: dict[str, tuple] = {}
    counts: dict[str, int] = defaultdict(int)
    try:
        for r in store.iter_rows(data_dir):
            y = (r.get("fd") or "")[:4]
            if not y.isdigit():
                continue
            if target_year and y != str(target_year):
                continue
            h = handles.get(y)
            if h is None:
                raw = open(os.path.join(cdir, f"trades-{y}.csv.gz"), "wb")
                gz = gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=9, mtime=0)
                txt = io.TextIOWrapper(gz, encoding="utf-8", newline="")
                w = csv.DictWriter(txt, fieldnames=list(COMPACT_KEYS), extrasaction="ignore")
                w.writeheader()
                h = handles[y] = (raw, gz, txt, w)
            h[3].writerow({k: r.get(k) for k in COMPACT_KEYS})
            counts[y] += 1
    finally:
        for raw, gz, txt, _ in handles.values():
            txt.flush()
            txt.detach()
            gz.close()
            raw.close()
    return dict(counts)


def write_summary(agg, out_dir, paper_counts, target_year: int | None = None):
    t = agg["totals"]
    months = sorted(agg["monthly"].items())
    code_rows = sorted(
        ({"code": k, "n": v["n"], "v": r2(v["v"])} for k, v in agg["by_code"].items()),
        key=lambda x: -x["n"])
    years = sorted(
        ({"y": k, **{kk: r2(vv) if isinstance(vv, float) else vv
                     for kk, vv in v.items()}} for k, v in agg["yearly"].items()),
        key=lambda x: x["y"])
    summary = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "target_year": target_year,
        "range": {"from": agg["first_fd"], "to": agg["last_fd"]},
        "counts": {
            "trades": t["n"], "filings": agg["filings"],
            "companies": len(agg["companies"]), "insiders": len(agg["insiders"]),
            "with_ticker": t["with_ticker"], "with_price": t["with_price"],
            "derivative": t["deriv"],
            "buys": agg["by_code"].get("P", {}).get("n", 0),
            "sells": agg["by_code"].get("S", {}).get("n", 0),
            "paper_positions": paper_counts.get("open", 0),
        },
        "value": {
            "total": r2(t["val"]), "buy": r2(t["buy"]), "sell": r2(t["sell"]),
            "net": r2(t["buy"] - t["sell"]),
        },
        "by_code": code_rows,
        "by_rel": sorted(({"rel": k, "n": v} for k, v in agg["by_rel"].items()),
                         key=lambda x: -x["n"]),
        "by_side": sorted(({"side": k, "n": v} for k, v in agg["by_side"].items()),
                          key=lambda x: -x["n"]),
        "by_form": sorted(({"form": k, "n": v} for k, v in agg["by_form"].items()),
                          key=lambda x: -x["n"]),
        "yearly": years,
        "months": [{"m": m, "n": v["n"], "buy": r2(v["buy"]), "sell": r2(v["sell"]),
                    "buy_n": v["buy_n"], "sell_n": v["sell_n"]} for m, v in months],
    }
    jdump(summary, os.path.join(out_dir, "summary.json"))
    return summary


# ---------------------------------------------------------------------------
# Prices — daily bars, full history
# ---------------------------------------------------------------------------

import re                      # noqa: E402
import time                    # noqa: E402
import urllib.error            # noqa: E402
import urllib.parse            # noqa: E402
import urllib.request          # noqa: E402

UA_MKT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/124.0 Safari/537.36")

PRICE_CACHE = os.path.join(HERE, "data", "prices")
STAKE = 10_000.0
MIN_INSIDER_VALUE = 1_000.0   # ignore token purchases


class _Throttle:
    def __init__(self, rate):
        self.iv = 1.0 / rate
        self._n = 0.0

    def wait(self):
        now = time.monotonic()
        d = self._n - now
        if d > 0:
            time.sleep(d)
        self._n = max(now, self._n) + self.iv


MKT_THROTTLE = _Throttle(4.0)


def http_get(url: str, retries: int = 2, timeout: int = 45) -> bytes | None:
    for attempt in range(retries + 1):
        MKT_THROTTLE.wait()
        req = urllib.request.Request(url, headers={
            "User-Agent": UA_MKT,
            "Accept": "application/json,text/csv,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw
        except urllib.error.HTTPError as e:
            if e.code in (404, 401, 403):
                return None
        except Exception:  # noqa: BLE001
            pass
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    return None


def yahoo_symbol(tk: str) -> str:
    """Map an SEC-reported ticker to Yahoo's symbology (class shares use '-')."""
    t = (tk or "").strip().upper()
    t = t.replace(".", "-").replace("/", "-")
    return re.sub(r"[^A-Z0-9\-]", "", t)


def valid_ticker(tk: str) -> bool:
    t = yahoo_symbol(tk)
    if not t or len(t) > 8:
        return False
    if t in ("NONE", "N-A", "NA", "NULL", "NOTAPPLICABLE"):
        return False
    return bool(re.match(r"^[A-Z][A-Z0-9\-]*$", t))


def _r4(x):
    try:
        return None if x is None else round(float(x), 4)
    except (TypeError, ValueError):
        return None


def bars_from_yahoo(raw: bytes):
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    try:
        res = doc["chart"]["result"][0]
        ts = res.get("timestamp") or []
        q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
        offset = int((res.get("meta") or {}).get("gmtoffset") or 0)
    except (KeyError, TypeError, IndexError):
        return None
    o_, c_ = q.get("open") or [], q.get("close") or []
    by = {}
    for i, t in enumerate(ts):
        if t is None:
            continue
        d = datetime.fromtimestamp(int(t) + offset, tz=timezone.utc).strftime("%Y-%m-%d")
        o = o_[i] if i < len(o_) else None
        c = c_[i] if i < len(c_) else None
        if o is None and c is None:
            continue
        by[d] = {"d": d, "o": _r4(o if o is not None else c),
                 "c": _r4(c if c is not None else o)}
    return [by[k] for k in sorted(by)] or None


def fetch_yahoo(tk: str):
    sym = urllib.parse.quote(yahoo_symbol(tk), safe="-")
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        raw = http_get(f"https://{host}/v8/finance/chart/{sym}"
                       f"?interval=1d&range=max&events=split")
        if raw:
            bars = bars_from_yahoo(raw)
            if bars:
                return bars, f"yahoo:{host.split('.')[0]}"
    return None, "yahoo"


def fetch_stooq(tk: str):
    sym = yahoo_symbol(tk).lower() + ".us"
    raw = http_get("https://stooq.com/q/d/l/?s=" + urllib.parse.quote(sym) + "&i=d")
    if not raw:
        return None, "stooq"
    text = raw.decode("utf-8", "replace")
    lines = text.strip().splitlines()
    if len(lines) < 2 or not lines[0].lower().startswith("date"):
        return None, "stooq"
    hdr = [h.strip().lower() for h in lines[0].split(",")]
    try:
        di, oi, ci = hdr.index("date"), hdr.index("open"), hdr.index("close")
    except ValueError:
        return None, "stooq"
    bars = []
    for ln in lines[1:]:
        p = ln.split(",")
        if len(p) <= max(di, oi, ci):
            continue
        d = p[di].strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
            continue
        try:
            o, c = float(p[oi]), float(p[ci])
        except ValueError:
            continue
        if o <= 0 or c <= 0:
            continue
        bars.append({"d": d, "o": _r4(o), "c": _r4(c)})
    bars.sort(key=lambda b: b["d"])
    return (bars or None), "stooq"


def cache_path(tk: str) -> str:
    return os.path.join(PRICE_CACHE, f"{yahoo_symbol(tk)}.json.gz")


def load_bars_cached(tk: str, max_age_days: int = 3):
    p = cache_path(tk)
    if not os.path.exists(p):
        return None, None
    try:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None, None
    fetched = (doc.get("fetched") or "")[:10]
    stale = True
    if fetched:
        try:
            age = (date.today() - date.fromisoformat(fetched)).days
            stale = age > max_age_days
        except ValueError:
            stale = True
    return doc.get("bars") or None, ("cache-stale" if stale else doc.get("src") or "cache")


def save_bars(tk: str, bars, src: str):
    os.makedirs(PRICE_CACHE, exist_ok=True)
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6, mtime=0) as gz:
        gz.write(json.dumps({"tk": tk, "src": src,
                             "fetched": date.today().isoformat(), "bars": bars},
                            separators=(",", ":")).encode("utf-8"))
    with open(cache_path(tk), "wb") as f:
        f.write(buf.getvalue())


def get_bars(tk: str, offline: bool = False):
    bars, src = load_bars_cached(tk)
    if bars and (src != "cache-stale" or offline):
        return bars, src
    if offline:
        return (bars or []), (src or "none")
    for fn in (fetch_yahoo, fetch_stooq):
        got, s = fn(tk)
        if got:
            save_bars(tk, got, s)
            return got, s
    return (bars or []), (src or "none")


# ---------------------------------------------------------------------------
# Simulation — $10,000 at the first open strictly after the filing is public
# ---------------------------------------------------------------------------

def next_open_after(bars, fd):
    """First bar strictly after the filing date that has a usable open."""
    lo, hi = 0, len(bars)
    while lo < hi:                       # first index with d > fd
        mid = (lo + hi) // 2
        if bars[mid]["d"] <= fd:
            lo = mid + 1
        else:
            hi = mid
    for i in range(lo, len(bars)):
        o = bars[i].get("o")
        if o and o > 0:
            return i
    return None


def simulate(sig, bars, asof):
    out = dict(sig)
    out.update({"stake": STAKE, "status": "no_price", "entry_d": None,
                "entry_px": None, "shares": None, "last_d": None, "last_px": None,
                "mtm": None, "pnl": None, "roi": None, "gap": None,
                "r1": None, "r5": None, "r21": None, "r63": None, "r252": None,
                "delay_fd_entry": None, "hold": None})
    if sig.get("td") and sig.get("fd"):
        try:
            out["delay_td_fd"] = (date.fromisoformat(sig["fd"])
                                  - date.fromisoformat(sig["td"])).days
        except ValueError:
            out["delay_td_fd"] = None

    usable = [b for b in bars if b["d"] <= asof]
    if not usable:
        return out
    i = next_open_after(usable, sig["fd"])
    if i is None:
        out["status"] = "awaiting_entry"
        return out
    entry = usable[i]
    epx = float(entry["o"])
    sh = STAKE / epx
    out.update({"status": "open", "entry_d": entry["d"], "entry_px": r4(epx),
                "shares": r4(sh)})
    try:
        out["delay_fd_entry"] = (date.fromisoformat(entry["d"])
                                 - date.fromisoformat(sig["fd"])).days
    except ValueError:
        pass
    ipx = sig.get("insider_px")
    if ipx:
        out["gap"] = r4(epx / float(ipx) - 1.0)

    last = usable[-1]
    lpx = last.get("c")
    if lpx and lpx > 0:
        mtm = sh * float(lpx)
        out.update({"last_d": last["d"], "last_px": r4(lpx), "mtm": r2(mtm),
                    "pnl": r2(mtm - STAKE), "roi": r4(mtm / STAKE - 1.0),
                    "hold": len(usable) - 1 - i})
    for key, n in (("r1", 1), ("r5", 5), ("r21", 21), ("r63", 63), ("r252", 252)):
        j = i + n
        if j < len(usable) and usable[j].get("c"):
            out[key] = r4(float(usable[j]["c"]) / epx - 1.0)
    return out


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def stats(xs):
    xs = sorted(x for x in xs if x is not None)
    n = len(xs)
    if not n:
        return {"n": 0, "mean": None, "median": None, "win": None,
                "p25": None, "p75": None, "min": None, "max": None}

    def pct(p):
        if n == 1:
            return xs[0]
        k = (n - 1) * p
        lo, hi = int(k), min(int(k) + 1, n - 1)
        return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)

    return {"n": n, "mean": r4(sum(xs) / n), "median": r4(pct(0.5)),
            "win": r4(sum(1 for x in xs if x > 0) / n),
            "p25": r4(pct(0.25)), "p75": r4(pct(0.75)),
            "min": r4(xs[0]), "max": r4(xs[-1])}


SIZE_BUCKETS = [(10_000, "<$10k"), (50_000, "$10k–50k"), (250_000, "$50k–250k"),
                (1_000_000, "$250k–$1M"), (float("inf"), ">$1M")]


def size_bucket(v):
    for lim, name in SIZE_BUCKETS:
        if v < lim:
            return name
    return ">$1M"


def role_bucket(rel, title):
    r, t = (rel or ""), (title or "").lower()
    if "Officer" in r:
        if any(k in t for k in ("chief executive", "ceo")):
            return "CEO"
        if any(k in t for k in ("chief financial", "cfo")):
            return "CFO"
        if "president" in t:
            return "President"
        return "Other officer"
    if "10% Owner" in r:
        return "10% owner"
    if "Director" in r:
        return "Director"
    return "Other"


def analyze(positions):
    opens = [p for p in positions if p["status"] == "open" and p.get("roi") is not None]
    by_role, by_size, by_year = defaultdict(list), defaultdict(list), defaultdict(list)
    for p in opens:
        by_role[role_bucket(p.get("rel"), p.get("title"))].append(p["roi"])
        by_size[size_bucket(p.get("insider_val") or 0)].append(p["roi"])
        by_year[(p.get("fd") or "")[:4]].append(p["roi"])

    deployed = sum(p["stake"] for p in opens)
    value = sum(p["mtm"] for p in opens if p.get("mtm") is not None)

    def tbl(d, key):
        rows = [{key: k, **stats(v)} for k, v in d.items()]
        rows.sort(key=lambda x: -x["n"])
        return rows

    ranked = sorted(opens, key=lambda p: p["roi"], reverse=True)
    keep = ("id", "tk", "co", "insider", "rel", "title", "fd", "td", "entry_d",
            "entry_px", "insider_px", "insider_sh", "insider_val", "shares",
            "last_px", "roi", "pnl", "mtm", "gap", "acc")

    def slim(p):
        return {k: p.get(k) for k in keep}

    out = {
        "stake": STAKE,
        "counts": {
            "signals": len(positions),
            "open": len(opens),
            "awaiting_entry": sum(1 for p in positions if p["status"] == "awaiting_entry"),
            "no_price": sum(1 for p in positions if p["status"] == "no_price"),
        },
        "capital": {
            "deployed": r2(deployed), "value": r2(value),
            "pnl": r2(value - deployed),
            "roi": r4(value / deployed - 1.0) if deployed else None,
        },
        "roi": stats([p["roi"] for p in opens]),
        "gap": stats([p["gap"] for p in opens if p.get("gap") is not None]),
        "horizons": {h: stats([p.get(h) for p in opens]) for h in
                     ("r1", "r5", "r21", "r63", "r252")},
        "by_role": tbl(by_role, "role"),
        "by_size": tbl(by_size, "size"),
        "by_year": sorted([{"y": k, **stats(v)} for k, v in by_year.items()],
                          key=lambda x: x["y"]),
        "best": [slim(p) for p in ranked[:25]],
        "worst": [slim(p) for p in ranked[-25:]][::-1],
        "rule": {
            "stake_usd": STAKE,
            "signal": "Form 4/5 non-derivative transaction code P (open-market or "
                      "private purchase) of common equity or ADR, with a reported "
                      "share count and price, aggregated per filing per ticker",
            "min_insider_value": MIN_INSIDER_VALUE,
            "entry": "regular-session OPEN of the first trading day strictly after "
                     "the SEC filing date — never the insider's own fill price",
            "exit": "none — positions stay open for forward testing",
            "mark": "latest available regular-session close",
            "lookahead": "none — no price at or before the filing date is used as a fill",
            "costs": "no commission, slippage or spread modelled",
            "prices": "Yahoo Finance daily bars, Stooq fallback",
            "splits": "split-adjusted series; insider prices are as-filed",
        },
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    out["findings"] = findings(out, opens)
    return out


def findings(a, opens):
    f = []
    n = a["counts"]["open"]
    if not n:
        return ["No paper positions have been opened yet."]
    roi = a["roi"]
    f.append(
        f"{n:,} simulated $10,000 positions are open, deploying "
        f"${a['capital']['deployed']:,.0f}. Current mark-to-market is "
        f"${a['capital']['value']:,.0f}, a net P&L of ${a['capital']['pnl']:,.0f} "
        f"({a['capital']['roi'] * 100:+.2f}% on deployed capital)."
        if a["capital"]["roi"] is not None else f"{n:,} positions open.")
    if roi["median"] is not None:
        f.append(
            f"Median position ROI is {roi['median'] * 100:+.2f}% and the mean is "
            f"{roi['mean'] * 100:+.2f}%; {roi['win'] * 100:.1f}% of positions are "
            f"above water. The spread runs from {roi['min'] * 100:+.1f}% to "
            f"{roi['max'] * 100:+.1f}% (p25 {roi['p25'] * 100:+.1f}%, "
            f"p75 {roi['p75'] * 100:+.1f}%).")
    g = a["gap"]
    if g["n"]:
        f.append(
            f"Following the filing costs a median {g['median'] * 100:+.2f}% versus the "
            f"insider's own fill: by the time a Form 4 is public, the entry open is "
            f"{'higher' if (g['median'] or 0) > 0 else 'lower'} than what the insider paid "
            f"in the typical case.")
    hz = [(h, a["horizons"][h]) for h in ("r1", "r5", "r21", "r63", "r252")
          if a["horizons"][h]["n"] >= 30]
    if hz:
        lbl = {"r1": "1 session", "r5": "1 week", "r21": "1 month",
               "r63": "1 quarter", "r252": "1 year"}
        parts = [f"{lbl[h]} {s['median'] * 100:+.2f}% (n={s['n']:,})" for h, s in hz]
        f.append("Median return after entry by holding period: " + "; ".join(parts) + ".")
    roles = [r for r in a["by_role"] if r["n"] >= 30]
    if roles:
        best = max(roles, key=lambda r: r["median"])
        worst = min(roles, key=lambda r: r["median"])
        f.append(
            f"By role, {best['role']} purchases show the best median ROI at "
            f"{best['median'] * 100:+.2f}% (n={best['n']:,}), while {worst['role']} "
            f"purchases trail at {worst['median'] * 100:+.2f}% (n={worst['n']:,}).")
    sizes = [s for s in a["by_size"] if s["n"] >= 30]
    if sizes:
        b = max(sizes, key=lambda s: s["median"])
        f.append(
            f"By conviction, insider buys of {b['size']} produced the best median ROI "
            f"at {b['median'] * 100:+.2f}% across {b['n']:,} positions.")
    return f


def equity_curve(positions):
    """Deployed capital vs mark-to-market value, by entry month."""
    months = defaultdict(lambda: {"n": 0, "deployed": 0.0, "value": 0.0})
    for p in positions:
        if p["status"] != "open" or p.get("mtm") is None:
            continue
        m = (p.get("entry_d") or "")[:7]
        if not m:
            continue
        months[m]["n"] += 1
        months[m]["deployed"] += p["stake"]
        months[m]["value"] += p["mtm"]
    out, cd, cv = [], 0.0, 0.0
    for m in sorted(months):
        d = months[m]
        cd += d["deployed"]
        cv += d["value"]
        out.append({"m": m, "n": d["n"], "deployed": r2(cd), "value": r2(cv),
                    "pnl": r2(cv - cd), "roi": r4(cv / cd - 1.0) if cd else None})
    return out


def write_paper(positions, out_dir):
    pdir = os.path.join(out_dir, "paper")
    os.makedirs(pdir, exist_ok=True)
    a = analyze(positions)
    jdump(a, os.path.join(pdir, "summary.json"))
    jdump(equity_curve(positions), os.path.join(pdir, "equity.json"))

    ordered = sorted(positions, key=lambda p: (p.get("fd") or ""), reverse=True)
    cols = ["id", "status", "fd", "td", "entry_d", "tk", "co", "insider", "rel",
            "title", "sec", "lots", "insider_sh", "insider_px", "insider_val",
            "entry_px", "gap", "shares", "stake", "last_d", "last_px", "mtm",
            "pnl", "roi", "r1", "r5", "r21", "r63", "r252", "delay_td_fd",
            "delay_fd_entry", "hold", "acc", "form", "amend", "icik", "pcik"]
    jdump([{k: p.get(k) for k in cols} for p in ordered[:PAPER_BROWSE_CAP]],
          os.path.join(pdir, "positions.json"))

    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as gz:
        txt = io.TextIOWrapper(gz, encoding="utf-8", newline="")
        w = csv.DictWriter(txt, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for p in ordered:
            w.writerow({k: p.get(k) for k in cols})
        txt.flush()
        txt.detach()
    with open(os.path.join(pdir, "positions.csv.gz"), "wb") as f:
        f.write(buf.getvalue())
    return a


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Build CEOTrades site data.")
    ap.add_argument("--data", default=DATA_IN)
    ap.add_argument("--out", default=SITE_DATA)
    ap.add_argument("--year", type=int, default=0,
                    help="publish only this SEC filing year (0 = all local rows)")
    ap.add_argument("--offline", action="store_true",
                    help="use only cached prices (no network)")
    ap.add_argument("--max-tickers", type=int, default=0,
                    help="limit tickers priced this run (0 = no limit)")
    ap.add_argument("--price-budget-min", type=float,
                    default=float(os.environ.get("CEOTRADES_PRICE_MIN", "0")),
                    help="maximum minutes to spend fetching market prices; 0 = no explicit limit")
    ap.add_argument("--audit-year", type=int, default=0,
                    help="target year for completeness/audit artifacts (default: --year or current year)")
    args = ap.parse_args()
    target_year = args.year or None
    audit_year = args.audit_year or target_year or audit_mod.asof_today().year

    if not store.shard_files(args.data):
        log(f"No trade shards found in {args.data}. Run bulk_backfill.py first.")
        return 1

    log("Pass 1: streaming trade store …")
    if target_year:
        log(f"  filtering to SEC filing year {target_year}")
    agg = collect(args.data, target_year=target_year)
    t = agg["totals"]
    log(f"  {t['n']:,} rows | {agg['filings']:,} filings | "
        f"{len(agg['companies']):,} companies | {len(agg['insiders']):,} insiders")
    log(f"  filing dates {agg['first_fd']} .. {agg['last_fd']}")

    log("Pass 2: pricing insider-buy signals …")
    sigs = []
    for s in agg["signals"].values():
        if s["insider_sh"] <= 0 or s["insider_val"] < MIN_INSIDER_VALUE:
            continue
        if not valid_ticker(s["tk"]):
            continue
        s["insider_px"] = r4(s["insider_val"] / s["insider_sh"])
        s["insider_sh"] = r4(s["insider_sh"])
        s["insider_val"] = r2(s["insider_val"])
        sigs.append(s)
    sigs.sort(key=lambda s: (s["fd"], s["tk"]))
    log(f"  {len(sigs):,} qualifying buy signals")

    by_tk = defaultdict(list)
    for s in sigs:
        by_tk[s["tk"]].append(s)
    tickers = sorted(by_tk)
    if args.max_tickers:
        tickers = tickers[:args.max_tickers]
    log(f"  {len(tickers):,} unique tickers to price")

    asof = audit_mod.asof_today().isoformat()
    positions, priced, nosrc = [], 0, 0
    price_deadline = None
    if args.price_budget_min and args.price_budget_min > 0:
        price_deadline = time.monotonic() + args.price_budget_min * 60.0
    for i, tk in enumerate(tickers, 1):
        if price_deadline is not None and time.monotonic() > price_deadline:
            bars, src = [], "price_budget_exhausted"
        else:
            bars, src = get_bars(tk, offline=args.offline)
        if bars:
            priced += 1
        else:
            nosrc += 1
        for s in by_tk[tk]:
            p = simulate(s, bars, asof)
            p["price_src"] = src
            positions.append(p)
        if i % 100 == 0 or i == len(tickers):
            log(f"  priced {i}/{len(tickers)} tickers "
                f"({priced} with bars, {nosrc} without)")

    log("Pass 3: writing site data …")
    os.makedirs(args.out, exist_ok=True)
    paper = write_paper(positions, args.out)
    nco, nb = write_companies(agg, args.out)
    nins = write_insiders(agg, args.out)
    jdump(agg["recent"], os.path.join(args.out, "recent.json"))

    # data/trades.csv — plain-text export of the recent tape. Kept because the
    # published workflow sanity-checks this exact path, and it is the most
    # convenient single-file download for spreadsheet users.
    with open(os.path.join(args.out, "trades.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(COMPACT_KEYS), extrasaction="ignore")
        w.writeheader()
        for r in agg["recent"]:
            w.writerow({k: r.get(k) for k in COMPACT_KEYS})
    ycounts = write_year_csvs(args.data, args.out, target_year=target_year)
    summary = write_summary(agg, args.out, paper["counts"], target_year=target_year)
    report_path = (os.path.join(ROOT, "INSIDER_TRADING_FORENSIC_REPORT.md")
                   if os.path.abspath(args.out) == os.path.abspath(SITE_DATA) else None)
    audit = audit_mod.write_audit(args.data, args.out, audit_year, report_path)
    flags = irregularities_mod.write_irregularities(args.data, args.out, audit_year, audit)

    log(f"  companies.json: {nco:,} rows ({nb} detail buckets)")
    log(f"  insiders.json:  {nins:,} insiders aggregated")
    log(f"  recent.json:    {len(agg['recent']):,} rows")
    log(f"  csv/:           {sum(ycounts.values()):,} rows across {len(ycounts)} years")
    log(f"  audit:          {audit['completeness']['status']} | "
        f"{audit['integrity']['row_issues']:,} row issues")
    log(f"  irregularities: {len(flags):,} automated review flag(s)")
    log(f"  paper:          {paper['counts']['open']:,} open positions, "
        f"ROI {paper['capital']['roi']}")
    log(f"Done. {summary['counts']['trades']:,} transactions published.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
