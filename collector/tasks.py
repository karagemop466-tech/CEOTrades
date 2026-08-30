#!/usr/bin/env python3
"""CEOTrades dispatchable task runner (GitHub Actions 'CEOTrades backfill').

tasks:
  diag      probe official SEC + market endpoints -> collector/data/logs/diag_net.log
  quarters  resumable SEC quarterly ZIP backfill into the local store (no site build)
  build     paper simulation + site artifacts + line-by-line verification (no collection)
  full      build_site.py orchestrator (daily + quarterly + build), env-driven

Environment knobs (see build_site.py / build_data.py):
  CEOTRADES_TOTAL_BUDGET_MIN   wall-clock budget for the job
  CEOTRADES_QUARTERS_FROM      first year for the quarters task
  CEOTRADES_TARGET_YEAR        target/audit year (full task)
  CEOTRADES_BACKFILL_FROM      first year to backfill (full task)
  CEOTRADES_PUBLISH_FROM       first year published on the site
  CEOTRADES_PAPER_FROM         first year for the $10k paper simulation
  CEOTRADES_FROM_YEAR          publish from-year for the build task
  CEOTRADES_PRICE_MIN          minutes of market-data fetching (build task)
  CEOTRADES_OFFLINE            "1" = build with cached prices only

Standard library only.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

LOG_DIR = os.path.join(HERE, "data", "logs")


def env(name: str, default: str) -> str:
    v = os.environ.get(name, "").strip()
    return v if v else default


def log(msg: str):
    print(msg, flush=True)


def run_with_log(name: str, cmd: list) -> int:
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, f"{name}.log")
    line = "$ " + " ".join(str(c) for c in cmd)
    log(line)
    with open(path, "a", encoding="utf-8") as lf:
        lf.write(line + "\n")
        rc = subprocess.call(cmd, cwd=ROOT, stdout=lf, stderr=subprocess.STDOUT)
        lf.write(f"\nexit={rc}\n")
    return rc


def task_diag() -> int:
    return run_with_log("tasks_diag", [sys.executable, os.path.join(HERE, "diag_net.py")])


def task_quarters() -> int:
    import runlog
    runlog.start("tasks_quarters")
    try:
        from_year = int(env("CEOTRADES_QUARTERS_FROM", "2006") or 2006)
    except ValueError:
        from_year = 2006
    try:
        budget = max(5.0, float(env("CEOTRADES_TOTAL_BUDGET_MIN", "300")))
    except ValueError:
        budget = 300.0
    import build_site
    build_site.backfill(max(2.0, budget - 2.0), from_year)
    return 0


def task_build() -> int:
    cmd = [sys.executable, os.path.join(HERE, "build_data.py")]
    try:
        target = int(env("CEOTRADES_TARGET_YEAR", "0") or 0)
    except ValueError:
        target = 0
    try:
        from_year = int(env("CEOTRADES_FROM_YEAR", "0") or 0)
    except ValueError:
        from_year = 0
    try:
        paper_from = int(env("CEOTRADES_PAPER_FROM", "0") or 0)
    except ValueError:
        paper_from = 0
    if from_year and target and from_year < target:
        cmd += ["--from-year", str(from_year), "--paper-from-year", str(paper_from),
                "--audit-year", str(target)]
    elif target:
        cmd += ["--year", str(target), "--audit-year", str(target)]
    elif from_year:
        cmd += ["--from-year", str(from_year), "--paper-from-year", str(paper_from)]
    if env("CEOTRADES_OFFLINE", "0") == "1":
        cmd.append("--offline")
    else:
        price_min = env("CEOTRADES_PRICE_MIN", "")
        if price_min:
            cmd += ["--price-budget-min", price_min]
    rc = run_with_log("tasks_build", cmd)
    if rc != 0:
        return rc
    return run_with_log("tasks_verify",
                        [sys.executable, os.path.join(HERE, "verify_lines.py")])


def task_full() -> int:
    return run_with_log("tasks_full", [sys.executable, os.path.join(HERE, "build_site.py")])


def main() -> int:
    ap = argparse.ArgumentParser(description="CEOTrades backfill task runner")
    ap.add_argument("--task", required=True,
                    choices=["diag", "quarters", "build", "full"])
    args = ap.parse_args()
    return {"diag": task_diag, "quarters": task_quarters,
            "build": task_build, "full": task_full}[args.task]()


if __name__ == "__main__":
    raise SystemExit(main())
