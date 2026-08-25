#!/usr/bin/env python3
"""
CEOTrades site builder — turns collector/data/trades.json into a static
dashboard published on GitHub Pages.

Outputs (all committed, no build step needed at view time):
  data/trades.csv            full history, human-readable
  data/recent.json           last 90 days (dashboard default view)
  data/months/YYYY-MM.json   per filing-month (trades page)
  data/summary.json          precomputed aggregates for every page
  data/stats.json            collector run statistics
  index.html, trades.html, companies.html, insiders.html,
  analysis.html, about.html, css/site.css, js/app.js

Standard library only.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_IN = os.path.join(HERE, "data")
SITE_DATA = os.path.join(ROOT, "data")
SITE_CSS = os.path.join(ROOT, "css")
SITE_JS = os.path.join(ROOT, "js")

RECENT_DAYS = 90
TOP_N = 60

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


def fnum(v):
    """Normalize a possibly-None numeric to a float or None."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def edgar_url(acc: str) -> str:
    cik = str(int(acc.split("-")[0]))
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}-index.htm"


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate(rows: list[dict]) -> dict:
    today = datetime.utcnow()
    today_s = today.strftime("%Y-%m-%d")

    n_filings = len({r["acc"] for r in rows})
    companies = {}
    insiders = {}
    by_code = defaultdict(lambda: {"count": 0, "value": 0.0})
    by_rel = defaultdict(int)
    by_security = defaultdict(lambda: {"count": 0, "value": 0.0})
    daily = defaultdict(lambda: {"trades": 0, "buy": 0.0, "sell": 0.0})
    months = set()
    totals = {"buy": 0.0, "sell": 0.0, "value": 0.0}
    sides = defaultdict(int)
    n_priced_p = n_priced_s = 0
    form_counts = defaultdict(int)
    with_ticker = 0

    for r in rows:
        fd, td = r.get("fd", ""), r.get("td", "")
        if fd:
            daily[fd]["trades"] += 1
            months.add(fd[:7])
        v = fnum(r.get("val")) or 0.0
        code = r.get("code", "?")
        by_code[code]["count"] += 1
        by_code[code]["value"] += v
        side = r.get("side", "other")
        sides[side] += 1
        form_counts[r.get("form", "?")] += 1
        if r.get("tk"):
            with_ticker += 1
        if side == "buy":
            totals["buy"] += v
            if code == "P":
                n_priced_p += 1
                daily[fd]["buy"] += v
        elif side == "sell":
            totals["sell"] += v
            if code == "S":
                n_priced_s += 1
                daily[fd]["sell"] += v
        if v:
            totals["value"] += v

        sec = r.get("sec") or "(unspecified)"
        by_security[sec]["count"] += 1
        by_security[sec]["value"] += v

        for rel in (r.get("rel") or "").split("/"):
            rel = rel.strip()
            if rel:
                by_rel[rel] += 1

        key_c = (r.get("tk") or r.get("co"), r.get("co"), r.get("icik"))
        c = companies.setdefault(key_c, {
            "tk": r.get("tk", ""), "co": r.get("co", ""), "icik": r.get("icik", ""),
            "trades": 0, "insiders": set(), "filings": set(),
            "buy": 0.0, "sell": 0.0, "last": fd,
        })
        c["trades"] += 1
        c["insiders"].add(r.get("pcik") or r.get("in"))
        c["filings"].add(r.get("acc"))
        if side == "buy":
            c["buy"] += v
        elif side == "sell":
            c["sell"] += v
        c["last"] = max(c["last"], fd)

        key_i = r.get("pcik") or r.get("in")
        i = insiders.setdefault(key_i, {
            "in": r.get("in", ""), "pcik": r.get("pcik", ""),
            "companies": set(), "trades": 0, "buy": 0.0, "sell": 0.0,
            "last": fd, "title": r.get("title", ""), "rel": r.get("rel", ""),
        })
        i["companies"].add(r.get("tk") or r.get("co"))
        i["trades"] += 1
        if side == "buy":
            i["buy"] += v
        elif side == "sell":
            i["sell"] += v
        i["last"] = max(i["last"], fd)
        if r.get("title") and not i["title"]:
            i["title"] = r.get("title")
        if r.get("rel") and not i["rel"]:
            i["rel"] = r.get("rel")

    comp_list = []
    for c in companies.values():
        net = c["buy"] - c["sell"]
        comp_list.append({
            "tk": c["tk"], "co": c["co"], "icik": c["icik"],
            "trades": c["trades"], "insiders": len(c["insiders"]),
            "filings": len(c["filings"]),
            "buy": round(c["buy"], 2), "sell": round(c["sell"], 2), "net": round(net, 2),
            "last": c["last"],
        })
    comp_list.sort(key=lambda x: (-x["trades"], -abs(x["net"]), x["co"]))

    ins_list = []
    for i in insiders.values():
        ins_list.append({
            "in": i["in"], "pcik": i["pcik"], "co": sorted(c or "?" for c in i["companies"])[:4],
            "trades": i["trades"],
            "buy": round(i["buy"], 2), "sell": round(i["sell"], 2),
            "net": round(i["buy"] - i["sell"], 2),
            "last": i["last"], "title": i["title"], "rel": i["rel"],
        })
    ins_list.sort(key=lambda x: (-x["trades"], -abs(x["net"]), x["in"]))

    net_sorted = sorted(comp_list, key=lambda x: -abs(x["net"]))
    top_buyers = [c for c in sorted(comp_list, key=lambda x: -x["net"]) if c["net"] > 0][:TOP_N]
    top_sellers = [c for c in sorted(comp_list, key=lambda x: x["net"]) if c["net"] < 0][:TOP_N]
    top_purchasers = [i for i in sorted(ins_list, key=lambda x: -x["buy"])
                      if i["buy"] > 0][:TOP_N]

    daily_list = []
    cutoff = (today - timedelta(days=90)).strftime("%Y-%m-%d")
    for d in sorted(daily.keys()):
        if d < cutoff:
            continue
        e = daily[d]
        daily_list.append({
            "d": d, "trades": e["trades"],
            "buy": round(e["buy"], 2), "sell": round(e["sell"], 2),
            "net": round(e["buy"] - e["sell"], 2),
        })

    fdates = sorted(r["fd"] for r in rows if r.get("fd"))
    summary = {
        "generated": today.strftime("%Y-%m-%d %H:%M UTC"),
        "range": {"from": fdates[0] if fdates else "", "to": fdates[-1] if fdates else ""},
        "counts": {
            "trades": len(rows), "filings": n_filings,
            "companies": len(comp_list), "insiders": len(ins_list),
            "form4": form_counts.get("4", 0), "form5": form_counts.get("5", 0),
            "with_ticker": with_ticker, "priced_purchases": n_priced_p,
            "priced_sales": n_priced_s,
        },
        "sides": {k: sides[k] for k in sorted(sides)},
        "value": {
            "total": round(totals["value"], 2),
            "buy": round(totals["buy"], 2),
            "sell": round(totals["sell"], 2),
            "net": round(totals["buy"] - totals["sell"], 2),
        },
        "by_code": [
            {"code": c, "text": CODE_TEXT.get(c, f"Unknown ({c})"),
             "count": v["count"], "value": round(v["value"], 2)}
            for c, v in sorted(by_code.items(), key=lambda kv: -kv[1]["count"])
        ],
        "by_rel": [{"rel": k, "count": v}
                   for k, v in sorted(by_rel.items(), key=lambda kv: -kv[1])],
        "by_security": [
            {"sec": k, "count": v["count"], "value": round(v["value"], 2)}
            for k, v in sorted(by_security.items(), key=lambda kv: -kv[1]["count"])[:30]
        ],
        "companies": comp_list,
        "insiders": ins_list,
        "top_companies": comp_list[:TOP_N],
        "top_insiders": ins_list[:TOP_N],
        "net_buyers": top_buyers[:TOP_N],
        "net_sellers": top_sellers[:TOP_N],
        "top_purchasers": top_purchasers,
        "daily": daily_list,
        "months": sorted(months, reverse=True),
    }
    return summary


# ---------------------------------------------------------------------------
# Data file outputs
# ---------------------------------------------------------------------------

CSV_COLS = [
    ("filing_date", "fd"), ("transaction_date", "td"), ("form", "form"),
    ("accession", "acc"), ("company", "co"), ("ticker", "tk"),
    ("insider", "in"), ("relationship", "rel"), ("title", "title"),
    ("code", "code"), ("code_text", "ct"), ("side", "side"),
    ("security", "sec"), ("shares", "sh"), ("price", "px"), ("value", "val"),
    ("acquired_disposed", "ad"), ("shares_after", "af"),
    ("direct_indirect", "di"), ("derivative", "der"), ("underlying", "under"),
    ("put_call", "putcall"), ("expiration", "exp"), ("edgar_url", None),
]


def write_csv(rows: list[dict], path: str):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([c[0] for c in CSV_COLS])
        for r in rows:
            row = []
            for name, key in CSV_COLS:
                if key is None:
                    row.append(edgar_url(r["acc"]))
                else:
                    row.append(r.get(key, ""))
            w.writerow(row)


def write_data_outputs(rows: list[dict], summary: dict, stats: dict | None):
    os.makedirs(SITE_DATA, exist_ok=True)
    os.makedirs(os.path.join(SITE_DATA, "months"), exist_ok=True)

    write_csv(rows, os.path.join(SITE_DATA, "trades.csv"))
    with open(os.path.join(SITE_DATA, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, separators=(",", ":"))
    if stats:
        with open(os.path.join(SITE_DATA, "stats.json"), "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, separators=(",", ":"))

    # Remove stale month files, then rewrite current months.
    mdir = os.path.join(SITE_DATA, "months")
    for fn in os.listdir(mdir):
        if fn.endswith(".json"):
            os.remove(os.path.join(mdir, fn))

    cutoff = (datetime.utcnow() - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%d")
    recent = [r for r in rows if r.get("fd", "") >= cutoff]
    with open(os.path.join(SITE_DATA, "recent.json"), "w", encoding="utf-8") as f:
        json.dump(recent, f, ensure_ascii=False, separators=(",", ":"))

    by_month: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        m = (r.get("fd") or "")[:7]
        if m:
            by_month[m].append(r)
    for m, rs in by_month.items():
        rs.sort(key=lambda r: (r["fd"], r["td"]), reverse=True)
        with open(os.path.join(mdir, f"{m}.json"), "w", encoding="utf-8") as f:
            json.dump(rs, f, ensure_ascii=False, separators=(",", ":"))
    return len(recent), len(by_month)


# ---------------------------------------------------------------------------
# HTML / CSS / JS
# ---------------------------------------------------------------------------

NAV = """
    <a href="index.html" data-nav="index">Dashboard</a>
    <a href="trades.html" data-nav="trades">Trades</a>
    <a href="companies.html" data-nav="companies">Companies</a>
    <a href="insiders.html" data-nav="insiders">Insiders</a>
    <a href="analysis.html" data-nav="analysis">Analysis</a>
    <a href="about.html" data-nav="about">About</a>
"""


def page(title: str, active: str, body: str, desc: str = "") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · CEOTrades</title>
<meta name="description" content="{desc or 'Every insider trade (SEC Form 4 / Form 5) at publicly traded companies — collected automatically from SEC EDGAR.'}">
<link rel="stylesheet" href="css/site.css">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📈</text></svg>">
</head>
<body data-page="{active}">
<header class="top">
  <div class="wrap nav">
    <a class="brand" href="index.html"><span class="brand-ico">📈</span>CEO<span>Trades</span></a>
    <nav>{NAV}</nav>
  </div>
</header>
<main class="wrap">
{body}
</main>
<footer class="wrap foot">
  <div class="foot-in">
    <div><strong>CEOTrades</strong> — insider trade intelligence.</div>
    <div class="foot-src">
      Source: <a href="https://www.sec.gov/edgar/searchedgar/companysearch" target="_blank" rel="noopener">SEC EDGAR</a>
      Form 4 / Form 5 filings · collected automatically by GitHub Actions ·
      <span data-slot="last-updated">loading…</span>
    </div>
    <div class="foot-disc">Data is public SEC filing data. Not investment advice.</div>
  </div>
</footer>
<script src="js/app.js"></script>
</body>
</html>
"""


CSS = r"""
:root{
  --bg:#f4f6f9; --card:#ffffff; --ink:#0f1b2d; --muted:#5c6b82; --line:#e4e9f0;
  --acc:#2457e6; --acc-soft:#eaf0fe;
  --buy:#0e8a5f; --buy-soft:#e2f5ec; --sell:#d64550; --sell-soft:#fdecee;
  --neutral:#64748b; --neutral-soft:#eef1f5; --warn:#b45309;
  --radius:14px; --shadow:0 1px 2px rgba(16,27,45,.05),0 4px 14px rgba(16,27,45,.06);
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Inter,"Helvetica Neue",Arial,sans-serif;}
a{color:var(--acc);text-decoration:none}
a:hover{text-decoration:underline}
.wrap{max-width:1180px;margin:0 auto;padding:0 20px}
.top{background:#fff;border-bottom:1px solid var(--line);position:sticky;top:0;z-index:20}
.nav{display:flex;align-items:center;justify-content:space-between;height:60px;gap:16px}
.brand{font-size:19px;font-weight:800;color:var(--ink);letter-spacing:-.02em;white-space:nowrap}
.brand span{color:var(--acc)}
.brand-ico{margin-right:2px}
.brand:hover{text-decoration:none}
.nav nav{display:flex;gap:4px;flex-wrap:wrap}
.nav nav a{padding:7px 12px;border-radius:9px;color:var(--muted);font-weight:600;font-size:14px}
.nav nav a:hover{background:var(--acc-soft);color:var(--acc);text-decoration:none}
.nav nav a.active{background:var(--acc-soft);color:var(--acc)}
main{padding:28px 20px 40px}
.hero{background:linear-gradient(135deg,#0f1b2d 0%,#172a4a 55%,#1d3a6b 100%);color:#fff;
  border-radius:var(--radius);padding:34px 34px 30px;margin-bottom:22px;box-shadow:var(--shadow)}
.hero h1{margin:0 0 8px;font-size:30px;letter-spacing:-.02em}
.hero p{margin:0;color:#c3d0e6;max-width:720px;font-size:15.5px}
.hero .range{display:inline-block;margin-top:14px;background:rgba(255,255,255,.12);
  border:1px solid rgba(255,255,255,.2);padding:5px 12px;border-radius:999px;font-size:13px;font-weight:600}
h2.sec{font-size:18px;margin:26px 0 12px;letter-spacing:-.01em}
h2.sec small{color:var(--muted);font-weight:500;font-size:13px}
.grid{display:grid;gap:16px}
.g2{grid-template-columns:1fr 1fr}
.g3{grid-template-columns:repeat(3,1fr)}
.g4{grid-template-columns:repeat(4,1fr)}
.g6{grid-template-columns:repeat(6,1fr)}
@media(max-width:980px){.g6{grid-template-columns:repeat(3,1fr)}.g4{grid-template-columns:repeat(2,1fr)}.g2{grid-template-columns:1fr}}
@media(max-width:640px){.g6{grid-template-columns:repeat(2,1fr)}.g3{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
  box-shadow:var(--shadow);padding:18px}
.card h3{margin:0 0 12px;font-size:15px;font-weight:700}
.card h3 small{color:var(--muted);font-weight:500;font-size:12px}
.stat .lbl{font-size:12.5px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.stat .val{font-size:24px;font-weight:800;margin-top:4px;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.stat .sub{font-size:12px;color:var(--muted);margin-top:2px}
.val.buy{color:var(--buy)} .val.sell{color:var(--sell)}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{font-size:11.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);
  text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap;cursor:default}
th.sortable{cursor:pointer}
th.sortable:hover{color:var(--ink)}
td{padding:9px 10px;border-bottom:1px solid #f0f3f7;vertical-align:middle}
tbody tr:hover{background:#f8fafd}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.pos{color:var(--buy);font-weight:600}.neg{color:var(--sell);font-weight:600}
.ticker{font-weight:700;color:var(--ink)}
.sub{color:var(--muted);font-size:12px}
.pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:11.5px;font-weight:700;line-height:1.5}
.pill.code{min-width:26px;text-align:center}
.pill.buy{background:var(--buy-soft);color:var(--buy)}
.pill.sell{background:var(--sell-soft);color:var(--sell)}
.pill.neutral{background:var(--neutral-soft);color:var(--neutral)}
.pill.acc{background:var(--acc-soft);color:var(--acc)}
.badge{display:inline-block;background:var(--acc-soft);color:var(--acc);font-size:11px;font-weight:700;padding:1px 7px;border-radius:6px;margin-left:6px}
.toolbar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:14px}
.toolbar input[type=search],.toolbar select{
  font:inherit;font-size:13.5px;padding:8px 12px;border:1px solid var(--line);
  border-radius:10px;background:#fff;color:var(--ink);outline:none}
.toolbar input[type=search]{min-width:220px;flex:1;max-width:340px}
.toolbar input:focus,.toolbar select:focus{border-color:var(--acc);box-shadow:0 0 0 3px var(--acc-soft)}
.toolbar .spacer{flex:1}
.btn{display:inline-block;font:inherit;font-size:13px;font-weight:700;color:#fff;background:var(--acc);
  border:none;border-radius:10px;padding:8px 14px;cursor:pointer}
.btn:hover{filter:brightness(1.08)}
.btn.ghost{background:#fff;color:var(--ink);border:1px solid var(--line)}
.count-note{font-size:12.5px;color:var(--muted);margin:10px 2px 0}
.table-scroll{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:#fff}
.pager{display:flex;gap:8px;align-items:center;justify-content:flex-end;margin-top:12px;font-size:13px}
.pager button{font:inherit;font-size:13px;font-weight:600;border:1px solid var(--line);background:#fff;
  border-radius:8px;padding:5px 12px;cursor:pointer}
.pager button:disabled{opacity:.4;cursor:default}
.pager button:not(:disabled):hover{border-color:var(--acc);color:var(--acc)}
.bars{display:flex;flex-direction:column;gap:8px}
.bar-row{display:grid;grid-template-columns:150px 1fr 92px;gap:10px;align-items:center;font-size:13px}
.bar-row .blbl{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600}
.bar-track{background:#eef1f6;border-radius:6px;height:14px;overflow:hidden;position:relative}
.bar-fill{height:100%;border-radius:6px;background:var(--acc)}
.bar-fill.buy{background:var(--buy)}.bar-fill.sell{background:var(--sell)}
.bar-row .bval{font-variant-numeric:tabular-nums;text-align:right;color:var(--muted);font-weight:600}
.chart{display:flex;align-items:flex-end;gap:2px;height:150px;padding-top:6px}
.chart .col{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;gap:3px;height:100%;position:relative}
.chart .col .b{width:100%;max-width:22px;border-radius:3px 3px 0 0;background:var(--acc);opacity:.85}
.chart .col .b.buy{background:var(--buy)}.chart .col .b.sell{background:var(--sell)}
.chart .col:hover .tip{opacity:1}
.chart .tip{position:absolute;bottom:calc(100% + 4px);left:50%;transform:translateX(-50%);
  background:#0f1b2d;color:#fff;font-size:11px;padding:4px 8px;border-radius:6px;white-space:nowrap;
  opacity:0;pointer-events:none;transition:opacity .12s;z-index:5}
.chart .xlab{font-size:10px;color:var(--muted);margin-top:5px}
.donut-wrap{display:flex;align-items:center;gap:22px;flex-wrap:wrap}
.donut{width:150px;height:150px;border-radius:50%;position:relative;flex:none}
.donut::after{content:"";position:absolute;inset:26%;background:#fff;border-radius:50%}
.donut .hole{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;z-index:1;font-size:12px;color:var(--muted)}
.donut .hole b{font-size:17px;color:var(--ink)}
.legend{display:flex;flex-direction:column;gap:8px;font-size:13.5px}
.legend .dot{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:8px}
.linklist{display:flex;flex-direction:column}
.linklist a{display:flex;justify-content:space-between;gap:10px;padding:9px 4px;border-bottom:1px solid #f0f3f7;color:var(--ink)}
.linklist a:last-child{border-bottom:none}
.linklist a:hover{background:#f8fafd;text-decoration:none}
.linklist .l-r{color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}
ul.clean{margin:0;padding-left:18px}
ul.clean li{margin:6px 0}
.doc{font-size:14px}
.doc h3{margin:18px 0 6px}
.doc table{font-size:13px}
.doc td,.doc th{padding:7px 9px}
.foot{padding:26px 20px 34px;color:var(--muted);font-size:12.5px}
.foot-in{display:flex;flex-direction:column;gap:5px;border-top:1px solid var(--line);padding-top:18px}
.empty{padding:30px;text-align:center;color:var(--muted)}
.spinner{display:inline-block;width:14px;height:14px;border:2px solid var(--line);border-top-color:var(--acc);
  border-radius:50%;animation:spin .8s linear infinite;vertical-align:-2px;margin-right:6px}
@keyframes spin{to{transform:rotate(360deg)}}
@media print{.top,.toolbar,.pager,.foot{display:none}}
"""

JS = r"""
"use strict";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

async function loadJSON(url) {
  const r = await fetch(url, { cache: "no-cache" });
  if (!r.ok) throw new Error(url + " -> " + r.status);
  return r.json();
}
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtNum(n) {
  if (n === null || n === undefined || n === "") return "—";
  return Number(n).toLocaleString("en-US", { maximumFractionDigits: 2 });
}
function fmtMoney(n, signed) {
  if (n === null || n === undefined || isNaN(n)) return "—";
  const a = Math.abs(n);
  let s;
  if (a >= 1e9) s = "$" + (n / 1e9).toFixed(2) + "B";
  else if (a >= 1e6) s = "$" + (n / 1e6).toFixed(1) + "M";
  else if (a >= 1e3) s = "$" + (n / 1e3).toFixed(0) + "K";
  else s = "$" + n.toFixed(0);
  if (signed) { if (n > 0) s = "+" + s; }
  return s;
}
function fmtDate(d) {
  if (!d) return "—";
  return d; // ISO already
}
function sideClass(code) {
  if (code === "P") return "buy";
  if (code === "S") return "sell";
  return "neutral";
}
function codePill(code, text) {
  return `<span class="pill code ${sideClass(code)}" title="${esc(text || "")}">${esc(code || "?")}</span>`;
}
function companyCell(r) {
  const tk = r.tk ? `<span class="badge">${esc(r.tk)}</span>` : "";
  return `<td><span class="ticker">${esc(r.co)}</span>${tk}<div class="sub">${esc(r.tk ? "" : "no ticker filed")}</div></td>`;
}
function tradeRow(r) {
  const link = r.acc ? `<a href="${`https://www.sec.gov/Archives/edgar/data/${parseInt(r.acc.slice(0, 10))}/${r.acc.replace(/-/g, "")}-index.htm`}" target="_blank" rel="noopener" title="View filing on SEC EDGAR">↗</a>` : "";
  return `<tr>
    <td class="num" title="Filed">${fmtDate(r.fd)}</td>
    <td class="num" title="Transaction date">${fmtDate(r.td)}</td>
    ${companyCell(r)}
    <td>${esc(r.in)}<div class="sub">${esc([r.rel, r.title].filter(Boolean).join(" · "))}</div></td>
    <td>${codePill(r.code, r.ct)}</td>
    <td>${esc(r.sec)}${r.der ? `<div class="sub">derivative${r.under ? " → " + esc(r.under) : ""}</div>` : ""}</td>
    <td class="num">${fmtNum(r.sh)}</td>
    <td class="num">${r.px ? "$" + fmtNum(r.px) : "—"}</td>
    <td class="num"><b>${r.val ? fmtMoney(r.val) : "—"}</b></td>
    <td class="num">${fmtNum(r.af)}</td>
    <td style="text-align:center">${r.di === "I" ? "I" : "D"} ${link}</td>
  </tr>`;
}

function makeTable(el, cols, rows, opts) {
  // cols: [{key,label,cls,render(row)}...]
  opts = opts || {};
  const state = { sortKey: opts.sortKey || null, sortDir: -1, page: 1, per: opts.per || 25, filter: opts.filter || (() => true) };
  el.innerHTML = "";
  const wrap = document.createElement("div");
  wrap.className = "table-scroll";
  el.appendChild(wrap);
  const note = document.createElement("div");
  note.className = "count-note";
  el.appendChild(note);
  const pager = document.createElement("div");
  pager.className = "pager";
  el.appendChild(pager);

  function applyFilter(rows) { return rows.filter(state.filter); }
  function applySort(rows) {
    if (!state.sortKey) return rows;
    const k = state.sortKey, dir = state.sortDir;
    return rows.slice().sort((a, b) => {
      let x = a[k], y = b[k];
      if (x == null) x = dir === 1 ? Infinity : -Infinity;
      if (y == null) y = dir === 1 ? -Infinity : Infinity;
      if (typeof x === "string" || typeof y === "string")
        return String(x).localeCompare(String(y)) * dir;
      return (x - y) * dir;
    });
  }
  function render() {
    const frows = applySort(applyFilter(rows));
    const pages = Math.max(1, Math.ceil(frows.length / state.per));
    if (state.page > pages) state.page = pages;
    const slice = frows.slice((state.page - 1) * state.per, state.page * state.per);
    let html = "<table><thead><tr>";
    for (const c of cols)
      html += `<th class="${c.cls || ""} ${c.key ? "sortable" : ""}" data-key="${c.key || ""}">${c.label}${state.sortKey === c.key ? (state.sortDir === 1 ? " ▲" : " ▼") : ""}</th>`;
    html += "</tr></thead><tbody>";
    for (const r of slice) html += (opts.row || tradeRow)(r, cols);
    html += "</tbody></table>";
    wrap.innerHTML = slice.length ? html : '<div class="empty">No trades match the current filters.</div>';
    note.textContent = `${frows.length.toLocaleString()} of ${rows.length.toLocaleString()} trades shown`;
    pager.innerHTML = "";
    if (pages > 1) {
      const b = (label, pg, dis) => {
        const btn = document.createElement("button");
        btn.textContent = label; btn.disabled = !!dis;
        btn.onclick = () => { state.page = pg; render(); el.scrollIntoView({ block: "start" }); };
        return btn;
      };
      pager.appendChild(b("← Prev", state.page - 1, state.page === 1));
      const info = document.createElement("span");
      info.textContent = `page ${state.page} / ${pages}`;
      pager.appendChild(info);
      pager.appendChild(b("Next →", state.page + 1, state.page === pages));
    }
    $$(".sortable", wrap).forEach(th => th.onclick = () => {
      const k = th.dataset.key;
      if (!k) return;
      if (state.sortKey === k) state.sortDir *= -1;
      else { state.sortKey = k; state.sortDir = -1; }
      render();
    });
  }
  render();
  return { setFilter(fn) { state.filter = fn; state.page = 1; render(); }, setRows(rs) { rows = rs; state.page = 1; render(); } };
}

function barList(el, items, opts) {
  // items: [{label, value, sub, kind}] kind: buy|sell|acc
  opts = opts || {};
  const max = Math.max(...items.map(i => Math.abs(i.value)), 1);
  el.innerHTML = '<div class="bars">' + items.map(i => {
    const w = Math.max(2, Math.round(Math.abs(i.value) / max * 100));
    return `<div class="bar-row">
      <div class="blbl" title="${esc(i.label)}">${esc(i.label)}</div>
      <div class="bar-track"><div class="bar-fill ${i.kind || "acc"}" style="width:${w}%"></div></div>
      <div class="bval">${opts.fmt ? opts.fmt(i.value) : fmtNum(i.value)}${i.sub ? `<span class="sub"> ${esc(i.sub)}</span>` : ""}</div>
    </div>`;
  }).join("") + "</div>";
}

function setLastUpdated() {
  loadJSON("data/summary.json").then(s => {
    $$("[data-slot=last-updated]").forEach(e => e.textContent = "last updated " + s.generated);
  }).catch(() => {});
}

// ---------------------------------------------------------------- pages ----
async function initIndex() {
  const [sum, recent] = await Promise.all([loadJSON("data/summary.json"), loadJSON("data/recent.json")]);
  $(".hero .range").textContent =
    `Coverage ${sum.range.from} → ${sum.range.to} · ${sum.counts.trades.toLocaleString()} trades · auto-updated`;
  const cards = [
    ["Insider trades", sum.counts.trades, "across " + sum.counts.filings.toLocaleString() + " filings"],
    ["Companies", sum.counts.companies, "with filed trades"],
    ["Insiders", sum.counts.insiders, "directors, officers, 10%+ owners"],
    ["Open-market buys", sum.value.buy, "code P, " + sum.counts.priced_purchases.toLocaleString() + " trades", "buy"],
    ["Open-market sells", sum.value.sell, "code S, " + sum.counts.priced_sales.toLocaleString() + " trades", "sell"],
    ["Net insider flow", sum.value.net, "buys − sells (P − S)", sum.value.net >= 0 ? "buy" : "sell"],
  ];
  $("#stats").innerHTML = cards.map(c =>
    `<div class="card stat"><div class="lbl">${c[0]}</div><div class="val ${c[4] || ""}">${
      typeof c[1] === "number" && (c[1] >= 1e6 || c[1] <= -1e6 || String(c[0]).toLowerCase().includes("$") || /buys|sells|flow/.test(c[0]))
        ? (String(c[0]).toLowerCase().includes("trades") || /Companies|Insiders/.test(c[0])) ? c[1].toLocaleString() : fmtMoney(c[1], true)
        : c[1].toLocaleString()
    }</div><div class="sub">${c[2]}</div></div>`).join("");

  const latest = recent.slice(0, 15);
  $("#latest").innerHTML = `<div class="table-scroll"><table><thead><tr>
    <th>Filed</th><th>Company</th><th>Insider</th><th>Code</th><th class="num">Value</th>
  </tr></thead><tbody>` + latest.map(r =>
    `<tr><td class="num">${r.fd}<div class="sub">txn ${fmtDate(r.td)}</div></td>
     <td>${companyCell(r)}</td>
     <td>${esc(r.in)}<div class="sub">${esc(r.title || r.rel)}</div></td>
     <td>${codePill(r.code, r.ct)}</td>
     <td class="num"><b>${r.val ? fmtMoney(r.val) : "—"}</b></td></tr>`).join("") +
    `</tbody></table></div><div style="margin-top:10px"><a href="trades.html" class="btn ghost" style="width:100%;text-align:center">Browse all ${sum.counts.trades.toLocaleString()} trades →</a></div>`;

  barList($("#topco"), sum.top_companies.slice(0, 10)
    .map(c => ({ label: c.tk || c.co, value: c.trades, sub: c.co, kind: "acc" })), {});
  const netItems = sum.companies.slice().sort((a, b) => Math.abs(b.net) - Math.abs(a.net)).slice(0, 10)
    .map(c => ({ label: c.tk || c.co, value: c.net, kind: c.net >= 0 ? "buy" : "sell" }));
  barList($("#netflow"), netItems, { fmt: v => fmtMoney(v, true) });

  // daily volume
  const daily = sum.daily.slice(-30);
  const maxT = Math.max(...daily.map(d => d.trades), 1);
  $("#dailychart").innerHTML = '<div class="chart">' + daily.map(d => {
    const h = Math.max(2, Math.round(d.trades / maxT * 100));
    return `<div class="col" title="${d.d}: ${d.trades} trades, net ${fmtMoney(d.net, true)}">
      <div class="b" style="height:${h}%"></div></div>`;
  }).join("") + "</div>";
  $("#dailyx").innerHTML = `<div style="display:flex;justify-content:space-between;font-size:11px;color:var(--muted)">
    <span>${daily.length ? daily[0].d : ""}</span><span>${daily.length ? daily[daily.length - 1].d : ""}</span></div>`;
  const lastDaily = daily[daily.length - 1];
  $("#dailynote").textContent = lastDaily
    ? `Most recent day: ${lastDaily.d} — ${lastDaily.trades} trades, net ${fmtMoney(lastDaily.net, true)}` : "";

  const buys = sum.net_buyers.slice(0, 5), sells = sum.net_sellers.slice(0, 5);
  $("#buyers").innerHTML = buys.length ? buys.map(c =>
    `<div class="linklist"><a href="trades.html?tk=${encodeURIComponent(c.tk || c.co)}">
      <span>${esc(c.tk || c.co)} <span class="sub">${esc(c.co)}</span></span>
      <span class="l-r pos">${fmtMoney(c.net, true)}</span></a></div>`).join("")
    : '<div class="empty">No open-market net buyers yet.</div>';
  $("#sellers").innerHTML = sells.length ? sells.map(c =>
    `<div class="linklist"><a href="trades.html?tk=${encodeURIComponent(c.tk || c.co)}">
      <span>${esc(c.tk || c.co)} <span class="sub">${esc(c.co)}</span></span>
      <span class="l-r neg">${fmtMoney(c.net, true)}</span></a></div>`).join("")
    : '<div class="empty">No open-market net sellers yet.</div>';
}

async function initTrades() {
  const sum = await loadJSON("data/summary.json");
  const params = new URLSearchParams(location.search);
  const presetTk = params.get("tk") || "";
  const presetMonth = params.get("m") || "recent";

  const codeOpts = sum.by_code.map(c => `<option value="${esc(c.code)}">${esc(c.code)} — ${esc(c.text)}</option>`).join("");
  $("#codeSel").innerHTML = `<option value="">All codes</option>` + codeOpts;
  $("#sideSel").innerHTML = `<option value="">All sides</option>
    <option value="buy">Buys (P)</option><option value="sell">Sells (S)</option>
    <option value="exercise">Exercises</option><option value="grant">Grants</option>
    <option value="withholding">Tax withholding</option><option value="gift">Gifts</option>
    <option value="other">Other</option>`;
  $("#monthSel").innerHTML = `<option value="recent">Last 90 days</option>` +
    sum.months.map(m => `<option value="${esc(m)}">${m}</option>`).join("");
  if (presetMonth && (presetMonth === "recent" || sum.months.includes(presetMonth)))
    $("#monthSel").value = presetMonth;

  let api = null;
  const tableEl = $("#tradesTable");
  api = makeTable(tableEl, [
    { key: "fd", label: "Filed", cls: "num" },
    { key: "td", label: "Txn", cls: "num" },
    { key: "co", label: "Company" },
    { key: "in", label: "Insider" },
    { key: "code", label: "Code" },
    { key: "sec", label: "Security" },
    { key: "sh", label: "Shares", cls: "num" },
    { key: "px", label: "Price", cls: "num" },
    { key: "val", label: "Value", cls: "num" },
    { key: "af", label: "After", cls: "num" },
    { key: "di", label: "D/I", cls: "" },
  ], [], { per: 25, sortKey: "fd" });

  function currentFilters() {
    const q = $("#q").value.trim().toLowerCase();
    const code = $("#codeSel").value, side = $("#sideSel").value, form = $("#formSel").value;
    const der = $("#derChk").checked, tk = presetTk.toLowerCase();
    return (r) =>
      (!tk || (r.tk || "").toLowerCase() === tk || (r.co || "").toLowerCase().includes(tk)) &&
      (!code || r.code === code) && (!side || r.side === side) &&
      (!form || r.form === form) && (!der || r.der === 1) &&
      (!q || ((r.in || "") + " " + (r.co || "") + " " + (r.tk || "") + " " + (r.sec || "") + " " + (r.title || "")).toLowerCase().includes(q));
  }
  async function load(month) {
    tableEl.innerHTML = '<div class="empty"><span class="spinner"></span>Loading trades…</div>';
    try {
      const rows = month === "recent"
        ? await loadJSON("data/recent.json")
        : await loadJSON("data/months/" + month + ".json");
      api.setRows(rows);
      api.setFilter(currentFilters());
      if (presetTk) $("#q").placeholder = "Search within " + presetTk + " (clear box to widen)";
    } catch (e) {
      tableEl.innerHTML = '<div class="empty">Could not load data: ' + esc(String(e)) + "</div>";
    }
  }
  ["q", "codeSel", "sideSel", "formSel", "derChk"].forEach(id => {
    $("#" + id).addEventListener("input", () => api.setFilter(currentFilters()));
    $("#" + id).addEventListener("change", () => api.setFilter(currentFilters()));
  });
  $("#monthSel").addEventListener("change", () => load($("#monthSel").value));
  load(presetMonth);
}

async function initCompanies() {
  const sum = await loadJSON("data/summary.json");
  const el = $("#coTable");
  const api = makeTable(el, [
    { key: "tk", label: "Ticker" },
    { key: "co", label: "Company" },
    { key: "trades", label: "Trades", cls: "num" },
    { key: "insiders", label: "Insiders", cls: "num" },
    { key: "buy", label: "Buys $", cls: "num" },
    { key: "sell", label: "Sells $", cls: "num" },
    { key: "net", label: "Net $", cls: "num" },
    { key: "last", label: "Last filed", cls: "num" },
  ], sum.companies, { per: 50, sortKey: "trades", row: (r) => `<tr>
    <td><span class="ticker">${r.tk ? esc(r.tk) : "—"}</span></td>
    <td>${esc(r.co)}</td>
    <td class="num">${fmtNum(r.trades)}</td>
    <td class="num">${fmtNum(r.insiders)}</td>
    <td class="num">${r.buy ? fmtMoney(r.buy) : "—"}</td>
    <td class="num">${r.sell ? fmtMoney(r.sell) : "—"}</td>
    <td class="num ${r.net > 0 ? "pos" : r.net < 0 ? "neg" : ""}">${fmtMoney(r.net, true)}</td>
    <td class="num">${fmtDate(r.last)}</td>
  </tr>
  <tr><td colspan="8" style="padding-top:0"><a class="sub" href="trades.html?tk=${encodeURIComponent(r.tk || r.co)}">view trades →</a></td></tr>` });
  $("#coSearch").addEventListener("input", () => {
    const q = $("#coSearch").value.trim().toLowerCase();
    api.setFilter((r) => !q || (r.co || "").toLowerCase().includes(q) || (r.tk || "").toLowerCase().includes(q));
  });
  const nb = sum.net_buyers.slice(0, 8), ns = sum.net_sellers.slice(0, 8);
  barList($("#nb"), nb.map(c => ({ label: c.tk || c.co, value: c.net, kind: "buy" })), { fmt: v => fmtMoney(v, true) });
  barList($("#ns"), ns.map(c => ({ label: c.tk || c.co, value: -c.net, kind: "sell" })), { fmt: v => fmtMoney(v) });
}

async function initInsiders() {
  const sum = await loadJSON("data/summary.json");
  const el = $("#inTable");
  const api = makeTable(el, [
    { key: "in", label: "Insider" },
    { key: "rel", label: "Role" },
    { key: "co", label: "Companies" },
    { key: "trades", label: "Trades", cls: "num" },
    { key: "buy", label: "Buys $", cls: "num" },
    { key: "sell", label: "Sells $", cls: "num" },
    { key: "net", label: "Net $", cls: "num" },
    { key: "last", label: "Last filed", cls: "num" },
  ], sum.insiders, { per: 50, sortKey: "trades", row: (r) => `<tr>
    <td><b>${esc(r.in)}</b><div class="sub">${esc(r.title || "")}</div></td>
    <td>${esc(r.rel)}</td>
    <td>${r.co.map(esc).join(", ")}</td>
    <td class="num">${fmtNum(r.trades)}</td>
    <td class="num">${r.buy ? fmtMoney(r.buy) : "—"}</td>
    <td class="num">${r.sell ? fmtMoney(r.sell) : "—"}</td>
    <td class="num ${r.net > 0 ? "pos" : r.net < 0 ? "neg" : ""}">${fmtMoney(r.net, true)}</td>
    <td class="num">${fmtDate(r.last)}</td>
  </tr>` });
  $("#inSearch").addEventListener("input", () => {
    const q = $("#inSearch").value.trim().toLowerCase();
    api.setFilter((r) => !q || (r.in || "").toLowerCase().includes(q) || (r.co || []).join(" ").toLowerCase().includes(q) || (r.title || "").toLowerCase().includes(q));
  });
}

async function initAnalysis() {
  const sum = await loadJSON("data/summary.json");
  // code table
  $("#codeTable").innerHTML = `<table><thead><tr><th>Code</th><th>Meaning</th><th class="num">Trades</th><th class="num">Value</th></tr></thead><tbody>` +
    sum.by_code.map(c => `<tr><td>${codePill(c.code, c.text)}</td><td>${esc(c.text)}</td>
      <td class="num">${fmtNum(c.count)}</td><td class="num">${c.value ? fmtMoney(c.value) : "—"}</td></tr>`).join("") + "</tbody></table>";

  // donut: buys vs sells vs other value
  const buy = sum.value.buy, sell = sum.value.sell, other = Math.max(0, sum.value.total - buy - sell);
  const tot = Math.max(buy + sell + other, 1);
  const a1 = buy / tot * 360, a2 = a1 + sell / tot * 360;
  $("#donut").style.background = `conic-gradient(var(--buy) 0 ${a1}deg, var(--sell) ${a1}deg ${a2}deg, #94a3b8 ${a2}deg 360deg)`;
  $("#donuthole").innerHTML = `<b>${fmtMoney(sum.value.total)}</b>total value`;
  $("#legend").innerHTML = `
    <div><span class="dot" style="background:var(--buy)"></span>Open-market buys (P) — <b>${fmtMoney(buy)}</b></div>
    <div><span class="dot" style="background:var(--sell)"></span>Open-market sells (S) — <b>${fmtMoney(sell)}</b></div>
    <div><span class="dot" style="background:#94a3b8"></span>Other transactions — <b>${fmtMoney(other)}</b></div>`;

  // relationship breakdown
  const maxRel = Math.max(...sum.by_rel.map(r => r.count), 1);
  $("#relBars").innerHTML = sum.by_rel.map(r =>
    `<div class="bar-row"><div class="blbl">${esc(r.rel)}</div>
     <div class="bar-track"><div class="bar-fill" style="width:${Math.round(r.count / maxRel * 100)}%"></div></div>
     <div class="bval">${fmtNum(r.count)}</div></div>`).join("");

  // security types
  barList($("#secBars"), sum.by_security.slice(0, 12)
    .map(s => ({ label: s.sec.length > 34 ? s.sec.slice(0, 32) + "…" : s.sec, value: s.count, sub: s.value ? fmtMoney(s.value) : "" })), {});

  // daily net flow chart (last 60 days)
  const daily = sum.daily.slice(-60);
  const maxA = Math.max(...daily.map(d => Math.max(d.buy, d.sell, Math.abs(d.net))), 1);
  $("#netchart").innerHTML = '<div class="chart">' + daily.map(d => {
    const bh = Math.max(1, Math.round(d.buy / maxA * 100));
    const sh = Math.max(1, Math.round(d.sell / maxA * 100));
    return `<div class="col" title="${d.d}: buys ${fmtMoney(d.buy)}, sells ${fmtMoney(d.sell)}, net ${fmtMoney(d.net, true)}">
      <div class="b buy" style="height:${bh}%"></div><div class="b sell" style="height:${sh}%"></div></div>`;
  }).join("") + "</div>";
  $("#netx").innerHTML = `<div style="display:flex;justify-content:space-between;font-size:11px;color:var(--muted)">
    <span>${daily.length ? daily[0].d : ""}</span><span>${daily.length ? daily[daily.length - 1].d : ""}</span></div>`;

  // top net tables
  $("#ntb").innerHTML = sum.net_buyers.slice(0, 10).map(c =>
    `<tr><td>${esc(c.tk || c.co)}</td><td>${esc(c.co)}</td>
     <td class="num">${fmtMoney(c.buy)}</td><td class="num">${fmtMoney(c.sell)}</td>
     <td class="num pos">${fmtMoney(c.net, true)}</td></tr>`).join("") || '<tr><td colspan="5" class="empty">—</td></tr>';
  $("#nts").innerHTML = sum.net_sellers.slice(0, 10).map(c =>
    `<tr><td>${esc(c.tk || c.co)}</td><td>${esc(c.co)}</td>
     <td class="num">${fmtMoney(c.buy)}</td><td class="num">${fmtMoney(c.sell)}</td>
     <td class="num neg">${fmtMoney(c.net, true)}</td></tr>`).join("") || '<tr><td colspan="5" class="empty">—</td></tr>';
  $("#ntp").innerHTML = sum.top_purchasers.slice(0, 10).map(p =>
    `<tr><td><b>${esc(p.in)}</b></td><td>${esc((p.co || []).join(", "))}</td>
     <td class="num">${fmtMoney(p.buy)}</td></tr>`).join("") || '<tr><td colspan="3" class="empty">—</td></tr>';

  $("#formmix").innerHTML = `<ul class="clean">
    <li><b>Form 4</b> (statement of changes in beneficial ownership): <b>${sum.counts.form4.toLocaleString()}</b> trades</li>
    <li><b>Form 5</b> (annual statement, catch-all): <b>${sum.counts.form5.toLocaleString()}</b> trades</li>
    <li>Trades with a filed ticker symbol: <b>${sum.counts.with_ticker.toLocaleString()}</b> of ${sum.counts.trades.toLocaleString()}</li></ul>`;
}

async function initAbout() {
  let stats = null;
  try { stats = await loadJSON("data/stats.json"); } catch (e) { /* optional */ }
  if (stats && stats.last_updated) {
    const last = stats.runs && stats.runs.length ? stats.runs[stats.runs.length - 1] : null;
    $("#autostats").innerHTML = `<table>
      <tr><th>Last collection run</th><td>${esc(stats.last_updated)}</td></tr>
      ${last ? `<tr><th>Window collected</th><td>${esc((last.window || []).join(" → "))} (added ${esc(last.new_filings_trades)} trades)</td></tr>
      <tr><th>HTTP requests this run</th><td>${esc(last.requests)}</td></tr>
      <tr><th>Errors this run</th><td>${esc((last.errors || []).length)}</td></tr>` : ""}
    </table>`;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const p = document.body.dataset.page;
  $$(".nav a").forEach(a => a.classList.toggle("active", a.dataset.nav === p));
  const init = { index: initIndex, trades: initTrades, companies: initCompanies,
    insiders: initInsiders, analysis: initAnalysis, about: initAbout }[p] || initAbout;
  setLastUpdated();
  init().catch(e => {
    document.querySelector("main").insertAdjacentHTML("afterbegin",
      `<div class="card empty" style="border-color:var(--sell);color:var(--sell)">Failed to load data: ${esc(String(e))}</div>`);
    console.error(e);
  });
});
"""


INDEX_BODY = """
<section class="hero">
  <h1>Every insider trade, tracked.</h1>
  <p>CEOTrades collects <strong>all</strong> Form&nbsp;4 and Form&nbsp;5 insider transaction reports filed with
  SEC&nbsp;EDGAR for publicly traded companies — automatically, with no manual input.
  Directors, officers and 10%+ owners must disclose every purchase, sale, grant and exercise within two business days.</p>
  <span class="range" data-slot="range">loading…</span>
</section>

<div class="grid g6" id="stats"></div>

<div class="grid g2" style="margin-top:16px">
  <div class="card"><h3>Latest insider activity <small>· most recent filings</small></h3><div id="latest"></div></div>
  <div class="card"><h3>Most active companies <small>· trades in dataset</small></h3><div id="topco"></div></div>
</div>

<div class="grid g2" style="margin-top:16px">
  <div class="card"><h3>Net open-market flow by company <small>· buys minus sells, top 10 by |net|</small></h3>
    <div id="netflow"></div></div>
  <div class="card"><h3>Trades filed per day <small>· last 30 days</small></h3>
    <div id="dailychart"></div><div id="dailyx"></div><div id="dailynote" class="count-note"></div></div>
</div>

<div class="grid g2" style="margin-top:16px">
  <div class="card"><h3>Top net buyers <small>· open-market P − S</small></h3><div id="buyers"></div></div>
  <div class="card"><h3>Top net sellers <small>· open-market P − S</small></h3><div id="sellers"></div></div>
</div>
"""

TRADES_BODY = """
<section class="hero">
  <h1>All insider trades</h1>
  <p>Every transaction row from every collected Form 4 / Form 5 filing. Search, filter and sort freely — the full CSV is one click away.</p>
</section>

<div class="toolbar">
  <input type="search" id="q" placeholder="Search insider, company, ticker, title…">
  <select id="monthSel" title="Data window"></select>
  <select id="codeSel" title="Transaction code"></select>
  <select id="sideSel" title="Side"></select>
  <select id="formSel" title="Form">
    <option value="">Form 4 + 5</option><option value="4">Form 4</option><option value="5">Form 5</option>
  </select>
  <label class="sub" style="display:flex;align-items:center;gap:6px;cursor:pointer">
    <input type="checkbox" id="derChk" style="width:auto"> derivatives only</label>
  <span class="spacer"></span>
  <a class="btn ghost" href="data/trades.csv" download>⬇ Download full CSV</a>
</div>
<div id="tradesTable"></div>
"""

COMPANIES_BODY = """
<section class="hero">
  <h1>Companies</h1>
  <p>Insider activity aggregated per public company. “Net $” is open-market purchases minus sales (codes P and S); grants, exercises and tax withholding are shown but not netted.</p>
</section>
<div class="grid g2" style="margin-bottom:16px">
  <div class="card"><h3>Top net buyers</h3><div id="nb"></div></div>
  <div class="card"><h3>Top net sellers</h3><div id="ns"></div></div>
</div>
<div class="toolbar"><input type="search" id="coSearch" placeholder="Filter by ticker or company…"><span class="spacer"></span></div>
<div id="coTable"></div>
"""

INSIDERS_BODY = """
<section class="hero">
  <h1>Insiders</h1>
  <p>Directors, executive officers and 10%+ beneficial owners — the people required by Section 16 of the Exchange Act to report their trades.</p>
</section>
<div class="toolbar"><input type="search" id="inSearch" placeholder="Filter by name, title or company…"><span class="spacer"></span></div>
<div id="inTable"></div>
"""

ANALYSIS_BODY = """
<section class="hero">
  <h1>Analysis</h1>
  <p>Where the insider dollars are going: transaction-type mix, net flows, daily rhythm and the biggest players.</p>
</section>

<div class="grid g2">
  <div class="card"><h3>Transaction value by type</h3>
    <div class="donut-wrap"><div class="donut" id="donut"><div class="hole" id="donuthole"></div></div>
    <div class="legend" id="legend"></div></div></div>
  <div class="card"><h3>By transaction code</h3><div id="codeTable" class="table-scroll"></div></div>
</div>

<div class="grid g2" style="margin-top:16px">
  <div class="card"><h3>Daily buys vs sells <small>· last 60 days, $</small></h3>
    <div id="netchart"></div><div id="netx"></div></div>
  <div class="card"><h3>Filing mix</h3><div id="formmix" class="doc"></div>
    <h3 style="margin-top:16px">Reporter relationship</h3><div id="relBars"></div></div>
</div>

<div class="grid g2" style="margin-top:16px">
  <div class="card"><h3>Top 10 net buyers <small>· P − S $</small></h3><div class="table-scroll">
    <table><thead><tr><th>Ticker</th><th>Company</th><th class="num">Buys $</th><th class="num">Sells $</th><th class="num">Net $</th></tr></thead><tbody id="ntb"></tbody></table></div></div>
  <div class="card"><h3>Top 10 net sellers <small>· P − S $</small></h3><div class="table-scroll">
    <table><thead><tr><th>Ticker</th><th>Company</th><th class="num">Buys $</th><th class="num">Sells $</th><th class="num">Net $</th></tr></thead><tbody id="nts"></tbody></table></div></div>
</div>

<div class="grid g2" style="margin-top:16px">
  <div class="card"><h3>Biggest open-market buyers <small>· insiders, code P</small></h3><div class="table-scroll">
    <table><thead><tr><th>Insider</th><th>Companies</th><th class="num">Purchases $</th></tr></thead><tbody id="ntp"></tbody></table></div></div>
  <div class="card"><h3>Most traded security types</h3><div id="secBars"></div></div>
</div>
"""

ABOUT_BODY = """
<section class="hero">
  <h1>How CEOTrades works</h1>
  <p>Fully automated collection, organization and analysis of insider trading disclosures. No manual data entry, ever.</p>
</section>

<div class="grid g2">
  <div class="card doc">
    <h3>1 · Data source</h3>
    <p>All data comes from the <a href="https://www.sec.gov/edgar/searchedgar/companysearch" target="_blank" rel="noopener">U.S. SEC EDGAR</a> system:
    <ul class="clean">
      <li><b>Form 4</b> — Statement of Changes in Beneficial Ownership, filed within 2 business days of any insider transaction.</li>
      <li><b>Form 5</b> — annual statement that catches transactions not previously reported.</li>
    </ul>
    These are the official disclosure forms under Section 16 of the Securities Exchange Act of 1934 — every director,
    executive officer and &gt;10% shareholder of a public company must file them.</p>
    <h3>2 · Enumeration</h3>
    <p>Each day the pipeline reads the SEC’s <b>daily master index</b>
    (<code>sec.gov/Archives/edgar/daily-index/YYYY/MM/DD/master.json</code>) to list every Form 4/5 filed that day,
    with the EDGAR <b>full-text search API</b> (<code>efts.sec.gov</code>) as an automatic fallback and cross-check.</p>
    <h3>3 · Extraction</h3>
    <p>Each filing’s structured XML is downloaded and parsed (issuer, insider, relationship, and every
    non-derivative and derivative transaction: date, code, shares, price, shares after).</p>
    <h3>4 · Storage & publishing</h3>
    <p>Rows are merged into a growing dataset (amendments supersede originals) and this static site is regenerated
    and committed by <b>GitHub Actions every night at 04:00 UTC</b> — after EDGAR’s nightly index build.
    The site deploys automatically via GitHub Pages.</p>
    <h3>Automation status</h3>
    <div id="autostats" class="table-scroll"><div class="empty">stats unavailable</div></div>
  </div>
  <div class="card doc">
    <h3>Data dictionary</h3>
    <table>
      <tr><th>Field</th><th>Meaning</th></tr>
      <tr><td>fd</td><td>Filing date (EDGAR acceptance, YYYY-MM-DD)</td></tr>
      <tr><td>td</td><td>Transaction date (when the trade happened)</td></tr>
      <tr><td>code / ct</td><td>Transaction code and its official meaning</td></tr>
      <tr><td>side</td><td>buy / sell / exercise / grant / withholding / gift / other</td></tr>
      <tr><td>sh / px / val</td><td>Shares transacted, price per share, value = sh × px (when priced)</td></tr>
      <tr><td>ad</td><td>A = acquired, D = disposed</td></tr>
      <tr><td>af</td><td>Shares owned following the transaction</td></tr>
      <tr><td>di</td><td>D = direct ownership, I = indirect</td></tr>
      <tr><td>der</td><td>1 = derivative security (option, RSU, warrant, …)</td></tr>
      <tr><td>under</td><td>Underlying security for derivatives</td></tr>
      <tr><td>acc</td><td>SEC accession number (links to the filing on EDGAR)</td></tr>
    </table>
    <h3>Transaction codes</h3>
    <table>
      <tr><th>Code</th><th>Meaning (official)</th></tr>
      <tr><td>P</td><td>Open market or private purchase</td></tr>
      <tr><td>S</td><td>Open market or private sale</td></tr>
      <tr><td>A</td><td>Grant, award, or other acquisition</td></tr>
      <tr><td>M</td><td>Exercise or conversion of derivative security</td></tr>
      <tr><td>F</td><td>Payment of exercise price / tax by delivering or withholding securities</td></tr>
      <tr><td>G</td><td>Bona fide gift</td></tr>
      <tr><td>C</td><td>Conversion of derivative security</td></tr>
      <tr><td>D</td><td>Sale or disposition back to the issuer</td></tr>
      <tr><td>J</td><td>Other (described in footnotes)</td></tr>
      <tr><td>Others</td><td>V, I, E, H, O, X, L, W, Z, K, U — see CSV for each row</td></tr>
    </table>
    <h3>Caveats</h3>
    <ul class="clean">
      <li>“Net flow” = code P value minus code S value. Grants (A), exercises (M) and tax withholding (F) change holdings but are not open-market flow.</li>
      <li>Many sales follow pre-arranged <b>10b5-1 plans</b>; the data shows the trade, footnotes on EDGAR explain the plan.</li>
      <li>Some transactions are reported without a price (grants, gifts) — value is blank, not zero.</li>
      <li>Amendments (4/A) supersede the original filing; the latest version is kept.</li>
      <li>Filings for issuers with no U.S. ticker still appear (ticker shown as —).</li>
    </ul>
    <h3>Run it yourself</h3>
    <p><code>python3 collector/collect.py --days 3</code> then <code>python3 collector/build_site.py</code>.
    Standard library only. SEC fair-access policy: declared User-Agent, ≤10 req/s — this project self-throttles to 8/s.</p>
  </div>
</div>
"""


def build():
    dataset_path = os.path.join(DATA_IN, "trades.json")
    stats_path = os.path.join(DATA_IN, "stats.json")
    if not os.path.exists(dataset_path):
        print(f"ERROR: no dataset at {dataset_path} — run collector/collect.py first.", file=sys.stderr)
        sys.exit(1)
    with open(dataset_path, "r", encoding="utf-8") as f:
        rows = json.load(f)
    stats = None
    if os.path.exists(stats_path):
        with open(stats_path, "r", encoding="utf-8") as f:
            stats = json.load(f)

    print(f"Aggregating {len(rows)} trades …")
    summary = aggregate(rows)
    n_recent, n_months = write_data_outputs(rows, summary, stats)
    print(f"Wrote data outputs: recent={n_recent} rows, {n_months} month files, CSV rows={len(rows)}")

    os.makedirs(SITE_CSS, exist_ok=True)
    os.makedirs(SITE_JS, exist_ok=True)
    with open(os.path.join(SITE_CSS, "site.css"), "w", encoding="utf-8") as f:
        f.write(CSS)
    with open(os.path.join(SITE_JS, "app.js"), "w", encoding="utf-8") as f:
        f.write(JS)

    pages = [
        ("index.html", "Insider Trade Dashboard", "index", INDEX_BODY,
         "Dashboard of every insider trade filed with SEC EDGAR: buys, sells, grants and exercises at publicly traded companies."),
        ("trades.html", "All Trades", "trades", TRADES_BODY,
         "Searchable, filterable table of every insider transaction row."),
        ("companies.html", "Companies", "companies", COMPANIES_BODY,
         "Insider activity aggregated per publicly traded company."),
        ("insiders.html", "Insiders", "insiders", INSIDERS_BODY,
         "Directors, officers and 10%+ owners and their trades."),
        ("analysis.html", "Analysis", "analysis", ANALYSIS_BODY,
         "Insider flow analytics: code mix, net buyers and sellers, daily rhythm."),
        ("about.html", "About & Methodology", "about", ABOUT_BODY,
         "How CEOTrades automatically collects and verifies insider trade data."),
    ]
    for fn, title, active, body, desc in pages:
        with open(os.path.join(ROOT, fn), "w", encoding="utf-8") as f:
            f.write(page(title, active, body, desc))
        print(f"Wrote {fn}")
    print("Site build complete.")


if __name__ == "__main__":
    build()
