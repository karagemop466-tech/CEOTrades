#!/usr/bin/env python3
"""Verify the static site against the generated data.

Checks that every data file the pages fetch exists, that every field the
JavaScript reads is actually present in that data, and that the HTML is
internally consistent (nav links resolve, assets exist, no stray placeholders).
Run after build_data.py + build_pages.py.
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

FAILED = []


def check(name, ok, detail=""):
    if ok:
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name}{(' — ' + detail) if detail else ''}")
        print(f"  FAIL {name} {detail}")


def load(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return json.load(f)


PAGES = ["index.html", "paper.html", "trades.html", "companies.html",
         "insiders.html", "activity.html", "analysis.html", "irregularities.html", "about.html"]


def main() -> int:
    print("1. pages exist and are well-formed shells")
    for p in PAGES:
        path = os.path.join(ROOT, p)
        if not os.path.exists(path):
            check(f"{p} exists", False)
            continue
        html = open(path, encoding="utf-8").read()
        check(f"{p} has doctype", html.startswith("<!DOCTYPE html>"))
        check(f"{p} closes html", html.rstrip().endswith("</html>"))
        check(f"{p} loads app.js", 'src="js/app.js"' in html)
        check(f"{p} loads css", 'href="css/site.css"' in html)
        check(f"{p} tags open/close evenly",
              html.count("<div") == html.count("</div>"),
              f"{html.count('<div')} open vs {html.count('</div>')} close")
        # every nav target must be a real file
        for href in set(re.findall(r'href="([a-z_]+\.html)"', html)):
            check(f"{p} → {href} exists", os.path.exists(os.path.join(ROOT, href)))

    print("2. assets")
    for a in ("css/site.css", "js/app.js"):
        check(f"{a} exists", os.path.exists(os.path.join(ROOT, a)))
    js = open(os.path.join(ROOT, "js/app.js"), encoding="utf-8").read()
    for fn in ("fmtInt", "fmtMoney", "fmtPct", "pctCls", "esc", "titleCase",
               "edgar", "sideBadge", "load", "fail", "Table", "toCSV",
               "download", "debounce", "param", "statCard", "fmtPx", "fmtUSD"):
        check(f"CT.{fn} exported", re.search(rf"\b{fn}\s*:", js) is not None)

    print("3. data files referenced by the pages all exist")
    refs = set()
    for p in PAGES:
        html = open(os.path.join(ROOT, p), encoding="utf-8").read()
        refs |= set(re.findall(r"CT\.load\('([^']+)'\)", html))
    for r in sorted(refs):
        check(f"{r} present", os.path.exists(os.path.join(ROOT, r)))

    print("4. summary.json contract")
    s = load("data/summary.json")
    for k in ("generated", "range", "counts", "value", "by_code", "by_rel",
              "yearly", "months"):
        check(f"summary.{k}", k in s)
    for k in ("trades", "filings", "companies", "insiders", "buys", "sells"):
        check(f"summary.counts.{k}", k in s["counts"])
    for k in ("total", "buy", "sell", "net"):
        check(f"summary.value.{k}", k in s["value"])
    check("summary.range.from/to", "from" in s["range"] and "to" in s["range"])
    if s["by_code"]:
        check("by_code rows have code/n/v",
              all({"code", "n", "v"} <= set(r) for r in s["by_code"]))
    if s["yearly"]:
        check("yearly rows have y/n", all({"y", "n"} <= set(r) for r in s["yearly"]))
        for y in s["yearly"]:
            check(f"csv/trades-{y['y']}.csv.gz exists",
                  os.path.exists(os.path.join(ROOT, "data", "csv", f"trades-{y['y']}.csv.gz")))

    print("4b. audit / irregularities contract")
    a = load("data/audit.json")
    for k in ("generated", "target_year", "official_sources", "completeness", "counts", "integrity", "assurance"):
        check(f"audit.{k}", k in a)
    for k in ("status", "complete", "target_start", "target_end", "blockers"):
        check(f"audit.completeness.{k}", k in a["completeness"])
    for k in ("rows", "filings", "companies", "insiders", "with_ticker"):
        check(f"audit.counts.{k}", k in a["counts"])
    check("audit does not detect manual/synthetic generator",
          not a["integrity"].get("manual_data_guard", {}).get("manual_or_synthetic_generators_detected", True))
    irr = load("data/irregularities.json")
    check("irregularities is a list", isinstance(irr, list))
    if irr:
        need = {"id", "category", "severity", "summary", "details", "rule", "evidence", "review_status"}
        check("irregularity rows complete", need <= set(irr[0]), str(need - set(irr[0])))

    print("4c. insider_activity.json contract")
    act = load("data/insider_activity.json")
    for k in ("generated", "target_year", "summary", "rows", "truncated", "row_count"):
        check(f"activity.{k}", k in act)
    for k in ("insider_company_pairs", "buy_sell_pairs", "with_reported_common_shares",
              "with_priced_holdings", "scope", "full_csv"):
        check(f"activity.summary.{k}", k in act["summary"])
    check("insider_activity rows is a list", isinstance(act["rows"], list))
    check("activity row_count matches summary",
          act["row_count"] == act["summary"].get("insider_company_pairs"))
    check("activity rows respect truncation flag",
          (len(act["rows"]) == act["row_count"]) or act["truncated"])
    check("insider_activity.csv.gz exists",
          os.path.exists(os.path.join(ROOT, "data", "insider_activity.csv.gz")))
    if act["rows"]:
        need = {"id", "insider", "pcik", "co", "tk", "icik", "buy_n", "sell_n",
                "buy_v", "sell_v", "net_v", "buy_sell_overlap", "reported_common_shares",
                "holding_value", "valuation_status", "review_links", "portfolio_scope"}
        check("activity rows complete", need <= set(act["rows"][0]), str(need - set(act["rows"][0])))

    print("5. paper/summary.json contract")
    p = load("data/paper/summary.json")
    for k in ("stake", "counts", "capital", "roi", "gap", "horizons",
              "by_role", "by_size", "by_year", "best", "worst", "rule", "findings", "outcomes"):
        check(f"paper.{k}", k in p)
    for k in ("winner_log", "loser_log", "realized", "winners", "losers", "unclassified"):
        check(f"paper.outcomes.{k}", k in p.get("outcomes", {}))
    for fn in ("winners.json", "losers.json"):
        check(f"paper/{fn} exists", os.path.exists(os.path.join(ROOT, "data", "paper", fn)))
    for k in ("signals", "open", "awaiting_entry", "no_price"):
        check(f"paper.counts.{k}", k in p["counts"])
    for k in ("deployed", "value", "pnl", "roi"):
        check(f"paper.capital.{k}", k in p["capital"])
    for h in ("r1", "r5", "r21", "r63", "r252"):
        check(f"paper.horizons.{h}", h in p["horizons"])
    for k in ("entry", "exit", "costs"):
        check(f"paper.rule.{k}", k in p["rule"])
    for k in ("entry_rule_failures", "arithmetic_failures", "price_sources", "line_by_line_review"):
        check(f"paper.verification.{k}", k in p.get("verification", {}))
    check("paper entry-rule verification clean",
          p.get("verification", {}).get("entry_rule_failures", 0) == 0)
    check("paper arithmetic verification clean",
          p.get("verification", {}).get("arithmetic_failures", 0) == 0)
    for name, rows in (("by_role", p["by_role"]), ("by_size", p["by_size"])):
        if rows:
            check(f"{name} rows carry stats",
                  all({"n", "median", "mean", "win", "p25", "p75"} <= set(r) for r in rows))

    print("6. positions contract")
    pos = load("data/paper/positions.json")
    check("positions is a list", isinstance(pos, list))
    if pos:
        need = {"fd", "entry_d", "tk", "co", "insider", "insider_val", "insider_px",
                "entry_px", "gap", "last_px", "pnl", "roi", "acc", "status", "icik",
                "entry_rule_status", "entry_check", "price_src", "edgar_url"}
        missing = need - set(pos[0])
        check("position rows expose every column the UI renders", not missing, str(missing))
        opens = [x for x in pos if x["status"] == "open"]
        if opens:
            o = opens[0]
            # Recompute the arithmetic independently.
            sh = o["shares"]
            check("shares = stake / entry_px",
                  abs(sh - o["stake"] / o["entry_px"]) < 1e-3)
            if o.get("last_px") is not None:
                check("mtm = shares * last_px",
                      abs(o["mtm"] - sh * o["last_px"]) < 0.05)
                check("pnl = mtm - stake", abs(o["pnl"] - (o["mtm"] - o["stake"])) < 0.05)
                check("roi = pnl / stake",
                      abs(o["roi"] - o["pnl"] / o["stake"]) < 1e-3)
            check("entry is strictly after the filing date", o["entry_d"] > o["fd"])

    print("7. companies / insiders contract")
    cos = load("data/companies.json")
    check("companies is a list", isinstance(cos, list))
    if cos:
        need = {"cik", "co", "tk", "n", "ins", "buy_n", "sell_n", "buy_v",
                "sell_v", "net_v", "last"}
        check("company rows complete", need <= set(cos[0]),
              str(need - set(cos[0])))
        check("net_v = buy_v - sell_v",
              abs((cos[0]["net_v"] or 0) - ((cos[0]["buy_v"] or 0) - (cos[0]["sell_v"] or 0))) < 0.02)
        # every company must resolve to its detail bucket, using the same
        # modulo the front-end JS applies
        for c in cos[:50]:
            digits = re.sub(r"\D", "", str(c["cik"])) or "0"
            b = int(digits) % 64
            bp = os.path.join(ROOT, "data", "co", f"{b}.json")
            if not os.path.exists(bp):
                check(f"bucket {b} for {c['tk'] or c['cik']}", False, "missing file")
                continue
            data = json.load(open(bp, encoding="utf-8"))
            check(f"detail rows for {c['tk'] or c['cik']}", str(c["cik"]) in data)

    ins = load("data/insiders.json")
    check("insiders is a list", isinstance(ins, list))
    if ins:
        need = {"in", "rel", "cos", "n", "buy_n", "sell_n", "buy_v", "sell_v",
                "net_v", "last"}
        check("insider rows complete", need <= set(ins[0]), str(need - set(ins[0])))

    print("8. cross-checks between artifacts")
    check("summary company count matches companies.json",
          s["counts"]["companies"] == len(cos))
    check("paper open count matches positions with status open",
          p["counts"]["open"] == len([x for x in pos if x["status"] == "open"])
          or len(pos) < p["counts"]["signals"])
    if p["counts"]["open"]:
        check("deployed = open * stake",
              abs(p["capital"]["deployed"] - p["counts"]["open"] * p["stake"]) < 1.0)
        check("capital pnl = value - deployed",
              abs(p["capital"]["pnl"] - (p["capital"]["value"] - p["capital"]["deployed"])) < 1.0)

    print()
    if FAILED:
        print(f"FAILED ({len(FAILED)}):")
        for f in FAILED:
            print("  - " + f)
        return 1
    print("ALL SITE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
