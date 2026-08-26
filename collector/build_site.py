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
    for label in refresh:
        log(f"== refresh {label}")
        try:
            ok, _ = ingest(label)
        except Exception as e:  # noqa: BLE001
            log(f"   ! {e}")
            continue
        if ok:
            done.add(label)
            unavailable.discard(label)
        else:
            log("   not published yet")

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
        log("No data collected yet — nothing to publish.")
        return 1

    log("\nBuilding site data …")
    sys.argv = ["build_data"]
    return build_data.main()


if __name__ == "__main__":
    sys.exit(main())
