#!/usr/bin/env python3
"""
CEOTrades orchestrator — the single entry point the nightly GitHub Actions
workflow invokes after collect.py.

Responsibilities, in order:

  1. HISTORICAL BACKFILL (resumable). Downloads SEC quarterly "Insider
     Transactions Data Sets" archives (2006 -> current quarter) that have not
     been ingested yet, newest first so the site is useful immediately and
     deepens every night. Progress is recorded in collector/data/backfill.json,
     so each run resumes where the previous one stopped and the whole history
     is filled in automatically with no manual input.

  2. CURRENT QUARTER REFRESH. Re-pulls the current (and previous) quarter so
     amendments and late filings are absorbed.

  3. SITE BUILD. Streams the whole store, prices insider buys, runs the
     $10,000 paper simulation and writes every JSON/CSV artifact.

Time-budgeted: the backfill stops before the workflow's limit and the
remaining quarters are picked up by the next scheduled run.

Environment:
  CEOTRADES_BACKFILL_MIN   minutes to spend on the historical backfill (default 120)
  CEOTRADES_SKIP_BACKFILL  set to "1" to skip step 1
  CEOTRADES_PRICE_MIN      minutes budget for price fetching (default 120)
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DATA = os.path.join(HERE, "data")
STATE = os.path.join(DATA, "backfill.json")

import build_data  # noqa: E402
import bulk_backfill as bb  # noqa: E402
import store  # noqa: E402


def log(m):
    print(m, flush=True)


def load_state() -> dict:
    if os.path.exists(STATE):
        try:
            with open(STATE, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return {"done": [], "unavailable": [], "updated": ""}


def save_state(st: dict):
    os.makedirs(DATA, exist_ok=True)
    st["updated"] = date.today().isoformat()
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1, sort_keys=True)


def all_quarters() -> list[str]:
    """Every quarter from 2006Q1 through the current one, NEWEST FIRST."""
    today = date.today()
    out = []
    for y in range(bb.FIRST_YEAR, today.year + 1):
        for q in range(1, 5):
            if y == today.year and (q - 1) * 3 + 1 > today.month:
                break
            out.append(f"{y}q{q}")
    return list(reversed(out))


def ingest(label: str) -> tuple[bool, int]:
    """Download + merge one quarter. Returns (available, rows_parsed)."""
    y, q = int(label[:4]), int(label[-1])
    raw = bb.fetch_quarter(y, q)
    if not raw:
        return False, 0
    log(f"   downloaded {len(raw) / 1e6:.1f} MB")
    rows = bb.quarter_rows(raw, label)
    log(f"   parsed {len(rows):,} transaction rows")
    info = bb.merge_into_store(rows)
    log(f"   merged +{info['added']:,} new")
    return True, len(rows)


def backfill(budget_min: float) -> None:
    st = load_state()
    done = set(st.get("done", []))
    unavailable = set(st.get("unavailable", []))
    quarters = all_quarters()

    # The two most recent quarters are always refreshed (amendments arrive late).
    refresh = quarters[:2]
    todo = [q for q in quarters if q not in done and q not in unavailable]

    log(f"Backfill: {len(done)} quarters ingested, {len(todo)} remaining, "
        f"budget {budget_min:.0f} min")

    deadline = time.monotonic() + budget_min * 60
    source_failed = False
    for label in refresh:
        log(f"== refresh {label}")
        try:
            ok, _ = ingest(label)
        except bb.TransientFetchError as e:
            log(f"   ! transient SEC/network failure: {e}")
            source_failed = True
            break
        except Exception as e:  # noqa: BLE001
            log(f"   ! {e}")
            continue
        if ok:
            done.add(label)
            unavailable.discard(label)
        else:
            log("   not published yet")

    if source_failed:
        log("Backfill paused because SEC sources were unavailable. Existing data is preserved; no quarters were marked unavailable.")
        st["done"] = sorted(done)
        st["unavailable"] = sorted(unavailable)
        save_state(st)
        return

    for label in todo:
        if label in refresh:
            continue
        if time.monotonic() > deadline:
            log(f"Backfill budget reached — {len([q for q in todo if q not in done])} "
                f"quarters left for the next run.")
            break
        log(f"== {label}")
        try:
            ok, _ = ingest(label)
        except bb.TransientFetchError as e:
            log(f"   ! transient SEC/network failure: {e}")
            log("Backfill paused. Quarter remains retryable on the next run.")
            break
        except Exception as e:  # noqa: BLE001
            log(f"   ! failed: {e}")
            continue
        if ok:
            done.add(label)
        else:
            log("   archive unavailable")
            unavailable.add(label)
        st["done"] = sorted(done)
        st["unavailable"] = sorted(unavailable)
        save_state(st)

    st["done"] = sorted(done)
    st["unavailable"] = sorted(unavailable)
    save_state(st)
    remaining = [q for q in all_quarters() if q not in done and q not in unavailable]
    log(f"Backfill state: {len(done)} done, {len(remaining)} remaining"
        + (f" (next: {remaining[0]})" if remaining else " — history complete"))


def write_empty_outputs():
    """Publish schema-valid empty artifacts so the site renders a clean
    'awaiting first collection' state instead of failing to fetch."""
    out = os.path.join(os.path.dirname(HERE), "data")
    os.makedirs(os.path.join(out, "paper"), exist_ok=True)
    os.makedirs(os.path.join(out, "csv"), exist_ok=True)
    os.makedirs(os.path.join(out, "co"), exist_ok=True)
    stamp = date.today().isoformat()

    def w(name, obj):
        with open(os.path.join(out, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, separators=(",", ":"))

    w("summary.json", {
        "generated": stamp, "range": {"from": "", "to": ""},
        "counts": {"trades": 0, "filings": 0, "companies": 0, "insiders": 0,
                   "with_ticker": 0, "with_price": 0, "derivative": 0,
                   "buys": 0, "sells": 0, "paper_positions": 0},
        "value": {"total": 0, "buy": 0, "sell": 0, "net": 0},
        "by_code": [], "by_rel": [], "by_side": [], "by_form": [],
        "yearly": [], "months": []})
    w("recent.json", [])
    w("companies.json", [])
    w("insiders.json", [])
    w("paper/summary.json", {
        "stake": build_data.STAKE,
        "counts": {"signals": 0, "open": 0, "awaiting_entry": 0, "no_price": 0},
        "capital": {"deployed": 0, "value": 0, "pnl": 0, "roi": None},
        "roi": build_data.stats([]), "gap": build_data.stats([]),
        "horizons": {h: build_data.stats([]) for h in
                     ("r1", "r5", "r21", "r63", "r252")},
        "by_role": [], "by_size": [], "by_year": [], "best": [], "worst": [],
        "rule": {"entry": "regular-session open of the first trading day strictly "
                          "after the SEC filing date",
                 "exit": "none — positions stay open for forward testing",
                 "costs": "no commission, slippage or spread modelled"},
        "findings": ["No data collected yet. The nightly GitHub Actions job "
                     "populates the full SEC insider-trade history automatically."],
        "generated": stamp})
    w("paper/positions.json", [])
    w("paper/equity.json", [])
    with open(os.path.join(out, "trades.csv"), "w", encoding="utf-8") as f:
        f.write(",".join(build_data.COMPACT_KEYS) + "\n")


def main() -> int:
    if os.environ.get("CEOTRADES_SKIP_BACKFILL") != "1":
        try:
            backfill(float(os.environ.get("CEOTRADES_BACKFILL_MIN", "120")))
        except Exception as e:  # noqa: BLE001
            log(f"! backfill aborted: {e} — continuing to the site build")
    else:
        log("Backfill skipped (CEOTRADES_SKIP_BACKFILL=1)")

    shards = store.shard_files(DATA)
    log(f"\nStore: {len(shards)} shard(s)")
    if not shards:
        log("No data collected yet — writing empty (but valid) site artifacts.")
        write_empty_outputs()
        return 1

    log("\nBuilding site data …")
    sys.argv = ["build_data"]
    return build_data.main()


if __name__ == "__main__":
    sys.exit(main())
