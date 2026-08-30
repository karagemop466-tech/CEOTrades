#!/usr/bin/env python3
"""
CEOTrades backtest — place a verified paper trade for EVERY tracked insider
purchase across the whole collected store, with a real, observed entry AND a
real, observed exit.

What it does
------------
For every qualifying Form 4/5 code-P non-derivative common-equity purchase
(signals are VWAP-aggregated one per SEC accession+ticker, exactly like the
forward book):

  ENTRY  = regular-session OPEN of the first trading day STRICTLY after the
           SEC filing date (the first price a public follower could obtain).
  EXIT   = regular-session CLOSE 252 sessions later (about one trading year).
           The forward book leaves positions open; the backtest closes them so
           entry price/date AND exit price/date are both verified market data.

No fabrication rules
--------------------
  * Entry is never at or before the filing date and never the insider's price.
  * An exit exists only once the +252 session has actually printed a close.
    Positions younger than 252 sessions are reported `open` (exit blank),
    never estimated.
  * A ticker with no retrievable history that reaches the entry session is
    reported `no_price` and COUNTED — never silently dropped.
  * Delisted names whose series ends before the +252 session are exited at the
    last observed regular-session close and flagged `exit_last_observed`
    (delisted/merged/acquired — review flag), again a real price/date.

Prices: full-history daily bars, Yahoo chart v8 with Stooq fallback (the
forward engine's sources). Bars are cached per ticker so re-runs are cheap.

Outputs (data/backtest/):
  positions.json / .csv      every signal with entry+exit verification fields
  summary.json               counts, coverage, realised ROI/P&L stats
  winners.json / losers.json realised round-trip logs (verified exit only)
  coverage.json              line-by-line accounting: every P-row -> outcome

Standard library only. No manual input, no fabricated rows or prices.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import paper_trade as pt  # noqa: E402  (signals, bars, entry/horizon math, fetch)
import store  # noqa: E402

DATA_DIR = os.path.join(HERE, "data")
OUT_DIR = os.path.join(ROOT, "data", "backtest")
CACHE_DIR = os.path.join(DATA_DIR, "prices_backtest")

EXIT_SESSIONS = 252  # about one trading year; the documented long horizon


def log(m):
    print(m, flush=True)


# ---------------------------------------------------------------------------
# Full-history bars (the forward engine fetches ~3y; a backtest needs the
# entry window of old filings, so request the longest range Yahoo supports and
# rely on Stooq's full-history CSV as the fallback).
# ---------------------------------------------------------------------------

def fetch_yahoo_full(tk: str):
    import urllib.parse
    import urllib.request
    sym = urllib.parse.quote(pt.yahoo_symbol(tk), safe="-")
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        # range=max = the full listing history; events=div,split for adjustment.
        url = f"https://{host}/v8/finance/chart/{sym}?interval=1d&range=max&events=div%2Csplit"
        raw = _http_get(url)
        if not raw:
            continue
        bars = pt._bars_from_yahoo_json(raw)
        if bars:
            return bars, f"yahoo:{host.split('.')[0]}"
    return None, "yahoo"


def _http_get(url: str, retries: int = 3, timeout: int = 40):
    last = None
    for attempt in range(retries + 1):
        pt.THROTTLE.wait()
        req = pt.urllib.request.Request(url, headers={
            "User-Agent": pt.UA_MKT,
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "close",
        })
        try:
            with pt.urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                return raw
        except pt.urllib.error.HTTPError as e:
            if e.code in (404, 400):
                return None
            last = f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001 - network variability
            last = f"{type(e).__name__}: {e}"
        if attempt < retries:
            time.sleep(min(5 * (2 ** attempt), 60))
    log(f"    ! full-history fetch failed: {last}")
    return None


def fetch_bars_full(tk: str):
    bars, src = fetch_yahoo_full(tk)
    if bars:
        return bars, src
    # Stooq returns the full listing history on the daily endpoint.
    bars, src = pt.fetch_stooq(tk)
    if bars:
        return bars, "stooq"
    bars, src = pt.fetch_nasdaq(tk)
    if bars:
        return bars, src
    return [], "none"


# ---------------------------------------------------------------------------
# Price cache (per ticker, full history) — cumulative across runs.
# ---------------------------------------------------------------------------

def _cache_path(tk: str) -> str:
    return os.path.join(CACHE_DIR, f"{tk}.json")


def load_cached(tk: str):
    p = _cache_path(tk)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                doc = json.load(f)
            return doc.get("bars") or [], doc.get("src") or "cache"
        except (OSError, json.JSONDecodeError):
            pass
    return None, None


def save_cached(tk: str, bars, src: str):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(tk), "w", encoding="utf-8") as f:
        json.dump({"tk": tk, "src": src, "fetched": pt.iso_now()[:10],
                   "bars": bars}, f, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Backtest simulation (pure, given a signal + bars + asof)
# ---------------------------------------------------------------------------

def simulate_backtest(sig: dict, bars: list, asof: str, full_history: bool = True) -> dict:
    """Entry = next open after filing; exit = +252 session close (or last bar).

    full_history=True means `bars` is the complete listing history (Yahoo
    range=max / Stooq full), so a mature name whose bars end early genuinely
    stopped trading. False (partial/cached/offline window) means an early end
    is a coverage gap and the position stays open."""
    out = dict(sig)
    out.update({
        "stake": pt.STAKE,
        "status": "no_price",
        "entry_d": None, "entry_px": None, "shares": None, "entry_src": None,
        "exit_d": None, "exit_px": None, "exit_roi": None, "exit_pnl": None,
        "exit_status": None, "exit_sessions": None,
        "last_d": None, "last_px": None, "roi_open": None, "pnl_open": None,
        "gap": None, "price_src": None,
        "entry_rule": "open_first_session_strictly_after_filing",
        "entry_rule_status": "no_price",
        "edgar_url": edgar_url(sig.get("acc"), sig.get("icik")),
        "yahoo_history_url": (f"https://finance.yahoo.com/quote/{pt.yahoo_symbol(sig.get('tk') or '')}/history"
                              if sig.get("tk") else ""),
        "review_note": None,
    })
    fd = sig.get("fd") or ""
    usable = [b for b in bars if b["d"] <= asof]
    if not usable:
        out["entry_rule_status"] = "no_price"
        out["review_note"] = "no market bars retrieved for this ticker (delisted/uncovered) — no entry inferred"
        return out

    nxt = pt.next_open_after(usable, fd)
    if nxt is None:
        if usable[-1]["d"] < fd:
            out["entry_rule_status"] = "no_bars_after_filing"
            out["review_note"] = (f"bar series ends {usable[-1]['d']}, before filing {fd} — "
                                  "likely delisted before the signal; no entry inferred")
        else:
            out["status"] = "awaiting_entry"
            out["entry_rule_status"] = "awaiting_entry"
        return out

    entry_d, entry_px, _entry_close, idx = nxt
    import datetime as _dt
    try:
        gap_days = (_dt.date.fromisoformat(entry_d) - _dt.date.fromisoformat(fd)).days
    except ValueError:
        gap_days = None
    if gap_days is not None and gap_days > pt.ENTRY_MAX_GAP_DAYS:
        out["entry_rule_status"] = "entry_window_missing"
        out["review_note"] = (f"first open is {gap_days} days after filing; price history does not "
                              "reach the entry session — no entry inferred")
        return out
    if not entry_px or entry_px <= 0:
        return out

    shares = pt.STAKE / entry_px
    out["status"] = "open"
    out["entry_d"] = entry_d
    out["entry_px"] = pt.round4(entry_px)
    out["entry_src"] = sig.get("price_src")
    out["shares"] = pt.round4(shares)
    out["entry_rule_status"] = "verified" if entry_d > fd else "invalid"
    if sig.get("insider_px"):
        out["gap"] = pt.round4(entry_px / float(sig["insider_px"]) - 1.0)

    # Mark-to-market at the latest observed close (forward view).
    last = usable[-1]
    lpx = pt.fnum(last.get("c"))
    if lpx and lpx > 0:
        out["last_d"] = last["d"]
        out["last_px"] = pt.round4(lpx)
        mtm = shares * lpx
        out["roi_open"] = pt.round4(mtm / pt.STAKE - 1.0)
        out["pnl_open"] = pt.round2(mtm - pt.STAKE)

    # EXIT: the +252 session close when it has printed ...
    import datetime as _dt2
    ex_px, ex_d = pt.horizon_close(usable, idx, EXIT_SESSIONS)
    sessions_after = len(usable) - 1 - idx
    series_end = usable[-1]["d"]
    out["exit_sessions"] = EXIT_SESSIONS
    # A name is "mature" (old enough for a +252 exit to exist) when the entry
    # is ~13+ months before the as-of date.
    mature = False
    try:
        mature = (_dt2.date.fromisoformat(asof) - _dt2.date.fromisoformat(entry_d)).days >= 380
    except ValueError:
        mature = False
    # `full_history` is set by the caller for full-range fetches (Yahoo max /
    # Stooq), where a series ending well before as-of means the name actually
    # stopped trading (delisted/acquired). A partial/cached fetch ending early
    # is a coverage gap, never a delisting — so such positions stay open.
    full_history = bool(full_history)

    if ex_px is not None and ex_px > 0:
        val = shares * ex_px
        out["exit_d"] = ex_d
        out["exit_px"] = pt.round4(ex_px)
        out["exit_roi"] = pt.round4(val / pt.STAKE - 1.0)
        out["exit_pnl"] = pt.round2(val - pt.STAKE)
        out["exit_status"] = "exited_252"
    else:
        last_after = None
        for b in usable[idx + 1:]:
            if b.get("c"):
                last_after = b
        # Delisted/acquired: a MATURE name whose full-history series genuinely
        # ends before the mark date and before 252 sessions. Exit at the last
        # observed close (a real price/date) and flag. A partial/offline fetch
        # never triggers this: such a name stays open, not wrongly closed.
        if (mature and full_history and sessions_after < EXIT_SESSIONS
                and last_after is not None and series_end < asof):
            ex_px2 = float(last_after["c"])
            val = shares * ex_px2
            out["exit_d"] = last_after["d"]
            out["exit_px"] = pt.round4(ex_px2)
            out["exit_roi"] = pt.round4(val / pt.STAKE - 1.0)
            out["exit_pnl"] = pt.round2(val - pt.STAKE)
            out["exit_status"] = "exit_last_observed"
            out["exit_sessions"] = sessions_after
            out["review_note"] = ("mature position but price series ended before 252 sessions "
                                  f"(delisting/M&A/coverage end); exited at last observed close "
                                  f"{last_after['d']} — review corporate action")
        else:
            out["exit_status"] = "open"
            out["exit_sessions"] = sessions_after
            note = f"{sessions_after} sessions since entry (<{EXIT_SESSIONS}); one-year exit not yet observable"
            if not full_history:
                note += "; price history from a partial/offline cache — re-fetch on a connected run"
            out["review_note"] = note

    # Corporate-action straddle guard (same rule as the forward engine): a
    # multi-bagger/wipeout move over very few sessions implies a split/takeover
    # on a different share base, so the open-mark ROI must not be trusted as a
    # per-dollar return until confirmed. Observed prices are kept; ROI flagged.
    out["roi_review"] = False
    sessions = (len(usable) - 1 - idx) if out.get("entry_d") else None
    if out.get("roi_open") is not None and sessions is not None and (
            (out["roi_open"] > 2.0 or out["roi_open"] < -0.80) and sessions <= 90):
        out["roi_review"] = True
        out["roi_review_reason"] = (
            f"Open-mark return {out['roi_open']} over {sessions} sessions is implausible without a "
            "corporate action (reverse split/takeover); verify share basis before treating as a gain.")
    if out.get("exit_roi") is not None and out.get("exit_status") == "exit_last_observed" \
            and (out["exit_roi"] > 3.0 or out["exit_roi"] < -0.90):
        out["roi_review"] = True
        out["roi_review_reason"] = (out.get("roi_review_reason") or
                                    "") + " Last-observed (delisting) exit shows an extreme return; confirm the corporate-action share basis."
    return out


def edgar_url(acc: str, icik: str) -> str:
    a = "".join(c for c in str(acc or "") if c.isdigit() or c == "-")
    if not a:
        return ""
    plain = a.replace("-", "")
    cik = "".join(c for c in str(icik or "") if c.isdigit()).lstrip("0") or plain[:10].lstrip("0")
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{plain}/{a}-index.htm"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _stats(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return {"n": 0, "mean": None, "median": None, "win": None,
                "p25": None, "p75": None, "min": None, "max": None}
    def pct(p):
        return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]
    n = len(xs)
    return {
        "n": n,
        "mean": pt.round4(sum(xs) / n),
        "median": pt.round4(pct(0.5)),
        "win": pt.round4(sum(1 for x in xs if x > 0) / n),
        "p25": pt.round4(pct(0.25)), "p75": pt.round4(pct(0.75)),
        "min": pt.round4(xs[0]), "max": pt.round4(xs[-1]),
    }


def run(from_year: int, offline: bool, max_tickers: int, asof: str) -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)

    log(f"Backtest: streaming store rows (filing years >= {from_year}) …")
    rows = []
    for r in store.iter_rows(DATA_DIR):
        fd = r.get("fd") or ""
        if len(fd) >= 4 and fd[:4].isdigit() and int(fd[:4]) >= from_year:
            rows.append(r)
    log(f"  {len(rows):,} trade rows in window")

    signals = pt.extract_signals(rows)
    log(f"  {len(signals):,} code-P buy signals (one per SEC accession+ticker)")

    # Coverage: every P-row is accounted for with the same audit logic as build.
    coverage = {"p_rows": 0, "derivative": 0, "no_ticker": 0, "no_share_or_price": 0,
                "not_common_equity": 0, "signals": len(signals)}
    for r in rows:
        if (r.get("code") or "") != "P":
            continue
        coverage["p_rows"] += 1
        tk = (r.get("tk") or "").strip().upper()
        sh, px = r.get("sh"), r.get("px")
        if r.get("der"):
            coverage["derivative"] += 1
        elif not tk:
            coverage["no_ticker"] += 1
        elif not (sh and sh > 0 and px and px > 0):
            coverage["no_share_or_price"] += 1
        elif not pt.is_common_equity(r.get("sec") or "", r.get("der")):
            coverage["not_common_equity"] += 1

    by_tk = defaultdict(list)
    for s in signals:
        by_tk[s["tk"]].append(s)
    tickers = sorted(by_tk)
    if max_tickers:
        tickers = tickers[:max_tickers]
    log(f"  {len(tickers):,} unique tickers to price "
        f"({len(signals) - sum(len(by_tk[t]) for t in tickers):,} signals skipped by --max-tickers)")

    positions, priced, nopx = [], 0, 0
    src_counts = defaultdict(int)
    for i, tk in enumerate(tickers, 1):
        bars, src = load_cached(tk)
        if not bars:
            if offline:
                bars, src = [], "offline_no_cache"
            else:
                bars, src = fetch_bars_full(tk)
                if bars:
                    save_cached(tk, bars, src)
        if bars:
            priced += 1
        else:
            nopx += 1
        src_counts[src] += 1
        for s in by_tk[tk]:
            s["price_src"] = src
            positions.append(simulate_backtest(s, bars, asof, full_history=not offline))
        if i % 50 == 0 or i == len(tickers):
            log(f"  priced {i}/{len(tickers)} tickers ({priced} with bars, {nopx} without)")

    # Signals skipped by --max-tickers are still emitted as no_price so the
    # total signal count is preserved.
    for tk in [t for t in sorted(by_tk) if t not in tickers]:
        for s in by_tk[tk]:
            s["price_src"] = "max_tickers_skipped"
            p = simulate_backtest(s, [], asof, full_history=not offline)
            p["review_note"] = "not priced this run (--max-tickers cap); counted, not dropped"
            positions.append(p)

    positions.sort(key=lambda p: (p.get("fd") or "", p.get("tk") or ""))

    exited = [p for p in positions if p.get("exit_status") in ("exited_252", "exit_last_observed")]
    openp = [p for p in positions if p.get("exit_status") == "open"]
    noprice = [p for p in positions if p.get("entry_rule_status") != "verified"]
    winners = [p for p in exited if (p.get("exit_pnl") or 0) > 0]
    losers = [p for p in exited if (p.get("exit_pnl") or 0) <= 0]
    delisted = [p for p in exited if p.get("exit_status") == "exit_last_observed"]

    realised = _stats([p.get("exit_roi") for p in exited])
    openret = _stats([p.get("roi_open") for p in positions if p.get("roi_open") is not None])

    def brief(p):
        return {k: p.get(k) for k in (
            "id", "tk", "co", "insider", "rel", "title", "fd", "td", "acc", "icik",
            "insider_px", "insider_sh", "insider_val", "entry_d", "entry_px", "shares",
            "gap", "exit_d", "exit_px", "exit_status", "exit_sessions", "exit_roi",
            "exit_pnl", "last_d", "last_px", "roi_open", "pnl_open", "status",
            "entry_rule_status", "price_src", "edgar_url", "yahoo_history_url", "review_note")}

    summary = {
        "generated": pt.iso_now(),
        "asof": asof,
        "rule": {
            "signal": "Form 4/5 code P, non-derivative common equity/ADR, shares>0 & price>0; one $10,000 position per SEC accession+ticker",
            "entry": "regular-session OPEN of first trading day strictly after the SEC filing date",
            "exit": "regular-session CLOSE 252 sessions (~1 trading year) after entry; delisted names exit at last observed close and are flagged",
            "costs": "none modelled (no commission/spread/slippage)",
            "prices": "Yahoo Finance full-history daily bars, Stooq then Nasdaq fallback; cached per ticker",
            "no_lookahead": "entry never at/before filing date and never the insider price; exit only from observed sessions",
        },
        "window": {"from_year": from_year},
        "counts": {
            "trade_rows": len(rows),
            "signals": len(signals),
            "positions": len(positions),
            "verified_entry": len([p for p in positions if p.get("entry_rule_status") == "verified"]),
            "exited": len(exited),
            "exited_252": len([p for p in exited if p.get("exit_status") == "exited_252"]),
            "exit_last_observed_delisted": len(delisted),
            "open_under_252": len(openp),
            "no_price_or_pending": len(noprice),
        },
        "coverage": coverage,
        "price_sources": dict(sorted(src_counts.items())),
        "capital": {
            "deployed": pt.round2(pt.STAKE * len([p for p in positions if p.get("entry_rule_status") == "verified"])),
            "realised_pnl": pt.round2(sum(p.get("exit_pnl") or 0 for p in exited)),
            "realised_roi_mean": realised["mean"],
            "win_rate": realised["win"],
        },
        "realised_exit_roi": realised,
        "open_mark_roi": openret,
        "by_exit_year": _by_year(exited),
        "winners_n": len(winners), "losers_n": len(losers),
    }

    def dump(name, obj):
        with open(os.path.join(OUT_DIR, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=1)

    dump("positions.json", [brief(p) for p in positions])
    dump("summary.json", summary)
    dump("winners.json", {"generated": summary["generated"], "count": len(winners),
                          "rows": [brief(p) for p in sorted(winners, key=lambda x: -(x.get("exit_roi") or 0))]})
    dump("losers.json", {"generated": summary["generated"], "count": len(losers),
                         "rows": [brief(p) for p in sorted(losers, key=lambda x: (x.get("exit_roi") or 0))]})
    dump("coverage.json", {"generated": summary["generated"], "coverage": coverage,
                           "price_sources": summary["price_sources"],
                           "positions": [{"id": p.get("id"), "tk": p.get("tk"), "fd": p.get("fd"),
                                          "entry_rule_status": p.get("entry_rule_status"),
                                          "exit_status": p.get("exit_status"),
                                          "price_src": p.get("price_src"),
                                          "review_note": p.get("review_note")}
                                         for p in positions]})

    # CSV export of every position.
    cols = ["id", "tk", "co", "insider", "rel", "fd", "td", "acc", "icik",
            "insider_px", "insider_sh", "insider_val", "entry_d", "entry_px", "shares",
            "gap", "exit_d", "exit_px", "exit_status", "exit_sessions", "exit_roi",
            "exit_pnl", "last_d", "last_px", "roi_open", "status",
            "entry_rule_status", "price_src", "edgar_url", "yahoo_history_url", "review_note"]
    with open(os.path.join(OUT_DIR, "positions.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for p in positions:
            w.writerow(brief(p))

    log("Backtest complete:")
    log(f"  signals {len(signals)} | verified entry {summary['counts']['verified_entry']} | "
        f"exited {len(exited)} (252-session {summary['counts']['exited_252']}, "
        f"last-observed/delisted {len(delisted)}) | open {len(openp)} | no-price {len(noprice)}")
    log(f"  realised mean ROI {realised['mean']} over {realised['n']} round trips; "
        f"win rate {realised['win']}")
    return 0


def _by_year(exited):
    yrs = defaultdict(list)
    for p in exited:
        y = (p.get("exit_d") or "????")[:4]
        yrs[y].append(p.get("exit_roi"))
    out = []
    for y in sorted(yrs):
        xs = [x for x in yrs[y] if x is not None]
        if not xs:
            continue
        out.append({"exit_year": y, "n": len(xs),
                    "mean_roi": pt.round4(sum(xs) / len(xs)),
                    "win_rate": pt.round4(sum(1 for x in xs if x > 0) / len(xs))})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Backtest every tracked insider buy with verified entry+exit prices.")
    ap.add_argument("--from-year", type=int, default=2006, help="first filing year to backtest")
    ap.add_argument("--offline", action="store_true", help="use only cached bars; never hit the network")
    ap.add_argument("--max-tickers", type=int, default=0, help="cap tickers priced this run (0 = all)")
    ap.add_argument("--asof", default=pt.utcnow().date().isoformat())
    args = ap.parse_args()
    return run(args.from_year, args.offline, args.max_tickers, args.asof)


if __name__ == "__main__":
    raise SystemExit(main())
