#!/usr/bin/env python3
"""
CEOTrades paper-trading engine.

Forward-tests a single, fully specified rule on real SEC Form 4/5 data
and real daily market prices. No lookahead, no manual input.

Rule (the only strategy this module implements)
-----------------------------------------------
When an insider files a non-derivative open-market purchase (transaction
code P) of common equity, the simulator buys $10,000 of that ticker at
the next regular-session OPEN strictly after the Form 4 filing date.

Why next session's open, not the insider's fill:
  Form 4s may be filed up to two business days after the trade, and at
  any hour on the filing date. Using the following session's open is the
  first price a public follower could consistently obtain after the
  filing is in EDGAR. The insider's own price is recorded for comparison
  (the "gap") but is never used as our fill.

Mark-to-market: latest regular-session close in the price series.
Positions stay open (this is a forward-test collection, not a round-trip
backtest). Horizon returns (same-day close, +1/+5/+21/+63 sessions) are
filled in as those closes become available.

Price sources, in order, all free and keyless:
  1. Yahoo Finance chart v8 (query1, then query2)
  2. Stooq daily CSV
  3. Nasdaq historical quote API

Standard library only.
"""

from __future__ import annotations

import argparse
import csv
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
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from collect import load_dataset  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STAKE = 10000.0
# Skip sub-dollar "purchases" (almost always a $0 grant mis-coded or a stub).
MIN_INSIDER_VALUE = 1.0
# Yahoo/Stooq/Nasdaq polite pacing.
MKT_RATE = 4.0

UA_SEC = "CEOTrades Insider-Trade Collector (github.com/karagemop466-tech/CEOTrades)"
UA_MKT = (
    "Mozilla/5.0 (compatible; CEOTrades/1.0; "
    "+https://github.com/karagemop466-tech/CEOTrades)"
)

# Security titles that are not the issuer's common equity. Code-P on these
# is not "buy the stock".
_BAD_SEC = re.compile(
    r"\b("
    r"preferred|pref\.?|warrant|warrants|"
    r"stock option|option(?:s)?|"
    r"restricted stock units?|r[su]us?|"
    r"phantom|performance (?:share|unit|stock)|psu[s]?|"
    r"stock appreciation|sa[rs][s]?|"
    r"right to (?:buy|purchase)|subscription right|"
    r"convertible (?:note|debenture|preferred)|"
    r"note|bond|debenture|debt|swap|future|"
    r"unit \(|"
    r"depositary share"  # typically preferred; ADR/ADS handled separately
    r")\b",
    re.I,
)
_ADR_OK = re.compile(
    r"\b(adr|ads|american depositary(?:\s+(?:receipt|share))?s?)\b", re.I
)
_TICKER_OK = re.compile(r"^[A-Z][A-Z0-9]{0,5}(?:[./-][A-Z0-9]{1,2})?$")
_TICKER_BAD = {
    "NONE", "N/A", "NA", "NULL", "NONEYET", "OTC", "PINK", "UNKNOWN",
    "NOTAPPLICABLE", "N.A.",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str):
    print(msg, flush=True)


def fnum(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def round4(x):
    if x is None:
        return None
    return round(float(x), 4)


def round2(x):
    if x is None:
        return None
    return round(float(x), 2)


# ---------------------------------------------------------------------------
# HTTP (market data — separate UA from the SEC collector)
# ---------------------------------------------------------------------------

class Throttle:
    def __init__(self, rate_per_sec: float):
        self.min_interval = 1.0 / max(rate_per_sec, 0.1)
        self._next = 0.0

    def wait(self):
        now = time.monotonic()
        delta = self._next - now
        if delta > 0:
            time.sleep(delta)
        self._next = max(now, self._next) + self.min_interval


THROTTLE = Throttle(MKT_RATE)


def http_get(url: str, ua: str, retries: int = 3, timeout: int = 30):
    last_err = None
    for attempt in range(retries + 1):
        THROTTLE.wait()
        req = urllib.request.Request(url, headers={
            "User-Agent": ua,
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                return raw
        except urllib.error.HTTPError as e:
            if e.code in (404, 400):
                return None
            last_err = f"HTTP {e.code} for {url}"
            if e.code in (429, 503) and attempt < retries:
                time.sleep(5 * (2 ** attempt))
                continue
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last_err = f"{type(e).__name__}: {e} for {url}"
        if attempt < retries:
            time.sleep(2 * (attempt + 1))
    log(f"  ! price fetch failed: {last_err}")
    return None


# ---------------------------------------------------------------------------
# Ticker / security filters (pure)
# ---------------------------------------------------------------------------

def normalize_ticker(tk: str) -> str:
    t = re.sub(r"\s+", "", (tk or "")).upper()
    t = t.replace("_", "-")
    return t


def valid_ticker(tk: str) -> bool:
    t = normalize_ticker(tk)
    if not t or t in _TICKER_BAD:
        return False
    return bool(_TICKER_OK.match(t))


def yahoo_symbol(tk: str) -> str:
    """Yahoo share-class convention: BRK.B / BRK/B -> BRK-B."""
    t = normalize_ticker(tk)
    return t.replace(".", "-").replace("/", "-")


def stooq_symbol(tk: str) -> str:
    return yahoo_symbol(tk).lower() + ".us"


def is_common_equity(title: str, der) -> bool:
    """True iff this row is a non-derivative common-equity (or ADR) line."""
    if der in (1, "1", True):
        return False
    t = (title or "").strip()
    if not t:
        return True
    if _ADR_OK.search(t):
        return True
    if _BAD_SEC.search(t):
        return False
    return True


# ---------------------------------------------------------------------------
# Signal extraction (pure — no I/O, no prices)
# ---------------------------------------------------------------------------

def _row_id(r: dict) -> str:
    return f"{r.get('acc','')}:{r.get('tk','')}"


def extract_signals(rows: list[dict]) -> list[dict]:
    """Collapse qualifying code-P lots into one signal per (accession, ticker).

    Qualifying lot:
      - transaction code P (open-market / private purchase)
      - non-derivative common equity (or ADR)
      - valid ticker
      - shares > 0 and price > 0
    Lots in the same filing for the same ticker are VWAP-aggregated so a
    multi-lot Form 4 becomes one $10k paper trade, not N of them.
    """
    buckets: dict[tuple, dict] = {}
    for r in rows:
        if (r.get("code") or "") != "P":
            continue
        if not is_common_equity(r.get("sec") or "", r.get("der")):
            continue
        tk = normalize_ticker(r.get("tk") or "")
        if not valid_ticker(tk):
            continue
        sh = fnum(r.get("sh"))
        px = fnum(r.get("px"))
        if sh is None or px is None or sh <= 0 or px <= 0:
            continue
        val = fnum(r.get("val"))
        if val is None:
            val = sh * px
        key = (r.get("acc") or "", tk)
        b = buckets.get(key)
        if b is None:
            b = {
                "id": f"{r.get('acc','')}:{tk}",
                "acc": r.get("acc") or "",
                "form": r.get("form") or "4",
                "amend": int(r.get("amend") or 0),
                "fd": r.get("fd") or "",
                "td": r.get("td") or "",
                "co": r.get("co") or "",
                "tk": tk,
                "icik": r.get("icik") or "",
                "pcik": r.get("pcik") or "",
                "insider": r.get("in") or "",
                "rel": r.get("rel") or "",
                "title": r.get("title") or "",
                "sec": r.get("sec") or "",
                "insider_sh": 0.0,
                "insider_val": 0.0,
                "lots": 0,
            }
            buckets[key] = b
        b["insider_sh"] += sh
        b["insider_val"] += val
        b["lots"] += 1
        # Keep the earliest transaction date in the filing (when the insider
        # first put money in) and the latest filing date (already unique).
        if r.get("td") and (not b["td"] or r["td"] < b["td"]):
            b["td"] = r["td"]
        if r.get("sec") and not b["sec"]:
            b["sec"] = r["sec"]

    signals = []
    for b in buckets.values():
        if b["insider_val"] < MIN_INSIDER_VALUE or b["insider_sh"] <= 0:
            continue
        b["insider_px"] = round4(b["insider_val"] / b["insider_sh"])
        b["insider_sh"] = round4(b["insider_sh"])
        b["insider_val"] = round2(b["insider_val"])
        signals.append(b)
    signals.sort(key=lambda s: (s["fd"], s["td"], s["tk"], s["acc"]))
    return signals


# ---------------------------------------------------------------------------
# Daily bars + entry/MTM (pure given a bar list)
# ---------------------------------------------------------------------------

def _parse_ymd(s: str):
    if not s or len(s) < 10:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def next_open_after(bars: list[dict], fd: str):
    """First regular session strictly after filing date `fd`.

    Returns (entry_date, open, close_that_day, index) or None.
    Using a session on fd itself would look ahead: the filing may have
    been accepted after that day's open (or after the close).
    """
    if not bars or not fd:
        return None
    for i, b in enumerate(bars):
        if b["d"] > fd and b.get("o"):
            return b["d"], float(b["o"]), fnum(b.get("c")), i
    return None


def close_on_or_before(bars: list[dict], day: str):
    last = None
    for b in bars:
        if b["d"] <= day and b.get("c"):
            last = b
        elif b["d"] > day:
            break
    return last


def horizon_close(bars: list[dict], entry_idx: int, n_sessions: int):
    """Close of the bar `n_sessions` after the entry bar (0 = entry day)."""
    j = entry_idx + n_sessions
    if 0 <= j < len(bars) and bars[j].get("c"):
        return float(bars[j]["c"]), bars[j]["d"]
    return None, None


def simulate_one(signal: dict, bars: list[dict], asof: str, stake: float = STAKE) -> dict:
    """Apply the paper rule to one signal. Never reads future-of-`asof` bars."""
    out = dict(signal)
    out.update({
        "stake": stake,
        "status": "no_price",
        "entry_d": None,
        "entry_px": None,
        "entry_src": None,
        "shares": None,
        "last_d": None,
        "last_px": None,
        "mtm": None,
        "pnl": None,
        "roi": None,
        "gap": None,
        "delay_td_fd": None,
        "delay_fd_entry": None,
        "r0": None, "r0_d": None,
        "r1": None, "r1_d": None,
        "r5": None, "r5_d": None,
        "r21": None, "r21_d": None,
        "r63": None, "r63_d": None,
        "hold_sessions": None,
    })
    td, fd = signal.get("td") or "", signal.get("fd") or ""
    if td and fd:
        d0, d1 = _parse_ymd(td), _parse_ymd(fd)
        if d0 and d1:
            out["delay_td_fd"] = (d1 - d0).days

    usable = [b for b in bars if b["d"] <= asof]
    if not usable:
        return out

    nxt = next_open_after(usable, fd)
    if nxt is None:
        # Filing is public but the next session has not printed an open yet.
        out["status"] = "awaiting_entry"
        return out

    entry_d, entry_px, entry_close, idx = nxt
    if not entry_px or entry_px <= 0:
        out["status"] = "no_price"
        return out

    shares = stake / entry_px
    out["status"] = "open"
    out["entry_d"] = entry_d
    out["entry_px"] = round4(entry_px)
    out["shares"] = round4(shares)
    d0, d1 = _parse_ymd(fd), _parse_ymd(entry_d)
    if d0 and d1:
        out["delay_fd_entry"] = (d1 - d0).days
    if signal.get("insider_px"):
        out["gap"] = round4((entry_px / float(signal["insider_px"])) - 1.0)

    last = usable[-1]
    last_px = fnum(last.get("c"))
    if last_px and last_px > 0:
        out["last_d"] = last["d"]
        out["last_px"] = round4(last_px)
        mtm = shares * last_px
        out["mtm"] = round2(mtm)
        out["pnl"] = round2(mtm - stake)
        out["roi"] = round4((mtm / stake) - 1.0)
        out["hold_sessions"] = max(0, len(usable) - 1 - idx)

    def _set(key, n):
        px, dd = horizon_close(usable, idx, n)
        if px is None:
            return
        out[key] = round4((px / entry_px) - 1.0)
        out[key + "_d"] = dd

    _set("r0", 0)
    _set("r1", 1)
    _set("r5", 5)
    _set("r21", 21)
    _set("r63", 63)
    return out


# ---------------------------------------------------------------------------
# Price downloaders
# ---------------------------------------------------------------------------

def _bars_from_yahoo_json(raw: bytes) -> list[dict] | None:
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    try:
        result = doc["chart"]["result"][0]
        ts = result.get("timestamp") or []
        q = (result.get("indicators") or {}).get("quote") or [{}]
        q = q[0]
        opens, highs, lows, closes, vols = (
            q.get("open") or [], q.get("high") or [], q.get("low") or [],
            q.get("close") or [], q.get("volume") or [],
        )
        offset = int((result.get("meta") or {}).get("gmtoffset") or 0)
    except (KeyError, TypeError, IndexError):
        return None
    bars = []
    for i, t in enumerate(ts):
        if t is None:
            continue
        # Session date in the exchange timezone (gmtoffset seconds from UTC).
        d = datetime.fromtimestamp(int(t) + offset, tz=timezone.utc).strftime("%Y-%m-%d")
        o = opens[i] if i < len(opens) else None
        c = closes[i] if i < len(closes) else None
        if o is None and c is None:
            continue
        bars.append({
            "d": d,
            "o": round4(o if o is not None else c),
            "h": round4(highs[i] if i < len(highs) else None),
            "l": round4(lows[i] if i < len(lows) else None),
            "c": round4(c if c is not None else o),
            "v": int(vols[i]) if i < len(vols) and vols[i] is not None else None,
        })
    bars.sort(key=lambda b: b["d"])
    # Collapse any duplicate dates (keep last).
    by = {}
    for b in bars:
        by[b["d"]] = b
    return [by[k] for k in sorted(by)]


def fetch_yahoo(tk: str) -> tuple[list[dict] | None, str]:
    sym = urllib.parse.quote(yahoo_symbol(tk), safe="-")
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        url = f"https://{host}/v8/finance/chart/{sym}?interval=1d&range=1y&events=div%2Csplit"
        raw = http_get(url, UA_MKT)
        if not raw:
            continue
        bars = _bars_from_yahoo_json(raw)
        if bars:
            return bars, f"yahoo:{host.split('.')[0]}"
    return None, "yahoo"


def fetch_stooq(tk: str) -> tuple[list[dict] | None, str]:
    sym = stooq_symbol(tk)
    url = "https://stooq.com/q/d/l/?s=" + urllib.parse.quote(sym) + "&i=d"
    raw = http_get(url, UA_MKT)
    if not raw:
        return None, "stooq"
    try:
        text = raw.decode("utf-8", "replace")
    except Exception:
        return None, "stooq"
    if "Date" not in text[:40] and "date" not in text[:40].lower():
        return None, "stooq"
    bars = []
    for i, line in enumerate(text.splitlines()):
        if i == 0 or not line.strip():
            continue
        parts = line.split(",")
        if len(parts) < 5:
            continue
        d, o, h, l, c = parts[0].strip(), parts[1], parts[2], parts[3], parts[4]
        if not re.match(r"\d{4}-\d{2}-\d{2}", d):
            continue
        o, c = fnum(o), fnum(c)
        if o is None and c is None:
            continue
        if o is not None and o <= 0:
            o = c
        if c is not None and c <= 0:
            c = o
        if not o or not c:
            continue
        vol = fnum(parts[5]) if len(parts) > 5 else None
        bars.append({
            "d": d, "o": round4(o), "h": round4(fnum(h)),
            "l": round4(fnum(l)), "c": round4(c),
            "v": int(vol) if vol is not None else None,
        })
    bars.sort(key=lambda b: b["d"])
    # Stooq returns the full history; keep 1y to match Yahoo.
    cut = (utcnow().date() - timedelta(days=400)).isoformat()
    bars = [b for b in bars if b["d"] >= cut]
    return (bars or None), "stooq"


def fetch_nasdaq(tk: str) -> tuple[list[dict] | None, str]:
    sym = urllib.parse.quote(yahoo_symbol(tk))
    today = utcnow().date()
    url = (
        f"https://api.nasdaq.com/api/quote/{sym}/historical"
        f"?assetclass=stocks&fromdate={(today - timedelta(days=400)).isoformat()}"
        f"&todate={today.isoformat()}&limit=9999"
    )
    raw = http_get(url, UA_MKT)
    if not raw:
        return None, "nasdaq"
    try:
        doc = json.loads(raw.decode("utf-8"))
        rows = (((doc or {}).get("data") or {}).get("tradesTable") or {}).get("rows") or []
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError):
        return None, "nasdaq"
    bars = []
    for r in rows:
        # Nasdaq dates look like "08/24/2026"; prices like "$309.90".
        ds = (r.get("date") or "").strip()
        try:
            d = datetime.strptime(ds, "%m/%d/%Y").strftime("%Y-%m-%d")
        except ValueError:
            continue

        def money(x):
            return fnum(str(x or "").replace("$", "").replace(",", "").replace("—", "").replace("--", ""))

        o, h, l, c = money(r.get("open")), money(r.get("high")), money(r.get("low")), money(r.get("close"))
        if o is None and c is None:
            continue
        bars.append({
            "d": d, "o": round4(o if o is not None else c),
            "h": round4(h), "l": round4(l),
            "c": round4(c if c is not None else o),
            "v": None,
        })
    bars.sort(key=lambda b: b["d"])
    by = {b["d"]: b for b in bars}
    bars = [by[k] for k in sorted(by)]
    return (bars or None), "nasdaq"


def fetch_bars(tk: str) -> tuple[list[dict], str]:
    for fn in (fetch_yahoo, fetch_stooq, fetch_nasdaq):
        bars, src = fn(tk)
        if bars:
            return bars, src
    return [], "none"


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

SIZE_BUCKETS = [
    (10_000, "<$10k"),
    (50_000, "$10–50k"),
    (250_000, "$50–250k"),
    (1_000_000, "$250k–$1M"),
    (float("inf"), ">$1M"),
]


def _bucket(val: float) -> str:
    for cap, name in SIZE_BUCKETS:
        if val < cap:
            return name
    return SIZE_BUCKETS[-1][1]


def _rel_bucket(rel: str, title: str) -> str:
    t = (title or "").lower()
    r = (rel or "").lower()
    if "chief executive" in t or re.search(r"\bceo\b", t):
        return "CEO"
    if "chief financial" in t or re.search(r"\bcfo\b", t):
        return "CFO"
    if "officer" in r:
        return "Officer"
    if "10%" in r or "10 percent" in r:
        return "10% Owner"
    if "director" in r:
        return "Director"
    return "Other"


def _stats(xs: list[float]) -> dict:
    xs = [float(x) for x in xs if x is not None]
    if not xs:
        return {"n": 0, "mean": None, "median": None, "win_rate": None,
                "p25": None, "p75": None, "min": None, "max": None}
    xs.sort()
    n = len(xs)
    mean = sum(xs) / n
    mid = n // 2
    median = xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2
    p25 = xs[max(0, (n * 25) // 100)]
    p75 = xs[max(0, min(n - 1, (n * 75) // 100))]
    wins = sum(1 for x in xs if x > 0)
    return {
        "n": n,
        "mean": round4(mean),
        "median": round4(median),
        "win_rate": round4(wins / n),
        "p25": round4(p25),
        "p75": round4(p75),
        "min": round4(xs[0]),
        "max": round4(xs[-1]),
    }


def analyze(positions: list[dict]) -> dict:
    open_p = [p for p in positions if p.get("status") == "open"]
    pending = [p for p in positions if p.get("status") == "awaiting_entry"]
    noprice = [p for p in positions if p.get("status") == "no_price"]
    rois = [p["roi"] for p in open_p if p.get("roi") is not None]
    pnls = [p["pnl"] for p in open_p if p.get("pnl") is not None]
    deployed = STAKE * len(open_p)
    value = sum(p["mtm"] for p in open_p if p.get("mtm") is not None)
    pnl = sum(pnls) if pnls else 0.0

    def group(keyfn):
        g = defaultdict(list)
        for p in open_p:
            if p.get("roi") is None:
                continue
            g[keyfn(p)].append(p["roi"])
        return [{"k": k, **_stats(vs)} for k, vs in sorted(g.items(), key=lambda kv: -len(kv[1]))]

    horizons = {}
    for h in ("r0", "r1", "r5", "r21", "r63", "gap"):
        horizons[h] = _stats([p.get(h) for p in open_p])

    findings = []
    roi_s = _stats(rois)
    if roi_s["n"]:
        findings.append({
            "id": "equal_weight_roi",
            "title": "Equal-weight $10k follow-the-buy",
            "text": (
                f"{roi_s['n']} entered paper longs, each ${STAKE:,.0f}. "
                f"Mean ROI {roi_s['mean']*100:.2f}% · median {roi_s['median']*100:.2f}% · "
                f"win rate {roi_s['win_rate']*100:.1f}% (ROI > 0). "
                f"Total P&L ${pnl:,.0f} on ${deployed:,.0f} deployed "
                f"({(pnl/deployed*100) if deployed else 0:.2f}%)."
            ),
        })
    if horizons["gap"]["n"]:
        g = horizons["gap"]
        findings.append({
            "id": "entry_gap",
            "title": "Insider fill vs our next-open entry",
            "text": (
                f"Mean gap {g['mean']*100:.2f}% (positive = we paid more than the insider). "
                f"Median {g['median']*100:.2f}%. This is the move between the insider's "
                f"transaction and the first open after the Form 4 hit EDGAR."
            ),
        })
    for h, label in (("r0", "entry-day close"), ("r1", "+1 session"),
                     ("r5", "+5 sessions"), ("r21", "+21 sessions (~1 month)")):
        s = horizons[h]
        if s["n"]:
            findings.append({
                "id": f"horizon_{h}",
                "title": f"Return to {label}",
                "text": (
                    f"n={s['n']} · mean {s['mean']*100:.2f}% · median {s['median']*100:.2f}% · "
                    f"win rate {s['win_rate']*100:.1f}%."
                ),
            })
    rel = group(lambda p: _rel_bucket(p.get("rel") or "", p.get("title") or ""))
    if rel:
        bits = [f"{x['k']}: mean {x['mean']*100:.2f}% (n={x['n']})" for x in rel]
        findings.append({
            "id": "by_role",
            "title": "ROI by insider role",
            "text": " · ".join(bits),
        })
    sz = group(lambda p: _bucket(p.get("insider_val") or 0))
    if sz:
        bits = [f"{x['k']}: mean {x['mean']*100:.2f}% (n={x['n']})" for x in sz]
        findings.append({
            "id": "by_size",
            "title": "ROI by insider purchase size",
            "text": " · ".join(bits),
        })
    delays = [p.get("delay_td_fd") for p in open_p if p.get("delay_td_fd") is not None]
    if delays:
        findings.append({
            "id": "filing_delay",
            "title": "Transaction date → filing date",
            "text": (
                f"Mean {(sum(delays)/len(delays)):.2f} calendar days "
                f"(n={len(delays)}; Form 4 is due within two business days)."
            ),
        })
    if pending:
        findings.append({
            "id": "pending",
            "title": "Awaiting next session",
            "text": (
                f"{len(pending)} buy signal(s) are filed but the next regular-session "
                f"open has not printed yet — they will enter automatically on the next run."
            ),
        })
    if noprice:
        findings.append({
            "id": "no_price",
            "title": "No verified market price",
            "text": (
                f"{len(noprice)} signal(s) had a ticker that Yahoo/Stooq/Nasdaq did not "
                f"return bars for (often foreign, delisted, or a non-standard symbol). "
                f"They are kept in the book with status no_price and are never filled."
            ),
        })

    best = sorted(open_p, key=lambda p: p.get("roi") if p.get("roi") is not None else -999, reverse=True)
    worst = sorted(open_p, key=lambda p: p.get("roi") if p.get("roi") is not None else 999)

    return {
        "stake": STAKE,
        "counts": {
            "signals": len(positions),
            "open": len(open_p),
            "awaiting_entry": len(pending),
            "no_price": len(noprice),
        },
        "capital": {
            "deployed": round2(deployed),
            "value": round2(value),
            "pnl": round2(pnl),
            "roi": round4((value / deployed) - 1.0) if deployed else None,
        },
        "roi": roi_s,
        "horizons": horizons,
        "by_role": rel,
        "by_size": sz,
        "best": [_brief(p) for p in best[:15]],
        "worst": [_brief(p) for p in worst[:15]],
        "findings": findings,
    }


def _brief(p: dict) -> dict:
    return {
        "id": p.get("id"), "tk": p.get("tk"), "co": p.get("co"),
        "insider": p.get("insider"), "fd": p.get("fd"), "entry_d": p.get("entry_d"),
        "insider_val": p.get("insider_val"), "entry_px": p.get("entry_px"),
        "last_px": p.get("last_px"), "roi": p.get("roi"), "pnl": p.get("pnl"),
        "rel": p.get("rel"), "title": p.get("title"),
    }


def equity_curve(positions: list[dict], bars_by_tk: dict[str, list[dict]], asof: str) -> list[dict]:
    """Weekday MTM of all entered $10k longs (ffill close)."""
    open_p = [p for p in positions if p.get("status") == "open" and p.get("entry_d") and p.get("shares")]
    if not open_p:
        return []
    start = min(p["entry_d"] for p in open_p)
    end = asof
    d0, d1 = _parse_ymd(start), _parse_ymd(end)
    if not d0 or not d1 or d1 < d0:
        return []

    # Per ticker: date -> close, plus a sorted date list for ffill.
    close_map: dict[str, dict[str, float]] = {}
    for tk, bars in bars_by_tk.items():
        m = {}
        for b in bars:
            if b.get("c"):
                m[b["d"]] = float(b["c"])
        close_map[tk] = m

    out = []
    d = d0
    last_close: dict[str, float] = {}
    while d <= d1:
        ds = d.isoformat()
        if d.weekday() < 5:
            deployed = 0.0
            value = 0.0
            n = 0
            for p in open_p:
                if p["entry_d"] > ds:
                    continue
                tk = p["tk"]
                if ds in close_map.get(tk, {}):
                    last_close[tk] = close_map[tk][ds]
                px = last_close.get(tk) or fnum(p.get("last_px"))
                if not px:
                    continue
                deployed += STAKE
                value += p["shares"] * px
                n += 1
            if n:
                out.append({
                    "d": ds,
                    "n": n,
                    "deployed": round2(deployed),
                    "value": round2(value),
                    "pnl": round2(value - deployed),
                    "roi": round4((value / deployed) - 1.0) if deployed else None,
                })
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

PAPER_COLS = [
    "id", "status", "fd", "td", "entry_d", "tk", "co", "insider", "rel", "title",
    "insider_sh", "insider_px", "insider_val", "lots",
    "entry_px", "entry_src", "gap", "shares", "stake",
    "last_d", "last_px", "mtm", "pnl", "roi",
    "r0", "r1", "r5", "r21", "r63",
    "delay_td_fd", "delay_fd_entry", "hold_sessions",
    "acc", "form", "amend", "icik", "pcik", "sec",
]


def write_outputs(site_data: str, positions: list[dict], summary: dict, equity: list[dict]):
    outdir = os.path.join(site_data, "paper")
    os.makedirs(outdir, exist_ok=True)
    # Newest first for the dashboard table.
    pos = sorted(positions, key=lambda p: (p.get("fd") or "", p.get("td") or "", p.get("tk") or ""), reverse=True)
    with open(os.path.join(outdir, "positions.json"), "w", encoding="utf-8") as f:
        json.dump(pos, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(outdir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(outdir, "equity.json"), "w", encoding="utf-8") as f:
        json.dump(equity, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(outdir, "positions.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(PAPER_COLS)
        for p in pos:
            w.writerow([p.get(c, "") for c in PAPER_COLS])


def load_price_cache(cache_dir: str) -> dict[str, dict]:
    cache = {}
    if not os.path.isdir(cache_dir):
        return cache
    for fn in os.listdir(cache_dir):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(cache_dir, fn)
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
            tk = doc.get("tk") or fn[:-5]
            cache[tk] = doc
        except (OSError, json.JSONDecodeError):
            continue
    return cache


def save_price_cache(cache_dir: str, tk: str, bars: list[dict], src: str):
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{yahoo_symbol(tk)}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"tk": tk, "src": src, "fetched": iso_now(), "bars": bars},
                  f, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(data_dir: str, site_data: str, cache_dir: str, force_prices: bool = False) -> int:
    rows = load_dataset(data_dir)
    log(f"Loaded {len(rows)} insider-trade rows")
    signals = extract_signals(rows)
    log(f"Open-market common-equity buy signals: {len(signals)}")

    asof = utcnow().date().isoformat()
    cache = load_price_cache(cache_dir)
    bars_by_tk: dict[str, list[dict]] = {}
    src_by_tk: dict[str, str] = {}
    tickers = sorted({s["tk"] for s in signals})
    log(f"Unique tickers to price: {len(tickers)}")

    for i, tk in enumerate(tickers, 1):
        cached = cache.get(tk) or cache.get(yahoo_symbol(tk))
        fresh = False
        if cached and not force_prices:
            fetched = (cached.get("fetched") or "")[:10]
            if fetched == asof and cached.get("bars"):
                bars_by_tk[tk] = cached["bars"]
                src_by_tk[tk] = cached.get("src") or "cache"
                fresh = True
        if not fresh:
            bars, src = fetch_bars(tk)
            if bars:
                bars_by_tk[tk] = bars
                src_by_tk[tk] = src
                save_price_cache(cache_dir, tk, bars, src)
            elif cached and cached.get("bars"):
                bars_by_tk[tk] = cached["bars"]
                src_by_tk[tk] = "cache-stale"
                log(f"  {tk}: live fetch missed, using stale cache ({len(cached['bars'])} bars)")
            else:
                bars_by_tk[tk] = []
                src_by_tk[tk] = "none"
                log(f"  {tk}: no price source")
        if i % 25 == 0 or i == len(tickers):
            log(f"  priced {i}/{len(tickers)}")

    positions = []
    for s in signals:
        bars = bars_by_tk.get(s["tk"]) or []
        pos = simulate_one(s, bars, asof)
        pos["entry_src"] = src_by_tk.get(s["tk"]) if pos["status"] == "open" else src_by_tk.get(s["tk"])
        pos["price_src"] = src_by_tk.get(s["tk"])
        positions.append(pos)

    summary = analyze(positions)
    summary["generated"] = iso_now()
    summary["asof"] = asof
    summary["rule"] = {
        "stake_usd": STAKE,
        "signal": "Form 4/5 non-derivative transaction code P (open-market or private purchase) of common equity / ADR",
        "entry": "regular-session open of the first trading day strictly after the SEC filing date",
        "exit": "none — positions stay open for forward testing",
        "mark": "latest regular-session close",
        "size": f"fixed ${STAKE:,.0f} notional per insider-buy filing (fractional shares)",
        "lookahead": "none — filing-day prices are never used as the fill",
        "costs": "none modelled (no commission, no bid/ask beyond using the next open)",
        "prices": "Yahoo Finance daily bars, Stooq fallback, Nasdaq historical fallback",
    }
    equity = equity_curve(positions, bars_by_tk, asof)
    summary["equity_points"] = len(equity)

    write_outputs(site_data, positions, summary, equity)
    n_open = summary["counts"]["open"]
    log(f"Paper book: {len(positions)} signals, {n_open} open, "
        f"deployed ${summary['capital']['deployed']:,.0f}, "
        f"MTM ${summary['capital']['value']:,.0f}, "
        f"P&L ${summary['capital']['pnl']:,.0f}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Paper-trade insider open-market buys at $10k each")
    ap.add_argument("--data-dir", default=os.path.join(HERE, "data"))
    ap.add_argument("--site-data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--cache-dir", default=os.path.join(HERE, "data", "prices"))
    ap.add_argument("--force-prices", action="store_true")
    ap.add_argument("--rate", type=float, default=MKT_RATE)
    args = ap.parse_args()
    global THROTTLE
    THROTTLE = Throttle(max(0.5, min(args.rate, 8.0)))
    os.makedirs(args.data_dir, exist_ok=True)
    os.makedirs(args.site_data, exist_ok=True)
    sys.exit(run(args.data_dir, args.site_data, args.cache_dir, args.force_prices))


if __name__ == "__main__":
    main()
