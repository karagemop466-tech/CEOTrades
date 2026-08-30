#!/usr/bin/env python3
"""Dispatchable task runner for the CEOTrades backfill workflow.

The scheduled workflow can only run a fixed pipeline; this runner lets a
maintainer dispatch richer operations from the Actions UI / gh CLI while
reusing exactly the same modules, state and safety gates as the nightly
build:

  diag      probe every upstream (SEC/Yahoo/Stooq/Nasdaq) -> diag_net.log
  quarters  resumable quarterly-ZIP backfill only (no site build)
  build     site build + paper simulation + line-by-line verification only
  full      the complete nightly path (target-year collection + backfill +
            build + verification), driven by the standard env knobs

Environment knobs (same names as build_site.py):
  CEOTRADES_TARGET_YEAR     target filing year to collect/audit (default: current UTC year)
  CEOTRADES_BACKFILL_FROM   first filing year to backfill (default: 2006; 0 disables)
  CEOTRADES_PUBLISH_FROM    first filing year published on the site
  CEOTRADES_PAPER_FROM      first filing year for the $10k paper simulation (default: 2024)
  CEOTRADES_TOTAL_BUDGET_MIN  wall-clock budget in minutes (default: 85)
  CEOTRADES_PRICE_MIN       minutes budget for price fetching (default: leftover)
  CEOTRADES_OFFLINE=1       build without any network
  CEOTRADES_SKIP_DIAG=1     skip the diagnostic probe step
  CEOTRADES_SKIP_BACKFILL=1 skip the legacy all-history backfill
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import build_site  # noqa: E402


def log(m: str) -> None:
    print(m, flush=True)


def run_diag() -> int:
    import diag_net
    return diag_net.main()


def run_quarters(budget_min: float, from_year: int) -> int:
    log(f"== quarterly backfill: from {from_year}, budget {budget_min:.0f} min")
    build_site.backfill(budget_min, from_year)
    return 0


def run_build(budget_min: float, target: int) -> int:
    """Site build + verification without touching SEC collection."""
    import build_data
    import store
    shards = store.shard_files(build_site.DATA)
    log(f"\nStore: {len(shards)} shard(s)")
    if not shards:
        log("No data collected yet — writing empty (but valid) site artifacts.")
        build_site.write_empty_outputs()
        return 1
    publish_from = build_site._publish_from(target)
    paper_from = build_site._paper_from()
    sys.argv = ["build_data"]
    if publish_from and publish_from < target:
        sys.argv += ["--from-year", str(publish_from),
                     "--paper-from-year", str(max(paper_from, publish_from)),
                     "--audit-year", str(target)]
    else:
        sys.argv += ["--year", str(target), "--audit-year", str(target)]
    if os.environ.get("CEOTRADES_OFFLINE") == "1":
        sys.argv.append("--offline")
    elif os.environ.get("CEOTRADES_PRICE_MIN") is None:
        sys.argv += ["--price-budget-min", f"{max(2.0, budget_min - 8.0):.0f}"]
    log("\nBuilding site data …")
    rc = build_data.main()
    if rc != 0:
        return rc
    log("\nLine-by-line verification …")
    return subprocess.call([sys.executable, os.path.join(HERE, "verify_lines.py")])


def main() -> int:
    task = os.environ.get("TASK", "full").strip()
    t_start = time.monotonic()
    try:
        # Cap at 340: the backfill workflow's job timeout is 345 minutes.
        budget = min(340.0, max(5.0, float(os.environ.get("CEOTRADES_TOTAL_BUDGET_MIN", "85"))))
    except ValueError:
        budget = 85.0
    target = build_site._target_year()
    from_year = build_site._backfill_from(target)

    log(f"CEOTrades task runner: {task} (target {target or 'all-years'}, "
        f"backfill from {from_year}, budget {budget:.0f} min)")

    if task == "diag":
        return run_diag()

    if task == "quarters":
        if from_year <= 0:
            log("CEOTRADES_BACKFILL_FROM disables the backfill; nothing to do.")
            return 0
        return run_quarters(budget - 1.0, from_year)

    if task == "build":
        return run_build(budget - 1.0, target)

    if task == "full":
        log("== full nightly path via build_site.main()")
        return build_site.main()

    log(f"unknown task {task!r} (want diag|quarters|build|full)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
