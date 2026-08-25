#!/usr/bin/env python3
"""
Offline tests for the paper-trading engine.

No network. Every assertion is a concrete number so a silent pass cannot
hide a lookahead or rounding bug.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paper_trade import (  # noqa: E402
    STAKE,
    analyze,
    extract_signals,
    is_common_equity,
    next_open_after,
    normalize_ticker,
    simulate_one,
    valid_ticker,
    yahoo_symbol,
)

FAILURES = []


def check(name, cond, detail=""):
    status = "ok " if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def row(**kw):
    r = {
        "acc": "0001-26-000001", "form": "4", "amend": 0,
        "fd": "2026-08-21", "td": "2026-08-20",
        "co": "ACME CORP", "tk": "ACME", "icik": "1", "pcik": "2",
        "in": "DOE JANE", "rel": "Officer", "title": "Chief Executive Officer",
        "code": "P", "sec": "Common Stock", "sh": 1000, "px": 10.0, "val": 10000,
        "der": 0, "ad": "A",
    }
    r.update(kw)
    return r


BARS = [
    {"d": "2026-08-20", "o": 9.50, "c": 9.80, "h": 10, "l": 9, "v": 1},
    {"d": "2026-08-21", "o": 10.00, "c": 10.50, "h": 11, "l": 9, "v": 1},  # filing day
    {"d": "2026-08-24", "o": 11.00, "c": 11.20, "h": 12, "l": 10, "v": 1},  # next session
    {"d": "2026-08-25", "o": 11.30, "c": 12.10, "h": 13, "l": 11, "v": 1},
    {"d": "2026-08-26", "o": 12.00, "c": 11.80, "h": 13, "l": 11, "v": 1},
    {"d": "2026-08-27", "o": 11.70, "c": 11.90, "h": 12, "l": 11, "v": 1},
    {"d": "2026-08-28", "o": 12.20, "c": 12.50, "h": 13, "l": 12, "v": 1},
    {"d": "2026-08-31", "o": 12.40, "c": 13.00, "h": 13, "l": 12, "v": 1},
]


def test_filters():
    check("ticker ACME ok", valid_ticker("ACME"))
    check("ticker BRK.B ok", valid_ticker("BRK.B"))
    check("ticker NONE rejected", not valid_ticker("NONE"))
    check("ticker empty rejected", not valid_ticker(""))
    check("ticker too long rejected", not valid_ticker("TOOLONGNAME"))
    check("yahoo BRK.B -> BRK-B", yahoo_symbol("BRK.B") == "BRK-B")
    check("yahoo slash", yahoo_symbol("BF/B") == "BF-B")
    check("normalize spaces", normalize_ticker(" brk.b ") == "BRK.B")

    check("common stock ok", is_common_equity("Common Stock", 0))
    check("class A ok", is_common_equity("Class A Common Stock", 0))
    check("ordinary ok", is_common_equity("Ordinary Shares", 0))
    check("empty non-der ok", is_common_equity("", 0))
    check("ADR ok", is_common_equity("American Depositary Shares", 0))
    check("preferred rejected", not is_common_equity("Series A Preferred Stock", 0))
    check("warrant rejected", not is_common_equity("Warrant", 0))
    check("RSU rejected", not is_common_equity("Restricted Stock Units", 0))
    check("option rejected", not is_common_equity("Stock Option (Right to Buy)", 0))
    check("derivative rejected even if common", not is_common_equity("Common Stock", 1))


def test_signals():
    rows = [
        row(),
        row(code="S", sh=500, px=11, val=5500),                 # sale — ignore
        row(code="P", der=1, sec="Call Option"),                # derivative — ignore
        row(code="P", sec="Preferred Stock", sh=10, px=25, val=250),
        row(code="A", sh=5000, px=0, val=None),                 # grant — ignore
        row(tk="NONE", sh=100, px=5, val=500),                  # bad ticker
        row(acc="0001-26-000002", tk="ZZZ", sh=50, px=20, val=1000),
        # two lots, same filing/ticker → one VWAP signal
        row(acc="0001-26-000003", tk="LOT", sh=100, px=10, val=1000, td="2026-08-19"),
        row(acc="0001-26-000003", tk="LOT", sh=300, px=12, val=3600, td="2026-08-20"),
    ]
    sigs = extract_signals(rows)
    ids = sorted(s["id"] for s in sigs)
    check("signals: only two qualifying filings",
          ids == ["0001-26-000001:ACME", "0001-26-000002:ZZZ", "0001-26-000003:LOT"],
          str(ids))
    lot = next(s for s in sigs if s["tk"] == "LOT")
    check("VWAP shares", lot["insider_sh"] == 400, str(lot["insider_sh"]))
    check("VWAP value", lot["insider_val"] == 4600, str(lot["insider_val"]))
    check("VWAP px", abs(lot["insider_px"] - 11.5) < 1e-9, str(lot["insider_px"]))
    check("earliest td kept", lot["td"] == "2026-08-19", lot["td"])
    acme = next(s for s in sigs if s["tk"] == "ACME")
    check("CEO title preserved", acme["title"] == "Chief Executive Officer")


def test_no_lookahead():
    nxt = next_open_after(BARS, "2026-08-21")
    check("entry is next session after fd, not fd itself",
          nxt is not None and nxt[0] == "2026-08-24" and nxt[1] == 11.00,
          str(nxt))
    nxt_same = next_open_after(BARS, "2026-08-24")
    check("fd on a session still waits for the NEXT open",
          nxt_same is not None and nxt_same[0] == "2026-08-25",
          str(nxt_same))
    nxt_future = next_open_after(BARS, "2026-08-31")
    check("no bar after last fd -> None (awaiting)", nxt_future is None)


def test_simulate_math():
    sig = extract_signals([row()])[0]
    # As-of filing day: next open has not happened.
    p = simulate_one(sig, BARS, asof="2026-08-21")
    check("awaiting_entry on filing day", p["status"] == "awaiting_entry", p["status"])
    check("no fill leaked on filing day", p["entry_px"] is None and p["roi"] is None)

    # As-of Monday 24th: we entered at 11.00 open, mark at 11.20 close.
    p = simulate_one(sig, BARS, asof="2026-08-24")
    check("open on next session", p["status"] == "open" and p["entry_d"] == "2026-08-24")
    check("fill is the OPEN 11.00, not close 11.20 and not insider 10.00",
          p["entry_px"] == 11.0, str(p["entry_px"]))
    shares = STAKE / 11.0
    check("shares = round(10000/11, 4)", p["shares"] == round(shares, 4), str(p["shares"]))
    check("r0 = round((11.20/11)-1, 4)", p["r0"] == round(11.20 / 11.0 - 1, 4), str(p["r0"]))
    check("r1 not yet available on entry day", p["r1"] is None)
    check("gap = 11/10 - 1 = 0.1", abs(p["gap"] - 0.1) < 1e-6, str(p["gap"]))
    check("delay td->fd = 1", p["delay_td_fd"] == 1, str(p["delay_td_fd"]))
    check("delay fd->entry = 3 (Fri->Mon)", p["delay_fd_entry"] == 3, str(p["delay_fd_entry"]))

    # As-of Aug 31: last close 13.00
    p = simulate_one(sig, BARS, asof="2026-08-31")
    check("last px is 13.00", p["last_px"] == 13.0, str(p["last_px"]))
    roi = 13.0 / 11.0 - 1.0
    check("roi = round(last/entry - 1, 4)", p["roi"] == round(roi, 4), str(p["roi"]))
    check("pnl = 10000 * roi", abs(p["pnl"] - STAKE * roi) < 0.05, str(p["pnl"]))
    check("r1 = 12.10/11 - 1", abs(p["r1"] - (12.10 / 11.0 - 1)) < 1e-6, str(p["r1"]))
    check("r5 uses 6th session after entry (idx+5)", p["r5"] is not None)
    # entry idx = 2 (2026-08-24); +5 = 2026-08-31 close 13.00
    check("r5 date is 2026-08-31", p["r5_d"] == "2026-08-31", str(p["r5_d"]))
    check("r21 still missing", p["r21"] is None)

    # Truncating bars to asof must not use later closes: simulate_one filters
    # by asof, so passing full BARS with asof=08-25 must mark at 12.10 not 13.
    p = simulate_one(sig, BARS, asof="2026-08-25")
    check("asof clips future bars", p["last_d"] == "2026-08-25" and p["last_px"] == 12.10,
          f"{p['last_d']} {p['last_px']}")

    p = simulate_one(sig, [], asof="2026-08-31")
    check("no bars -> no_price", p["status"] == "no_price")


def test_analyze():
    sig = extract_signals([row()])[0]
    p = simulate_one(sig, BARS, asof="2026-08-31")
    s = analyze([p])
    check("counts.open == 1", s["counts"]["open"] == 1)
    check("deployed 10000", s["capital"]["deployed"] == 10000)
    check("findings non-empty", len(s["findings"]) >= 1)
    check("by_role has CEO", any(x["k"] == "CEO" for x in s["by_role"]), str(s["by_role"]))


def test_never_uses_insider_px_as_fill():
    sig = extract_signals([row(px=50, val=50000)])[0]
    p = simulate_one(sig, BARS, asof="2026-08-31")
    check("insider px recorded", sig["insider_px"] == 50)
    check("fill is still market open 11, not 50", p["entry_px"] == 11.0, str(p["entry_px"]))


if __name__ == "__main__":
    print("== CEOTrades paper-trade self-test ==")
    test_filters()
    test_signals()
    test_no_lookahead()
    test_simulate_math()
    test_analyze()
    test_never_uses_insider_px_as_fill()
    print()
    if FAILURES:
        print(f"PAPER-TEST FAILED: {len(FAILURES)} failure(s): {FAILURES}")
        sys.exit(1)
    print("PAPER-TEST PASSED: all checks green.")
