#!/usr/bin/env python3
"""Offline tests for the historical backtest engine and the corporate-action
(split straddle) guards. Every number is computed by hand. No network."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import backtest as bt  # noqa: E402
import build_data as bd  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL {name} {detail}")


def mkbars(spec):
    # spec: list of (date, open, close)
    return [{"d": d, "o": o, "c": c, "h": max(o, c), "l": min(o, c)} for d, o, c in spec]


def daterange(start, n, o=10.0, c=10.0):
    import datetime as dt
    d = dt.date.fromisoformat(start)
    out = []
    for i in range(n):
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
        out.append((d.isoformat(), o, c))
        d += dt.timedelta(days=1)
    return out


def sig(fd="2024-01-03", td="2024-01-02", tk="TEST", acc="0000000000-24-000001"):
    return {"id": f"{acc}:{tk}", "acc": acc, "form": "4", "amend": 0, "fd": fd,
            "td": td, "co": "Test Co", "tk": tk, "icik": "123456", "pcik": "654321",
            "insider": "Jane Doe", "rel": "CEO", "title": "CEO", "sec": "Common Stock",
            "insider_sh": 1000.0, "insider_val": 10000.0, "lots": 1, "insider_px": 10.0}


def test_entry_no_lookahead():
    # Filing 2024-01-03; first session strictly after is 2024-01-04 open 11.
    spec = daterange("2024-01-02", 300, o=11.0, c=11.0)
    b = mkbars(spec)
    p = bt.simulate_backtest(sig(), b, "2025-06-01")
    check("entry is first open strictly after filing", p["entry_d"] == "2024-01-04", p["entry_d"])
    check("entry px is that open", abs(p["entry_px"] - 11.0) < 1e-9, p["entry_px"])
    check("shares = 10000/11", abs(p["shares"] - 10000 / 11.0) < 0.01, p["shares"])
    check("entry verified", p["entry_rule_status"] == "verified")


def test_exit_252():
    # Flat price $10 -> ROI 0, exit at bar entry_idx+252.
    spec = daterange("2024-01-02", 400, o=10.0, c=10.0)
    b = mkbars(spec)
    p = bt.simulate_backtest(sig(), b, "2025-12-31")
    check("252-exit populated", p["exit_status"] == "exited_252", p["exit_status"])
    check("exit after entry", p["exit_d"] > p["entry_d"], f"{p['entry_d']} {p['exit_d']}")
    check("exit roi ~0 for flat price", abs(p["exit_roi"]) < 1e-6, p["exit_roi"])
    check("exit pnl ~0 for flat price", abs(p["exit_pnl"]) < 1e-6, p["exit_pnl"])

    # +20% at exit: close 12 -> roi 0.2
    spec2 = daterange("2024-01-02", 400, o=10.0, c=10.0)
    # bump the +252 session close to 12
    bars2 = mkbars(spec2)
    # entry index: find entry_d
    eidx = next(i for i, x in enumerate(bars2) if x["d"] == p["entry_d"])
    bars2[eidx + 252]["c"] = 12.0
    p2 = bt.simulate_backtest(sig(), bars2, "2025-12-31")
    check("exit roi = 12/10-1 = 0.2", abs(p2["exit_roi"] - 0.2) < 1e-3, p2["exit_roi"])


def test_young_position_open():
    # Filing recent; asof close -> open, no exit.
    spec = daterange("2026-06-01", 40, o=10.0, c=10.0)
    b = mkbars(spec)
    p = bt.simulate_backtest(sig(fd="2026-06-02", td="2026-06-01"), b, "2026-08-01")
    check("young position is open", p["exit_status"] == "open", p["exit_status"])
    check("young position has no exit price", p["exit_px"] is None)


def test_delisted_last_observed_exit():
    # Mature entry (>380d before asof) but series ENDS early (delisted) before
    # 252 sessions: exit at last observed close, flagged.
    spec = daterange("2024-01-02", 100, o=10.0, c=8.0)  # drops to 8 then ends
    b = mkbars(spec)
    p = bt.simulate_backtest(sig(), b, "2025-12-31")
    check("delisted uses last observed exit", p["exit_status"] == "exit_last_observed",
          p["exit_status"])
    check("delisted exit roi = 8/10-1", abs(p["exit_roi"] - (-0.2)) < 1e-3, p["exit_roi"])
    check("delisted flagged for review", "corporate action" in (p.get("review_note") or ""),
          p.get("review_note"))


def test_no_price():
    p = bt.simulate_backtest(sig(), [], "2025-12-31")
    check("no bars -> no_price", p["entry_rule_status"] == "no_price", p["entry_rule_status"])
    check("no bars -> no entry", p["entry_px"] is None)


def test_split_straddle_guard_forward():
    # Entry ~1.0, then a 40x unadjusted reverse-split jump in-series, mark high.
    spec = daterange("2026-06-01", 80, o=1.0, c=1.0)
    b = mkbars(spec)
    # insert a ~40x jump around session 25 and keep high after
    for i in range(25, len(b)):
        b[i]["o"], b[i]["c"] = 40.0, 40.0
    s = sig(fd="2026-06-02", td="2026-06-01")
    p = bd.simulate(s, b, "2026-09-20")
    check("straddle ROI withheld", p.get("roi") is None, p.get("roi"))
    check("roi_review flag set", p.get("roi_review") is True)
    check("raw roi retained", p.get("roi_reported") is not None, p.get("roi_reported"))
    # find_split_in_window detects the jump
    jump_d = bd.find_split_in_window(b, p["entry_d"], p["last_d"])
    check("split discontinuity detected", jump_d is not None, str(jump_d))


def test_normal_move_not_flagged():
    # A normal doubling over many sessions must NOT be withheld.
    spec = daterange("2024-01-02", 300, o=10.0, c=10.0)
    b = mkbars(spec)
    eidx = 1  # entry near start
    for i in range(eidx, len(b)):
        px = 10.0 + (5.0 * (i - eidx) / max(1, len(b) - eidx))  # drifts to 15
        b[i]["c"] = px
        b[i]["o"] = px
    s = sig()
    p = bd.simulate(s, b, "2025-06-01")
    check("gradual +50% not flagged", p.get("roi_review") is False, p.get("roi_review"))
    check("gradual move has ROI", p.get("roi") is not None, p.get("roi"))


def main():
    test_entry_no_lookahead()
    test_exit_252()
    test_young_position_open()
    test_delisted_last_observed_exit()
    test_no_price()
    test_split_straddle_guard_forward()
    test_normal_move_not_flagged()
    if FAILED:
        print(f"\n{len(FAILED)} BACKTEST CHECK(S) FAILED")
        for f in FAILED:
            print("  " + f)
        return 1
    print("\nALL BACKTEST CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
