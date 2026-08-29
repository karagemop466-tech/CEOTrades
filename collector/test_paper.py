#!/usr/bin/env python3
"""Offline verification of the paper-trading simulation and data builder.

Every number below is computed by hand in the test and compared to the engine.
No network access.
"""
from __future__ import annotations

import csv
import gzip
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_data as bd  # noqa: E402
import bulk_backfill as bb  # noqa: E402
import store  # noqa: E402

FAILED = []


def check(name, got, want, tol=None):
    ok = (abs(got - want) <= tol) if (tol is not None and got is not None
                                      and want is not None) else (got == want)
    if ok:
        print(f"  ok   {name} = {got!r}")
    else:
        FAILED.append(f"{name}: got {got!r}, want {want!r}")
        print(f"  FAIL {name}: got {got!r}, want {want!r}")


def bars(spec):
    return [{"d": d, "o": o, "c": c} for d, o, c in spec]


def main() -> int:
    print("1. ticker normalisation")
    check("dot to dash", bd.yahoo_symbol("BRK.B"), "BRK-B")
    check("slash to dash", bd.yahoo_symbol("BF/B"), "BF-B")
    check("strips junk", bd.yahoo_symbol(" aapl "), "AAPL")
    check("valid", bd.valid_ticker("AAPL"), True)
    check("rejects blank", bd.valid_ticker(""), False)
    check("rejects NONE", bd.valid_ticker("NONE"), False)
    check("rejects overlong", bd.valid_ticker("ABCDEFGHIJ"), False)
    check("rejects leading digit", bd.valid_ticker("1234"), False)

    print("2. equity filter (only plain shares become paper trades)")
    check("common stock", bd.is_common_equity("Common Stock"), True)
    check("class A", bd.is_common_equity("Class A Common Stock"), True)
    check("ADR", bd.is_common_equity("American Depositary Shares"), True)
    check("option excluded", bd.is_common_equity("Stock Option (right to buy)"), False)
    check("RSU excluded", bd.is_common_equity("Restricted Stock Units"), False)
    check("warrant excluded", bd.is_common_equity("Warrant to purchase"), False)
    check("preferred excluded", bd.is_common_equity("Series A Preferred Stock"), False)
    check("convertible note excluded", bd.is_common_equity("Convertible Note"), False)

    print("3. entry selection — first OPEN strictly AFTER the filing date")
    b = bars([("2025-08-11", 10.0, 10.5),
              ("2025-08-12", 11.0, 11.5),   # filing day — must NOT be the fill
              ("2025-08-13", 12.0, 12.5),   # this is the fill
              ("2025-08-14", 13.0, 13.5)])
    check("skips filing day", bd.next_open_after(b, "2025-08-12"), 2)
    # Filing on Sunday 2025-08-10: the first bar strictly after it is index 0
    # (2025-08-11), which is the correct next available session.
    check("weekend filing -> next session", bd.next_open_after(b, "2025-08-10"), 0)
    check("after last bar -> None", bd.next_open_after(b, "2025-08-14"), None)
    b2 = bars([("2025-08-13", None, 12.5), ("2025-08-14", 13.0, 13.5)])
    check("skips bar with no open", bd.next_open_after(b2, "2025-08-12"), 1)

    print("4. simulation arithmetic")
    # $10,000 at an open of 12.50 = 800 shares exactly.
    px = [("2025-08-12", 11.0, 11.5),
          ("2025-08-13", 12.50, 13.00),      # entry open 12.50, +1 close 13.00
          ("2025-08-14", 13.10, 14.00),      # r1 close 14.00
          ("2025-08-15", 14.00, 15.00),
          ("2025-08-18", 15.00, 16.00),
          ("2025-08-19", 16.00, 20.00)]      # last close 20.00 -> r5
    sig = {"id": "X", "tk": "T", "co": "Test", "fd": "2025-08-12",
           "td": "2025-08-08", "insider_px": 10.00, "insider_sh": 100.0,
           "insider_val": 1000.0, "rel": "Officer", "title": "CEO"}
    p = bd.simulate(sig, bars(px), "2025-08-19")
    check("status", p["status"], "open")
    check("entry date", p["entry_d"], "2025-08-13")
    check("entry price", p["entry_px"], 12.50)
    check("shares = 10000/12.50", p["shares"], 800.0)
    check("mtm = 800 * 20.00", p["mtm"], 16000.00)
    check("pnl = 16000 - 10000", p["pnl"], 6000.00)
    check("roi = +60%", p["roi"], 0.6)
    check("gap = 12.50/10.00 - 1", p["gap"], 0.25)
    check("r1 = 14.00/12.50 - 1", p["r1"], 0.12)
    # Entry is index 1 and the series ends at index 5, so the 5-session horizon
    # (index 6) does not exist yet: the engine must report None, not guess.
    check("r5 unavailable -> None (no fabrication)", p["r5"], None)
    # 4 sessions after entry the close is 20.00 -> the +60% mark-to-market.
    check("last close drives roi", p["last_px"], 20.0)
    check("delay filing->entry days", p["delay_fd_entry"], 1)
    check("delay txn->filing days", p["delay_td_fd"], 4)
    check("hold sessions = len-1-entry_idx", p["hold"], 4)

    print("5. no-lookahead guarantees")
    # asof BEFORE any post-filing session exists -> must not open a position.
    p2 = bd.simulate(sig, bars(px), "2025-08-12")
    check("asof=filing day -> awaiting_entry", p2["status"], "awaiting_entry")
    check("no entry price leaked", p2["entry_px"], None)
    check("no roi leaked", p2["roi"], None)
    # Truncating history at entry day must not change the entry price.
    p3 = bd.simulate(sig, bars(px), "2025-08-13")
    check("entry stable under truncation", p3["entry_px"], 12.50)
    check("roi uses entry-day close only", p3["roi"], round(13.00 / 12.50 - 1, 4))
    check("future horizon unknown", p3["r5"], None)
    # No bars at all.
    p4 = bd.simulate(sig, [], "2025-08-19")
    check("no bars -> no_price", p4["status"], "no_price")

    print("5b. price-window guards (no phantom entries from short history)")
    # A 1-year-style fetch for an older filing: the first bar AFTER the filing
    # date is months away. Filling there would fabricate a wrong entry.
    short = bars([("2025-11-10", 30.0, 30.5),
                  ("2025-11-11", 31.0, 31.5),
                  ("2025-11-12", 32.0, 32.5)])
    g1 = bd.simulate(sig, short, "2025-11-12")
    check("late history -> no_price", g1["status"], "no_price")
    check("late history -> entry_window_missing", g1["entry_rule_status"], "entry_window_missing")
    check("late history -> no entry price", g1["entry_px"], None)
    check("late history -> no roi", g1["roi"], None)
    check("late history -> gap recorded", g1["entry_gap_days"], 90)
    # Series that ends before the filing date: delisted/uncovered, not pending.
    old = bars([("2025-07-01", 10.0, 10.5), ("2025-07-02", 11.0, 11.5)])
    g2 = bd.simulate(sig, old, "2025-11-12")
    check("series ends pre-filing -> no_price", g2["status"], "no_price")
    check("series ends pre-filing -> no_bars_after_filing", g2["entry_rule_status"], "no_bars_after_filing")
    check("series ends pre-filing -> no entry price", g2["entry_px"], None)
    # A 3-day gap (filing Friday -> Tuesday session after a holiday Monday) is
    # normal and must still open.
    ok_bars = bars([("2025-08-12", 11.0, 11.5),
                    ("2025-08-15", 12.50, 13.00),
                    ("2025-08-18", 13.00, 13.50)])
    g3 = bd.simulate(sig, ok_bars, "2025-08-18")
    check("3-day weekend/holiday gap still opens", g3["status"], "open")
    check("3-day gap entry price", g3["entry_px"], 12.50)
    # The standalone engine (paper_trade.simulate_one) must behave the same.
    import paper_trade as pt
    sig2 = dict(sig)
    sig2.update({"acc": "A1", "form": "4", "amend": 0, "icik": "111", "pcik": "222",
                 "insider": "JANE DOE", "sec": "Common Stock", "lots": 1})
    q1 = pt.simulate_one(sig2, short, "2025-11-12")
    check("engine: late history -> no_price", q1["status"], "no_price")
    check("engine: late history -> no entry price", q1["entry_px"], None)
    q2 = pt.simulate_one(sig2, old, "2025-11-12")
    check("engine: series ends pre-filing -> no_price", q2["status"], "no_price")
    q3 = pt.simulate_one(sig2, bars(px), "2025-08-19")
    check("engine: normal case still opens", q3["status"], "open")
    check("engine: normal entry price", q3["entry_px"], 12.5)

    print("6. statistics")
    s = bd.stats([0.1, -0.1, 0.3, 0.5, -0.2])
    check("n", s["n"], 5)
    check("mean", s["mean"], 0.12)
    check("median", s["median"], 0.1)
    check("win rate 3/5", s["win"], 0.6)
    check("min", s["min"], -0.2)
    check("max", s["max"], 0.5)
    check("empty", bd.stats([])["n"], 0)
    check("None-safe", bd.stats([None, 0.2, None])["n"], 1)

    print("7. buckets")
    check("size <10k", bd.size_bucket(5_000), "<$10k")
    check("size 100k", bd.size_bucket(100_000), "$50k–250k")
    check("size 5M", bd.size_bucket(5_000_000), ">$1M")
    check("role CEO", bd.role_bucket("Officer", "Chief Executive Officer"), "CEO")
    check("role CFO", bd.role_bucket("Officer", "CFO"), "CFO")
    check("role director", bd.role_bucket("Director", ""), "Director")
    check("role 10%", bd.role_bucket("10% Owner", ""), "10% owner")

    print("8. end-to-end build on a synthetic store")
    tmp = tempfile.mkdtemp()
    din, dout = os.path.join(tmp, "in"), os.path.join(tmp, "out")
    os.makedirs(din)
    rows = [
        # Two priced purchase lots in ONE filing -> must collapse to ONE $10k trade.
        {"fd": "2025-08-12", "td": "2025-08-08", "form": "4", "amend": 0,
         "acc": "A1", "co": "Test Co", "tk": "T", "icik": "111", "in": "JANE DOE",
         "pcik": "222", "own_n": 1, "rel": "Officer", "title": "Chief Executive Officer",
         "code": "P", "ct": "buy", "side": "buy", "sec": "Common Stock",
         "sh": 60.0, "px": 10.0, "val": 600.0, "ad": "A", "af": 1000.0, "di": "D",
         "der": 0, "period": "2025-08-08", "nature": "", "under": "",
         "under_sh": None, "xp": None, "exp": "", "timely": "", "swap": ""},
        {"fd": "2025-08-12", "td": "2025-08-08", "form": "4", "amend": 0,
         "acc": "A1", "co": "Test Co", "tk": "T", "icik": "111", "in": "JANE DOE",
         "pcik": "222", "own_n": 1, "rel": "Officer", "title": "Chief Executive Officer",
         "code": "P", "ct": "buy", "side": "buy", "sec": "Common Stock",
         "sh": 40.0, "px": 10.0, "val": 400.0, "ad": "A", "af": 1000.0, "di": "D",
         "der": 0, "period": "2025-08-08", "nature": "", "under": "",
         "under_sh": None, "xp": None, "exp": "", "timely": "", "swap": ""},
        # A sale — must be counted but never paper-traded.
        {"fd": "2025-08-12", "td": "2025-08-08", "form": "4", "amend": 0,
         "acc": "A2", "co": "Test Co", "tk": "T", "icik": "111", "in": "JOHN ROE",
         "pcik": "333", "own_n": 1, "rel": "Director", "title": "",
         "code": "S", "ct": "sell", "side": "sell", "sec": "Common Stock",
         "sh": 10.0, "px": 20.0, "val": 200.0, "ad": "D", "af": 5.0, "di": "D",
         "der": 0, "period": "2025-08-08", "nature": "", "under": "",
         "under_sh": None, "xp": None, "exp": "", "timely": "", "swap": ""},
        # Same insider later sells common stock — counted as buy/sell overlap,
        # never paper-traded, and it updates the reported held-after balance.
        {"fd": "2025-08-14", "td": "2025-08-13", "form": "4", "amend": 0,
         "acc": "A4", "co": "Test Co", "tk": "T", "icik": "111", "in": "JANE DOE",
         "pcik": "222", "own_n": 1, "rel": "Officer", "title": "Chief Executive Officer",
         "code": "S", "ct": "sell", "side": "sell", "sec": "Common Stock",
         "sh": 20.0, "px": 15.0, "val": 300.0, "ad": "D", "af": 900.0, "di": "D",
         "der": 0, "period": "2025-08-13", "nature": "", "under": "",
         "under_sh": None, "xp": None, "exp": "", "timely": "", "swap": ""},
        # An option grant — counted, never paper-traded.
        {"fd": "2025-08-12", "td": "2025-08-08", "form": "4", "amend": 0,
         "acc": "A3", "co": "Test Co", "tk": "T", "icik": "111", "in": "JANE DOE",
         "pcik": "222", "own_n": 1, "rel": "Officer", "title": "Chief Executive Officer",
         "code": "A", "ct": "grant", "side": "grant", "sec": "Stock Option (right to buy)",
         "sh": 500.0, "px": None, "val": None, "ad": "A", "af": 500.0, "di": "D",
         "der": 1, "period": "2025-08-08", "nature": "", "under": "Common Stock",
         "under_sh": 500.0, "xp": 12.0, "exp": "2030-01-01", "timely": "", "swap": ""},
    ]
    orig_data = bb.DATA
    try:
        bb.DATA = din
        bb.merge_into_store(rows)
        got = list(store.iter_rows(din))
        check("store streams all rows", len(got), 5)
        check("numeric coercion from CSV", got[0]["sh"] in (60.0, 40.0, 10.0, 500.0), True)

        # Seed the price cache so the build runs fully offline.
        bd.PRICE_CACHE = os.path.join(tmp, "prices")
        os.makedirs(bd.PRICE_CACHE)
        bd.save_bars("T", bars(px), "test")

        agg = bd.collect(din)
        check("total rows aggregated", agg["totals"]["n"], 5)
        check("distinct filings", agg["filings"], 4)
        check("companies", len(agg["companies"]), 1)
        check("insiders", len(agg["insiders"]), 2)
        check("buy value totalled", round(agg["totals"]["buy"], 2), 1000.00)
        check("sell value totalled", round(agg["totals"]["sell"], 2), 500.00)
        check("two P lots -> one signal", len(agg["signals"]), 1)
        sg = list(agg["signals"].values())[0]
        check("signal aggregates shares", sg["insider_sh"], 100.0)
        check("signal aggregates value", sg["insider_val"], 1000.0)
        check("signal lot count", sg["lots"], 2)

        sys.argv = ["build_data", "--data", din, "--out", dout, "--offline"]
        rc = bd.main()
        check("build exit code", rc, 0)

        summary = json.load(open(os.path.join(dout, "summary.json")))
        check("summary trade count", summary["counts"]["trades"], 5)
        check("summary companies", summary["counts"]["companies"], 1)
        check("summary net flow", summary["value"]["net"], 500.0)

        paper = json.load(open(os.path.join(dout, "paper", "summary.json")))
        check("one paper position", paper["counts"]["open"], 1)
        check("deployed = one $10k stake", paper["capital"]["deployed"], 10000.0)
        check("paper value = 800sh * 20.00", paper["capital"]["value"], 16000.0)
        check("paper roi +60%", paper["capital"]["roi"], 0.6)
        check("paper entry verification clean", paper["verification"]["entry_rule_failures"], 0)
        check("paper arithmetic verification clean", paper["verification"]["arithmetic_failures"], 0)
        check("findings generated", len(paper["findings"]) > 0, True)

        portfolios = json.load(open(os.path.join(dout, "insider_portfolios.json")))
        check("portfolio rows", len(portfolios["rows"]), 2)
        jane_p = next(r for r in portfolios["rows"] if r["pcik"] == "222")
        check("portfolio issuer count", jane_p["issuer_n"], 1)
        check("portfolio reported shares", jane_p["reported_shares"], 900.0)
        check("portfolio marked value", jane_p["priced_value"], 18000.0)
        check("portfolio overlap flag", jane_p["overlap"], True)
        check("portfolio issuer breakdown link", jane_p["issuers"][0]["edgar_url"].startswith("https://www.sec.gov/"), True)
        check("portfolio csv exists", os.path.getsize(os.path.join(dout, "insider_portfolios.csv.gz")) > 0, True)

        activity = json.load(open(os.path.join(dout, "insider_activity.json")))
        check("activity pairs", activity["summary"]["insider_company_pairs"], 2)
        check("activity buy+sell overlap", activity["summary"]["buy_sell_pairs"], 1)
        jane = next(r for r in activity["rows"] if r["pcik"] == "222")
        check("Jane overlap true", jane["buy_sell_overlap"], True)
        check("Jane reported shares from latest held-after", jane["reported_common_shares"], 900.0)
        check("Jane holding value marked to last close", jane["holding_value"], 18000.0)
        check("activity review link emitted", bool(jane["review_links"]), True)

        cos = json.load(open(os.path.join(dout, "companies.json")))
        check("company row", cos[0]["co"], "Test Co")
        check("company trade count", cos[0]["n"], 5)
        check("company insider count", cos[0]["ins"], 2)

        ins = json.load(open(os.path.join(dout, "insiders.json")))
        check("insiders published", len(ins), 2)

        with gzip.open(os.path.join(dout, "csv", "trades-2025.csv.gz"),
                       "rt", encoding="utf-8") as f:
            hist = list(csv.DictReader(f))
        check("year CSV has every row", len(hist), 5)

        with gzip.open(os.path.join(dout, "paper", "positions.csv.gz"),
                       "rt", encoding="utf-8") as f:
            pos = list(csv.DictReader(f))
        check("positions CSV", len(pos), 1)
        check("positions CSV roi", pos[0]["roi"], "0.6")
        check("positions CSV entry verified", pos[0]["entry_rule_status"], "verified")
        check("positions CSV SEC link", pos[0]["edgar_url"].startswith("https://www.sec.gov/Archives/edgar/data/111/"), True)
    finally:
        bb.DATA = orig_data
        shutil.rmtree(tmp)

    print()
    if FAILED:
        print(f"FAILED ({len(FAILED)}):")
        for x in FAILED:
            print("  - " + x)
        return 1
    print("ALL PAPER/BUILD CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
