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
  insider_activity.json   buyer/seller overlap + reported holding estimates
  insider_activity.csv.gz full buyer/seller/holding activity export
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
import runlog  # noqa: E402
import audit as audit_mod  # noqa: E402
import irregularities as irregularities_mod  # noqa: E402

RECENT_DAYS = 120        # window for the front-page tape
RECENT_CAP = 6000        # hard cap on tape rows
COMPANY_CAP = 40         # recent transactions kept per company detail view
CO_BUCKETS = 64          # per-company files are bucketed to keep file count sane
INSIDER_CAP = 4000       # insiders published in the browsable table
INSIDER_ACTIVITY_CAP = 12000  # insider/company pairs published as JSON
INSIDER_PORTFOLIO_CAP = 8000  # per-insider cross-issuer portfolio rows published as JSON
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


def edgar_url(acc: str, issuer_cik: str = "") -> str:
    """SEC filing-index URL for manual review.

    Ownership-form accession prefixes often identify the reporting owner or
    filing agent rather than the issuer, so prefer the parsed issuer CIK.
    """
    a = "".join(ch for ch in str(acc or "") if ch.isdigit() or ch == "-")
    if not a:
        return ""
    plain = a.replace("-", "")
    cik = "".join(ch for ch in str(issuer_cik or "") if ch.isdigit()).lstrip("0")
    if not cik:
        cik = plain[:10].lstrip("0")
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{plain}/{a}-index.htm"


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
    activity: dict[tuple, dict] = {}
    activity_tickers: set[str] = set()

    cutoff = (datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%d")
    first_fd, last_fd = "9999-99-99", ""

    for row_idx, r in enumerate(store.iter_rows(data_dir), 1):
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

        # ---- per insider/company activity and reported holdings ----
        # These rows answer: did the same insider both buy and sell this issuer,
        # and what is the latest as-filed post-transaction holding we can quote?
        # The holding estimate is deliberately narrow: it uses SEC
        # SHRS_OWND_FOLWNG_TRANS values from the latest non-derivative common
        # equity row per direct/indirect ownership bucket. Missing values remain
        # null; nothing is inferred from prior trades.
        owner_key = (r.get("pcik") or r.get("in") or "").strip()
        issuer_key = (cik or tk or r.get("co") or "").strip()
        if owner_key and issuer_key:
            akey = (owner_key, issuer_key)
            a = activity.get(akey)
            if a is None:
                a = activity[akey] = {
                    "insider": r.get("in") or "", "pcik": r.get("pcik") or "",
                    "co": r.get("co") or "", "tk": tk, "icik": cik,
                    "rel": r.get("rel") or "", "title": r.get("title") or "",
                    "n": 0, "buy_n": 0, "sell_n": 0, "other_n": 0,
                    "buy_sh": 0.0, "sell_sh": 0.0,
                    "buy_v": 0.0, "sell_v": 0.0, "other_v": 0.0,
                    "first": "", "last": "", "first_buy": "", "last_buy": "",
                    "first_sell": "", "last_sell": "", "filings": {},
                    "holdings": {}, "recent": [],
                }
            if r.get("in"):
                a["insider"] = r.get("in") or a["insider"]
            if r.get("pcik") and not a["pcik"]:
                a["pcik"] = r.get("pcik") or ""
            if r.get("co"):
                a["co"] = r.get("co") or a["co"]
            if tk and not a["tk"]:
                a["tk"] = tk
            if cik and not a["icik"]:
                a["icik"] = cik
            if r.get("rel") and r.get("rel") != "Unknown":
                a["rel"] = r.get("rel") or a["rel"]
            if r.get("title") and not a["title"]:
                a["title"] = r.get("title") or ""
            a["n"] += 1
            if fd:
                if not a["first"] or fd < a["first"]:
                    a["first"] = fd
                if fd > a["last"]:
                    a["last"] = fd
            if acc:
                a["filings"][acc] = {"acc": acc, "icik": cik, "fd": fd,
                                      "form": r.get("form") or "",
                                      "amend": r.get("amend") or 0}
            if is_buy:
                a["buy_n"] += 1
                a["buy_sh"] += float(r.get("sh") or 0)
                a["buy_v"] += v
                if fd:
                    if not a["first_buy"] or fd < a["first_buy"]:
                        a["first_buy"] = fd
                    if fd > a["last_buy"]:
                        a["last_buy"] = fd
            elif is_sell:
                a["sell_n"] += 1
                a["sell_sh"] += float(r.get("sh") or 0)
                a["sell_v"] += v
                if fd:
                    if not a["first_sell"] or fd < a["first_sell"]:
                        a["first_sell"] = fd
                    if fd > a["last_sell"]:
                        a["last_sell"] = fd
            else:
                a["other_n"] += 1
                a["other_v"] += v
            a["recent"].append(compact(r))
            if len(a["recent"]) > 25:
                a["recent"].sort(key=lambda x: (x.get("fd") or "", x.get("td") or ""), reverse=True)
                del a["recent"][10:]

            af = r.get("af")
            if tk and af is not None and af >= 0 and not r.get("der") and is_common_equity(r.get("sec") or ""):
                activity_tickers.add(tk)
                hkey = ((r.get("sec") or "Common equity").strip().lower(),
                        r.get("di") or "", (r.get("nature") or "").strip().lower())
                marker = (fd or "", r.get("td") or "", acc or "", row_idx)
                old = a["holdings"].get(hkey)
                if old is None or marker >= old["marker"]:
                    a["holdings"][hkey] = {
                        "security": r.get("sec") or "Common equity",
                        "direct_indirect": r.get("di") or "",
                        "nature": r.get("nature") or "",
                        "shares": float(af), "fd": fd, "td": r.get("td") or "",
                        "acc": acc, "icik": cik, "marker": marker,
                    }

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
        "activity": activity, "activity_tickers": activity_tickers,
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


def latest_mark(bars, asof: str):
    """Latest close at or before asof from a validated daily-bar list."""
    if not bars:
        return None
    usable = [b for b in bars if b.get("d") and b["d"] <= asof and b.get("c")]
    if not usable:
        return None
    last = usable[-1]
    c = last.get("c")
    if not c or c <= 0:
        return None
    return {"last_d": last["d"], "last_px": r4(float(c))}


def write_insider_portfolios(agg, out_dir, marks_by_tk, target_year: int | None = None):
    """Per-insider rollup of reported holdings ACROSS issuers.

    Answers: how large is this insider's reported common-stock portfolio, based
    on their own past and present SEC filings? For every insider/company pair
    we take the same narrow holding estimate used by the flows page (latest
    as-filed SHRS_OWND_FOLWNG_TRANS for non-derivative common equity per
    direct/indirect bucket), then sum across the issuers that person actually
    filed against in the window, marking each issuer's stake at that issuer's
    latest close when a price exists.

    This is a floor, not a brokerage statement: Form 3/4/5 rows only cover the
    issuers the insider transacted in, buckets can overlap (direct + trust-held
    indirect), and unpriced tickers stay explicit gaps.
    """
    by_person: dict[str, dict] = {}
    for a in agg.get("activity", {}).values():
        owner_key = a.get("pcik") or a.get("insider") or ""
        if not owner_key:
            continue
        holdings = list(a.get("holdings", {}).values())
        reported = sum(float(h.get("shares") or 0) for h in holdings)
        mark = marks_by_tk.get(a.get("tk") or "") or {}
        value = None
        if holdings and mark.get("last_px") is not None:
            value = r2(reported * float(mark["last_px"]))
        latest_h = max(holdings, key=lambda h: (h.get("fd") or "", h.get("td") or "")) if holdings else None
        if holdings:
            vstatus = "priced" if value is not None else "shares_only_no_market_price"
        else:
            vstatus = "no_reported_post_transaction_common_shares"
        p = by_person.get(owner_key)
        if p is None:
            p = by_person[owner_key] = {
                "id": owner_key, "insider": a.get("insider") or "",
                "pcik": a.get("pcik") or "", "rel": a.get("rel") or "Unknown",
                "title": a.get("title") or "",
                "issuer_n": 0, "reported_shares": 0.0, "priced_value": 0.0,
                "priced_issuer_n": 0, "unpriced_issuer_n": 0,
                "buy_n": 0, "sell_n": 0, "buy_v": 0.0, "sell_v": 0.0,
                "overlap": False, "first": "", "last": "", "issuers": [],
            }
        if a.get("insider") and not p["insider"]:
            p["insider"] = a["insider"]
        if a.get("rel") and p["rel"] in ("", "Unknown"):
            p["rel"] = a["rel"]
        if a.get("title") and not p["title"]:
            p["title"] = a["title"]
        p["buy_n"] += a.get("buy_n", 0)
        p["sell_n"] += a.get("sell_n", 0)
        p["buy_v"] += a.get("buy_v", 0.0)
        p["sell_v"] += a.get("sell_v", 0.0)
        if a.get("buy_n", 0) > 0 and a.get("sell_n", 0) > 0:
            p["overlap"] = True
        if a.get("first") and (not p["first"] or a["first"] < p["first"]):
            p["first"] = a["first"]
        if a.get("last") and (not p["last"] or a["last"] > p["last"]):
            p["last"] = a["last"]
        p["issuers"].append({
            "co": a.get("co") or "", "tk": a.get("tk") or "", "icik": a.get("icik") or "",
            "n": a.get("n", 0), "buy_n": a.get("buy_n", 0), "sell_n": a.get("sell_n", 0),
            "buy_v": r2(a.get("buy_v", 0.0)), "sell_v": r2(a.get("sell_v", 0.0)),
            "reported_shares": r4(reported) if holdings else None,
            "holding_value": value,
            "valuation_status": vstatus,
            "latest_holding_fd": (latest_h or {}).get("fd") or "",
            "latest_filing_fd": a.get("last") or "",
            "latest_filing_acc": (latest_h or {}).get("acc") or "",
            "edgar_url": edgar_url((latest_h or {}).get("acc") or "", a.get("icik") or ""),
        })
        if holdings:
            p["issuer_n"] += 1
            p["reported_shares"] += reported
            if value is not None:
                p["priced_issuer_n"] += 1
                p["priced_value"] += value
            else:
                p["unpriced_issuer_n"] += 1

    rows = sorted(by_person.values(),
                  key=lambda p: (-(p["priced_value"]), -(p["buy_v"] + p["sell_v"]),
                                 p.get("insider") or ""))
    for p in rows:
        p["net_v"] = r2(p["buy_v"] - p["sell_v"])
        p["buy_v"] = r2(p["buy_v"])
        p["sell_v"] = r2(p["sell_v"])
        p["reported_shares"] = r4(p["reported_shares"])
        p["priced_value"] = r2(p["priced_value"])
        p["issuers"].sort(key=lambda e: (-(e.get("holding_value") or -1),
                                         -(e.get("reported_shares") or 0)))
        p["issuers"] = p["issuers"][:25]

    total_value = r2(sum(p["priced_value"] or 0 for p in rows))
    summary = {
        "target_year": target_year,
        "insiders": len(rows),
        "with_multiple_issuers": sum(1 for p in rows if p["issuer_n"] > 1),
        "with_priced_value": sum(1 for p in rows if p["priced_value"] is not None and p["priced_value"] > 0),
        "reported_value_priced": total_value,
        "scope": ("Per-insider totals sum the latest SEC as-filed post-transaction common-share "
                  "counts across the issuers that person filed against in the window, priced at "
                  "each issuer's latest close where available. Only issuers with filed "
                  "transactions are included; direct and indirect buckets may overlap; this is "
                  "not a full brokerage portfolio."),
    }
    payload = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "target_year": target_year,
        "summary": summary,
        "rows": rows[:INSIDER_PORTFOLIO_CAP],
        "truncated": len(rows) > INSIDER_PORTFOLIO_CAP,
        "row_count": len(rows),
    }
    jdump(payload, os.path.join(out_dir, "insider_portfolios.json"))

    cols = ["id", "insider", "pcik", "rel", "title", "issuer_n", "reported_shares",
            "priced_value", "priced_issuer_n", "unpriced_issuer_n", "buy_n", "sell_n",
            "buy_v", "sell_v", "net_v", "overlap", "first", "last"]
    ibuf = io.BytesIO()
    with gzip.GzipFile(fileobj=ibuf, mode="wb", compresslevel=9, mtime=0) as gz:
        txt = io.TextIOWrapper(gz, encoding="utf-8", newline="")
        w = csv.DictWriter(txt, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for p in rows:
            w.writerow({k: p.get(k) for k in cols})
        txt.flush()
        txt.detach()
    with open(os.path.join(out_dir, "insider_portfolios.csv.gz"), "wb") as f:
        f.write(ibuf.getvalue())

    # Full per-issuer breakdown export (one row per insider x issuer).
    ecols = ["id", "insider", "pcik", "co", "tk", "icik", "n", "buy_n", "sell_n",
             "buy_v", "sell_v", "reported_shares", "holding_value",
             "valuation_status", "latest_holding_fd", "latest_filing_fd", "edgar_url"]
    ebuf = io.BytesIO()
    with gzip.GzipFile(fileobj=ebuf, mode="wb", compresslevel=9, mtime=0) as gz:
        txt = io.TextIOWrapper(gz, encoding="utf-8", newline="")
        w = csv.DictWriter(txt, fieldnames=ecols, extrasaction="ignore")
        w.writeheader()
        for p in rows:
            for e in p["issuers"]:
                erow = {"id": p["id"], "insider": p["insider"], "pcik": p["pcik"]}
                erow.update({k: e.get(k) for k in ecols if k not in erow})
                w.writerow({k: erow.get(k) for k in ecols})
        txt.flush()
        txt.detach()
    with open(os.path.join(out_dir, "insider_portfolios_issuers.csv.gz"), "wb") as f:
        f.write(ebuf.getvalue())
    return payload


def write_insider_activity(agg, out_dir, marks_by_tk, target_year: int | None = None):
    """Publish buyer/seller overlap and reported-holding tracking.

    This is not a reconstructed brokerage portfolio. It is a conservative
    ledger derived only from SEC rows: buys/sells are code P/S totals, and
    reported shares are the latest as-filed post-transaction ownership amounts
    available for each insider/issuer/security/directness bucket.
    """
    rows = []
    for key, a in agg.get("activity", {}).items():
        holdings = []
        for h in a.get("holdings", {}).values():
            hh = {k: v for k, v in h.items() if k != "marker"}
            hh["edgar_url"] = edgar_url(hh.get("acc"), hh.get("icik"))
            holdings.append(hh)
        holdings.sort(key=lambda h: (h.get("fd") or "", h.get("td") or "", h.get("acc") or ""), reverse=True)
        has_reported_shares = bool(holdings)
        reported_shares = sum(float(h.get("shares") or 0) for h in holdings)
        mark = marks_by_tk.get(a.get("tk") or "") or {}
        holding_value = None
        if has_reported_shares and mark.get("last_px") is not None:
            holding_value = r2(reported_shares * float(mark["last_px"]))
        filings = sorted(a.get("filings", {}).values(),
                         key=lambda f: (f.get("fd") or "", f.get("acc") or ""), reverse=True)
        review_links = [{**f, "edgar_url": edgar_url(f.get("acc"), f.get("icik"))}
                        for f in filings[:10]]
        buy_sell_overlap = a.get("buy_n", 0) > 0 and a.get("sell_n", 0) > 0
        if has_reported_shares:
            valuation_status = "priced" if holding_value is not None else "shares_only_no_market_price"
        else:
            valuation_status = "no_reported_post_transaction_common_shares"
        row = {
            "id": f"{a.get('pcik') or key[0]}:{a.get('icik') or key[1]}",
            "target_year": target_year,
            "insider": a.get("insider") or "", "pcik": a.get("pcik") or "",
            "co": a.get("co") or "", "tk": a.get("tk") or "",
            "icik": a.get("icik") or "", "rel": a.get("rel") or "Unknown",
            "title": a.get("title") or "", "n": a.get("n", 0),
            "buy_n": a.get("buy_n", 0), "sell_n": a.get("sell_n", 0),
            "other_n": a.get("other_n", 0),
            "buy_sh": r4(a.get("buy_sh", 0.0)), "sell_sh": r4(a.get("sell_sh", 0.0)),
            "buy_v": r2(a.get("buy_v", 0.0)), "sell_v": r2(a.get("sell_v", 0.0)),
            "net_v": r2(a.get("buy_v", 0.0) - a.get("sell_v", 0.0)),
            "buy_sell_overlap": buy_sell_overlap,
            "first": a.get("first") or "", "last": a.get("last") or "",
            "first_buy": a.get("first_buy") or "", "last_buy": a.get("last_buy") or "",
            "first_sell": a.get("first_sell") or "", "last_sell": a.get("last_sell") or "",
            "reported_common_shares": r4(reported_shares) if has_reported_shares else None,
            "holding_groups": len(holdings), "latest_holding_fd": holdings[0].get("fd") if holdings else "",
            "mark_d": mark.get("last_d"), "mark_px": mark.get("last_px"),
            "holding_value": holding_value, "price_src": mark.get("price_src"),
            "valuation_status": valuation_status,
            "portfolio_scope": "SEC as-filed latest sharesOwnedFollowingTransaction for non-derivative common-equity rows; not a full outside brokerage portfolio.",
            "review_links": review_links,
            "holdings": holdings[:8],
        }
        rows.append(row)

    rows.sort(key=lambda r: (not r["buy_sell_overlap"], -(abs(r["net_v"] or 0)), -(r["n"] or 0), r.get("insider") or ""))
    total_holding_value = r2(sum(r.get("holding_value") or 0 for r in rows))
    summary = {
        "target_year": target_year,
        "insider_company_pairs": len(rows),
        "buy_sell_pairs": sum(1 for r in rows if r["buy_sell_overlap"]),
        "with_reported_common_shares": sum(1 for r in rows if r.get("reported_common_shares") is not None),
        "with_priced_holdings": sum(1 for r in rows if r.get("holding_value") is not None),
        "reported_holding_value_priced": total_holding_value,
        "scope": "Buyer/seller overlap and holdings are computed only from stored SEC Form 3/4/5 fields; missing SEC rows or missing market bars remain explicit gaps.",
        "full_csv": "data/insider_activity.csv.gz",
    }
    payload = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "target_year": target_year,
        "summary": summary,
        "rows": rows[:INSIDER_ACTIVITY_CAP],
        "truncated": len(rows) > INSIDER_ACTIVITY_CAP,
        "row_count": len(rows),
    }
    jdump(payload, os.path.join(out_dir, "insider_activity.json"))

    cols = ["id", "target_year", "insider", "pcik", "co", "tk", "icik", "rel", "title",
            "n", "buy_n", "sell_n", "other_n", "buy_sh", "sell_sh", "buy_v", "sell_v",
            "net_v", "buy_sell_overlap", "first", "last", "first_buy", "last_buy",
            "first_sell", "last_sell", "reported_common_shares", "holding_groups",
            "latest_holding_fd", "mark_d", "mark_px", "holding_value", "price_src",
            "valuation_status", "portfolio_scope"]
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as gz:
        txt = io.TextIOWrapper(gz, encoding="utf-8", newline="")
        w = csv.DictWriter(txt, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in cols})
        txt.flush()
        txt.detach()
    with open(os.path.join(out_dir, "insider_activity.csv.gz"), "wb") as f:
        f.write(buf.getvalue())
    return payload


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
# A regular U.S. session always follows a filing within a few days (weekends,
# holidays). If the first available open is farther away than this, the price
# series does not reach the entry session and no entry may be inferred.
ENTRY_MAX_GAP_DAYS = 10


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


_YAHOO_COOKIE = ""
_YAHOO_CRUMB = ""


def http_get(url: str, retries: int = 3, timeout: int = 45, extra_headers=None) -> bytes | None:
    global _YAHOO_COOKIE
    for attempt in range(retries + 1):
        MKT_THROTTLE.wait()
        headers = {
            "User-Agent": UA_MKT,
            "Accept": "application/json,text/csv,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        }
        if extra_headers:
            headers.update(extra_headers)
        if _YAHOO_COOKIE:
            headers["Cookie"] = _YAHOO_COOKIE
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                enc = (r.headers.get("Content-Encoding") or "").lower()
                if enc == "gzip" or raw[:2] == b"\x1f\x8b":
                    try:
                        raw = gzip.decompress(raw)
                    except OSError:
                        pass
                set_cookie = r.headers.get("Set-Cookie") or ""
                if "yahoo.com" in url and set_cookie:
                    # Keep A1/A3 session cookies used by the chart API crumb.
                    bits = [c.split(";")[0] for c in set_cookie.split(",") if "=" in c.split(";")[0]]
                    if bits:
                        _YAHOO_COOKIE = "; ".join(bits)
                return raw
        except urllib.error.HTTPError as e:
            if e.code in (404,):
                return None
            if e.code in (401, 403, 429, 503) and attempt < retries:
                time.sleep(2.0 * (attempt + 1))
                continue
            if e.code in (401, 403):
                return None
        except Exception:  # noqa: BLE001
            pass
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    return None


def _yahoo_crumb() -> str:
    global _YAHOO_CRUMB
    if _YAHOO_CRUMB:
        return _YAHOO_CRUMB
    http_get("https://fc.yahoo.com")
    raw = http_get("https://query1.finance.yahoo.com/v1/test/getcrumb")
    if raw:
        crumb = raw.decode("utf-8", "replace").strip()
        if crumb and "<" not in crumb and len(crumb) < 80:
            _YAHOO_CRUMB = crumb
    return _YAHOO_CRUMB


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
    crumb = urllib.parse.quote(_yahoo_crumb() or "", safe="")
    q = "?interval=1d&range=5y&events=split"
    if crumb:
        q += f"&crumb={crumb}"
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        raw = http_get(f"https://{host}/v8/finance/chart/{sym}{q}")
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


def fetch_nasdaq(tk: str):
    """Nasdaq historical quotes as a third fallback."""
    sym = urllib.parse.quote(yahoo_symbol(tk))
    today = date.today()
    url = (f"https://api.nasdaq.com/api/quote/{sym}/historical"
           f"?assetclass=stocks&fromdate={(today - timedelta(days=1300)).isoformat()}"
           f"&todate={today.isoformat()}&limit=9999")
    raw = http_get(url, extra_headers={"Origin": "https://www.nasdaq.com",
                                       "Referer": "https://www.nasdaq.com/"})
    if not raw:
        return None, "nasdaq"
    try:
        doc = json.loads(raw.decode("utf-8"))
        rows = (((doc or {}).get("data") or {}).get("tradesTable") or {}).get("rows") or []
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError):
        return None, "nasdaq"
    by = {}
    for r in rows:
        ds = (r.get("date") or "").strip()
        try:
            d = datetime.strptime(ds, "%m/%d/%Y").strftime("%Y-%m-%d")
        except ValueError:
            continue

        def money(x):
            return _r4(str(x or "").replace("$", "").replace(",", "").replace("—", "").replace("--", ""))

        o, c = money(r.get("open")), money(r.get("close"))
        if o is None and c is None:
            continue
        by[d] = {"d": d, "o": o if o is not None else c, "c": c if c is not None else o}
    bars = [by[k] for k in sorted(by)]
    return (bars or None), "nasdaq"


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
    for fn in (fetch_yahoo, fetch_stooq, fetch_nasdaq):
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
    ysym = yahoo_symbol(sig.get("tk") or "")
    out = dict(sig)
    out.update({"stake": STAKE, "status": "no_price", "entry_d": None,
                "entry_px": None, "shares": None, "last_d": None, "last_px": None,
                "mtm": None, "pnl": None, "roi": None, "gap": None,
                "r1": None, "r5": None, "r21": None, "r63": None, "r252": None,
                "delay_fd_entry": None, "hold": None,
                "entry_rule": "first_regular_session_open_strictly_after_sec_filing_date",
                "entry_rule_status": "no_price",
                "entry_check": "No daily market bars available; no entry price was inferred.",
                "edgar_url": edgar_url(sig.get("acc"), sig.get("icik")),
                "yahoo_history_url": (f"https://finance.yahoo.com/quote/{ysym}/history"
                                      if ysym else ""),
                "stooq_url": (f"https://stooq.com/q/d/?s={ysym.lower()}.us"
                              if ysym else "")})
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
        # Distinguish a pending fill (series reaches the filing date) from a
        # data gap (series ends before the filing date — delisted or uncovered).
        if usable and usable[-1]["d"] < sig["fd"]:
            out["status"] = "no_price"
            out["entry_rule_status"] = "no_bars_after_filing"
            out["entry_check"] = ("Market-bar series ends " + usable[-1]["d"] +
                                  ", before the SEC filing date; the name is likely "
                                  "delisted or uncovered — no entry price was inferred.")
            return out
        out["status"] = "awaiting_entry"
        out["entry_rule_status"] = "awaiting_entry"
        out["entry_check"] = "Bars exist, but no regular-session open strictly after the SEC filing date is available as of the build date."
        return out
    entry = usable[i]
    epx = float(entry["o"])
    # Guard against a short price window: if the first session after the
    # filing date is far away, the bar list does not actually reach the entry
    # date (e.g. a 1-year fetch for an older filing). Filling at that later
    # open would fabricate a wrong entry date and price.
    try:
        gap_days = (date.fromisoformat(entry["d"]) - date.fromisoformat(sig["fd"])).days
    except ValueError:
        gap_days = None
    if gap_days is not None and gap_days > ENTRY_MAX_GAP_DAYS:
        out["status"] = "no_price"
        out["entry_rule_status"] = "entry_window_missing"
        out["entry_gap_days"] = gap_days
        out["entry_check"] = (f"First available open is {gap_days} days after the SEC filing "
                              "date; the fetched price history does not reach the entry "
                              "session, so no entry price was inferred.")
        return out
    sh = STAKE / epx
    verified_entry = entry["d"] > sig["fd"] and epx > 0
    out.update({"status": "open", "entry_d": entry["d"], "entry_px": r4(epx),
                "shares": r4(sh),
                "entry_rule_status": "verified" if verified_entry else "invalid",
                "entry_check": "Entry date is strictly after the SEC filing date and uses that session's open."
                if verified_entry else "Entry rule violation: entry date is not strictly after filing date or open price is invalid."})
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
    price_sources = defaultdict(int)
    entry_rule_failures = 0
    arithmetic_failures = 0
    for p in positions:
        price_sources[p.get("price_src") or "none"] += 1
        if p.get("status") == "open":
            if not (p.get("entry_d") and p.get("fd") and p["entry_d"] > p["fd"] and (p.get("entry_px") or 0) > 0):
                entry_rule_failures += 1
            if p.get("shares") is not None and p.get("entry_px") is not None:
                if abs((p["shares"] * p["entry_px"]) - p.get("stake", STAKE)) > 0.25:
                    arithmetic_failures += 1
            if p.get("mtm") is not None and p.get("shares") is not None and p.get("last_px") is not None:
                if abs(p["mtm"] - p["shares"] * p["last_px"]) > 0.25:
                    arithmetic_failures += 1

    def tbl(d, key):
        rows = [{key: k, **stats(v)} for k, v in d.items()]
        rows.sort(key=lambda x: -x["n"])
        return rows

    ranked = sorted(opens, key=lambda p: p["roi"], reverse=True)
    keep = ("id", "tk", "co", "insider", "rel", "title", "fd", "td", "entry_d",
            "entry_px", "entry_rule_status", "price_src", "insider_px", "insider_sh",
            "insider_val", "shares", "last_px", "roi", "pnl", "mtm", "gap",
            "acc", "icik", "edgar_url")

    def slim(p):
        return {k: p.get(k) for k in keep}

    out = {
        "stake": STAKE,
        "counts": {
            "signals": len(positions),
            "open": len(opens),
            "awaiting_entry": sum(1 for p in positions if p["status"] == "awaiting_entry"),
            "no_price": sum(1 for p in positions if p["status"] == "no_price"),
            "entry_window_missing": sum(1 for p in positions
                                        if p.get("entry_rule_status") == "entry_window_missing"),
            "no_bars_after_filing": sum(1 for p in positions
                                        if p.get("entry_rule_status") == "no_bars_after_filing"),
        },
        "capital": {
            "deployed": r2(deployed), "value": r2(value),
            "pnl": r2(value - deployed),
            "roi": r4(value / deployed - 1.0) if deployed else None,
        },
        "verification": {
            "entry_rule_failures": entry_rule_failures,
            "arithmetic_failures": arithmetic_failures,
            "open_positions_checked": len(opens),
            "price_sources": sorted(({"source": k, "n": v} for k, v in price_sources.items()),
                                    key=lambda x: (-x["n"], x["source"])),
            "line_by_line_review": "Each paper row carries SEC accession/issuer CIK, EDGAR filing URL, entry rule status, price source, entry date, entry open, latest close, ROI and arithmetic fields.",
            "portfolio_warning": "Reported holdings use SEC post-transaction ownership fields only; no outside brokerage accounts or unfiled trades are inferred.",
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
        missing = a["counts"].get("no_price", 0)
        awaiting = a["counts"].get("awaiting_entry", 0)
        msg = "No paper positions have been opened yet."
        if missing or awaiting:
            msg += f" Signals awaiting usable prices: no_price={missing:,}, awaiting_entry={awaiting:,}."
        ewm = a["counts"].get("entry_window_missing", 0)
        nbaf = a["counts"].get("no_bars_after_filing", 0)
        if ewm or nbaf:
            msg += (f" Price-history gaps (no entry inferred, never estimated): "
                    f"entry_window_missing={ewm:,}, no_bars_after_filing={nbaf:,}.")
        return [msg]
    ewm = a["counts"].get("entry_window_missing", 0)
    nbaf = a["counts"].get("no_bars_after_filing", 0)
    if ewm or nbaf:
        f.append(f"Price-history gaps kept explicit: {ewm:,} signal(s) whose fetched bars do not "
                 f"reach the entry session (entry_window_missing) and {nbaf:,} whose series ends "
                 f"before the filing date (no_bars_after_filing). No entry was inferred for them.")
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
    cols = ["id", "status", "entry_rule_status", "fd", "td", "entry_d", "tk", "co",
            "insider", "rel", "title", "sec", "lots", "insider_sh", "insider_px",
            "insider_val", "entry_px", "gap", "shares", "stake", "last_d", "last_px",
            "mtm", "pnl", "roi", "r1", "r5", "r21", "r63", "r252", "delay_td_fd",
            "delay_fd_entry", "hold", "price_src", "entry_check", "edgar_url",
            "yahoo_history_url", "stooq_url", "acc",
            "form", "amend", "icik", "pcik"]
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
    runlog.start("build_data")
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
    paper_tickers = sorted(by_tk)
    holding_tickers = sorted(t for t in agg.get("activity_tickers", set()) if valid_ticker(t))
    all_tickers = paper_tickers + [t for t in holding_tickers if t not in by_tk]
    tickers = all_tickers
    if args.max_tickers:
        tickers = tickers[:args.max_tickers]
    log(f"  {len(tickers):,} unique tickers to price "
        f"({len(paper_tickers):,} paper, {len(holding_tickers):,} with reported holdings)")

    asof = audit_mod.asof_today().isoformat()
    positions, priced, nosrc = [], 0, 0
    marks_by_tk = {}
    priced_tickers = set()
    price_deadline = None
    if args.price_budget_min and args.price_budget_min > 0:
        price_deadline = time.monotonic() + args.price_budget_min * 60.0
    for i, tk in enumerate(tickers, 1):
        if price_deadline is not None and time.monotonic() > price_deadline:
            bars, src = [], "price_budget_exhausted"
        else:
            bars, src = get_bars(tk, offline=args.offline)
        priced_tickers.add(tk)
        mark = latest_mark(bars, asof)
        if mark:
            mark["price_src"] = src
            marks_by_tk[tk] = mark
        else:
            marks_by_tk[tk] = {"price_src": src}
        if bars:
            priced += 1
        else:
            nosrc += 1
        for s in by_tk.get(tk, []):
            p = simulate(s, bars, asof)
            p["price_src"] = src
            positions.append(p)
        if i % 100 == 0 or i == len(tickers):
            log(f"  priced {i}/{len(tickers)} tickers "
                f"({priced} with bars, {nosrc} without)")

    # If --max-tickers clipped the pricing list, do not silently drop paper
    # signals. Publish them as no_price/max_tickers_skipped so the full signal
    # count remains visible and auditable.
    for tk in [t for t in paper_tickers if t not in priced_tickers]:
        marks_by_tk.setdefault(tk, {"price_src": "max_tickers_skipped"})
        for s in by_tk[tk]:
            p = simulate(s, [], asof)
            p["price_src"] = "max_tickers_skipped"
            positions.append(p)

    log("Pass 3: writing site data …")
    os.makedirs(args.out, exist_ok=True)
    paper = write_paper(positions, args.out)
    nco, nb = write_companies(agg, args.out)
    nins = write_insiders(agg, args.out)
    activity = write_insider_activity(agg, args.out, marks_by_tk, target_year=target_year)
    portfolios = write_insider_portfolios(agg, args.out, marks_by_tk, target_year=target_year)
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
    log(f"  activity:       {activity['row_count']:,} insider/company pair(s), "
        f"{activity['summary']['buy_sell_pairs']:,} with both buys and sells")
    log(f"  portfolios:     {portfolios['row_count']:,} insider(s) with reported "
        f"holdings across {portfolios['summary']['with_multiple_issuers']:,} multi-issuer")
    log(f"  recent.json:    {len(agg['recent']):,} rows")
    log(f"  csv/:           {sum(ycounts.values()):,} rows across {len(ycounts)} years")
    log(f"  audit:          {audit['completeness']['status']} | "
        f"{audit['integrity']['row_issues']:,} row issues")
    log(f"  irregularities: {len(flags):,} automated review flag(s)")
    log(f"  paper:          {paper['counts']['open']:,} open positions, "
        f"ROI {paper['capital']['roi']}")
    log(f"Done. {summary['counts']['trades']:,} transactions published.")
    if target_year and summary["counts"]["trades"] == 0:
        log("FAIL: the target-year publish contains 0 trades — the official SEC "
            "sources yielded nothing for this window. Check collector/data/logs/ "
            "and source_manifest.json; refusing to report success.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
