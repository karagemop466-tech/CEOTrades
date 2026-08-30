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
  CEOTRADES_TARGET_YEAR    target filing year to collect/audit (default: current UTC year)
  CEOTRADES_BACKFILL_FROM  first filing year to backfill + publish (default: target-1; 0 disables)
  CEOTRADES_TOTAL_BUDGET_MIN  wall-clock budget for the whole build (default 95; the
                           GitHub Actions job timeout is 120 min)
  CEOTRADES_SKIP_BACKFILL  set to "1" to skip legacy all-history backfill
  CEOTRADES_PRICE_MIN      optional minutes budget for price fetching (target mode defaults to offline/no fabricated prices)
  CEOTRADES_RESET_YEAR     set to "1" to purge+rebuild the target year's shards (occasional clean rebuilds only)
"""
from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
import time
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

DATA = os.path.join(HERE, "data")
STATE = os.path.join(DATA, "backfill.json")

import build_data  # noqa: E402
import bulk_backfill as bb  # noqa: E402
import runlog  # noqa: E402
import store  # noqa: E402


def _target_year() -> int:
    # Default is the current UTC calendar year so nightly collection keeps
    # placing paper trades on new Form-4 buys instead of rebuilding a stale year.
    raw = os.environ.get("CEOTRADES_TARGET_YEAR", str(date.today().year)).strip()
    if raw in ("", "0", "all", "ALL"):
        return 0
    try:
        return int(raw)
    except ValueError:
        return date.today().year


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


def all_quarters(from_year: int = 0) -> list[str]:
    """Every quarter from `from_year` (or 2006) through the current one, NEWEST FIRST."""
    today = date.today()
    out = []
    for y in range(max(bb.FIRST_YEAR, from_year or bb.FIRST_YEAR), today.year + 1):
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


def _quarter_end_of(label: str) -> date:
    y, q = int(label[:4]), int(label[-1])
    return date(y, 3 * q, 1) - timedelta(days=1) if q < 4 else date(y, 12, 31)


def backfill(budget_min: float, from_year: int = 0) -> None:
    st = load_state()
    done = set(st.get("done", []))
    unavailable = set(st.get("unavailable", []))
    quarters = all_quarters(from_year)
    today = date.today()

    # The two most recent quarters are refreshed while still inside the
    # late-filing/amendment window. Once a quarter ended more than
    # QUARTER_REFRESH_DAYS ago and is already ingested, re-downloading it
    # would rewrite a multi-MB store shard for no data change — skip it.
    refresh = [q for q in quarters[:2]
               if q not in done or (today - _quarter_end_of(q)).days <= 45]
    if len(refresh) < 2:
        skipped = [q for q in quarters[:2] if q not in refresh]
        log(f"Refresh skip (ingested, quarter ended >45d ago): {', '.join(skipped)}")
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
    remaining = [q for q in all_quarters(from_year) if q not in done and q not in unavailable]
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
    w("paper/winners.json", {"generated": stamp, "outcome": "winners", "count": 0, "rows": [], "unclassified_count": 0})
    w("paper/losers.json", {"generated": stamp, "outcome": "losers", "count": 0, "rows": [], "unclassified_count": 0})
    w("paper/summary.json", {
        "stake": build_data.STAKE,
        "counts": {"signals": 0, "open": 0, "awaiting_entry": 0, "no_price": 0},
        "capital": {"deployed": 0, "value": 0, "pnl": 0, "roi": None},
        "verification": {"entry_rule_failures": 0, "arithmetic_failures": 0,
                         "open_positions_checked": 0, "price_sources": [],
                         "line_by_line_review": "No paper rows were available.",
                         "portfolio_warning": "No holdings were available."},
        "roi": build_data.stats([]), "gap": build_data.stats([]),
        "horizons": {h: build_data.stats([]) for h in
                     ("r1", "r5", "r21", "r63", "r252")},
        "by_role": [], "by_size": [], "by_year": [], "best": [], "worst": [],
        "outcomes": {"realized": 0, "winners": 0, "losers": 0, "unclassified": 0,
                     "winner_log": "data/paper/winners.json", "loser_log": "data/paper/losers.json"},
        "rule": {"entry": "regular-session open of the first trading day strictly "
                          "after the SEC filing date",
                 "exit": "none — positions stay open for forward testing",
                 "costs": "no commission, slippage or spread modelled"},
        "findings": ["No data collected yet. The nightly GitHub Actions job "
                     "populates the full SEC insider-trade history automatically."],
        "generated": stamp})
    w("paper/positions.json", [])
    w("paper/equity.json", [])
    w("audit.json", {
        "generated": stamp, "target_year": date.today().year, "asof": stamp,
        "official_sources": [],
        "completeness": {"status": "incomplete_or_unproven", "complete": False,
                         "target_start": "", "target_end": "", "observed_from": "",
                         "observed_to": "", "target_observed_from": "",
                         "target_observed_to": "", "missing_months": [],
                         "candidate_missing_business_days": [],
                         "candidate_missing_business_days_truncated": False,
                         "blockers": ["No trade shards were available."],
                         "note": "No data collected yet."},
        "counts": {"rows": 0, "target_year_rows": 0, "filings": 0,
                   "target_year_filings": 0, "companies": 0, "insiders": 0,
                   "with_ticker": 0, "without_ticker": 0, "with_price": 0,
                   "derivative_rows": 0},
        "value": {"total_abs_reported": 0, "code_p_buy": 0,
                  "code_s_sell": 0, "net_p_minus_s": 0},
        "by_code": [],
        "integrity": {"row_hash_sha256": "", "row_issues": 0,
                      "by_issue": [], "by_severity": [], "issue_examples": [],
                      "manual_data_guard": {"manual_or_synthetic_generators_detected": False,
                                             "suspects": [], "policy": ""}},
        "shards": [], "assurance": {"remote_refetch": "not_performed_by_default"}})
    w("irregularities.json", [])
    w("insider_activity.json", {
        "generated": stamp, "target_year": _target_year() or date.today().year,
        "summary": {"target_year": _target_year() or date.today().year,
                    "insider_company_pairs": 0, "buy_sell_pairs": 0,
                    "with_reported_common_shares": 0, "with_priced_holdings": 0,
                    "reported_holding_value_priced": 0,
                    "scope": "No data collected yet.",
                    "full_csv": "data/insider_activity.csv.gz"},
        "rows": [], "truncated": False, "row_count": 0})
    w("insider_portfolios.json", {
        "generated": stamp, "target_year": _target_year() or date.today().year,
        "summary": {"target_year": _target_year() or date.today().year,
                    "insiders": 0, "with_multiple_issuers": 0,
                    "with_priced_value": 0, "reported_value_priced": 0,
                    "scope": "No data collected yet."},
        "rows": [], "truncated": False, "row_count": 0})
    with gzip.GzipFile(filename=os.path.join(out, "insider_activity.csv.gz"), mode="wb", compresslevel=9, mtime=0) as gz:
        gz.write(b"id,target_year,insider,pcik,co,tk,icik,rel,title,n,buy_n,sell_n,other_n,buy_sh,sell_sh,buy_v,sell_v,net_v,buy_sell_overlap,first,last,first_buy,last_buy,first_sell,last_sell,reported_common_shares,holding_groups,latest_holding_fd,mark_d,mark_px,holding_value,price_src,valuation_status,portfolio_scope\n")
    with open(os.path.join(out, "trades.csv"), "w", encoding="utf-8") as f:
        f.write(",".join(build_data.COMPACT_KEYS) + "\n")


def _total_budget_min() -> float:
    """Wall-clock budget for the WHOLE build. The nightly GitHub Actions job is
    capped at 120 minutes; default 95 leaves headroom for checkout, self-test,
    the earlier collect.py step and the commit/push step."""
    raw = os.environ.get("CEOTRADES_TOTAL_BUDGET_MIN", "85").strip()
    try:
        v = float(raw)
    except ValueError:
        v = 95.0
    return max(5.0, min(v, 115.0))


def _backfill_from(target: int) -> int:
    """First filing year to backfill and publish (target-1 by default so the
    paper book covers at least a rolling two-year window; 0 disables)."""
    raw = os.environ.get("CEOTRADES_BACKFILL_FROM", str(target - 1)).strip()
    if raw in ("", "0", "off", "none"):
        return 0
    try:
        v = int(raw)
    except ValueError:
        return max(0, target - 1)
    return max(0, min(v, target))


def main() -> int:
    t_start = time.monotonic()
    budget = _total_budget_min()
    target = _target_year()

    if target:
        # Target-year mode: incremental nightly collection of the current year
        # (SEC quarterly archives for completed quarters + EDGAR daily index for
        # the current one), plus a resumable backfill of earlier years within
        # CEOTRADES_BACKFILL_FROM. Nightly runs are NON-destructive: shards and
        # day markers persist and rows de-duplicate on the stable identity key,
        # so the current quarter is collected once and then extended, not
        # re-scanned from its first day on every run.
        from_year = _backfill_from(target)
        log(f"Target-year SEC collection: {target} "
            f"(publish window {from_year or target}..{target}, budget {budget:.0f} min)")

        collect_budget = min(35.0, budget * 0.4)
        cmd = [sys.executable, os.path.join(HERE, "collect_ytd.py"),
               "--year", str(target), "--rate", "8",
               "--daily-budget-min", f"{collect_budget:.0f}"]
        if os.environ.get("CEOTRADES_RESET_YEAR") == "1":
            log("CEOTRADES_RESET_YEAR=1 — purging target-year shards for a clean rebuild")
            cmd.append("--replace-year")
        rc = subprocess.call(cmd)
        if rc != 0:
            log(f"! target-year collection failed with exit code {rc}")
            return rc

        if from_year and os.environ.get("CEOTRADES_SKIP_BACKFILL") != "1":
            backfill_left = budget - (time.monotonic() - t_start) / 60.0 - 10.0
            try:
                backfill(max(2.0, backfill_left), from_year)
            except Exception as e:  # noqa: BLE001
                log(f"! backfill aborted: {e} — continuing to the site build")
    else:
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
    if target:
        if from_year and from_year < target:
            # Publish the backfilled window AND the target year so paper
            # positions cover every tracked insider buy, not just this year's.
            sys.argv += ["--from-year", str(from_year), "--audit-year", str(target)]
        else:
            sys.argv += ["--year", str(target), "--audit-year", str(target)]
        if os.environ.get("CEOTRADES_OFFLINE") == "1":
            sys.argv.append("--offline")
        # Whatever wall-clock budget is left (minus a safety margin and the
        # verification pass) goes to price fetching.
        if "CEOTRADES_PRICE_MIN" not in os.environ:
            left = budget - (time.monotonic() - t_start) / 60.0
            sys.argv += ["--price-budget-min", f"{max(2.0, left - 8.0):.0f}"]
    rc = build_data.main()
    if rc != 0:
        return rc

    # Line-by-line verification of the published artifacts (offline, fast).
    # Regenerates data/verification.json + VERIFICATION.md on every build so
    # they can never go stale. Exit code 1 fails the build: no unverified
    # publish.
    log("\nLine-by-line verification …")
    vrc = subprocess.call([sys.executable, os.path.join(HERE, "verify_lines.py")])
    if vrc != 0:
        log("! line-by-line verification FAILED — refusing to report success")
        return vrc
    return 0


def commit_diagnostics() -> None:
    """When running in GitHub Actions, commit the run's log files and source
    manifest even when a build stage failed, so the failure is diagnosable
    from the repository itself. Never commits trade or price data."""
    import subprocess
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    ref = os.environ.get("GITHUB_REF", "")
    if not ref.startswith("refs/heads/"):
        return
    paths = ["collector/data/logs", "collector/data/source_manifest.json",
             "collector/data/stats.json", "collector/data/backfill.json"]
    try:
        subprocess.run(["git", "config", "user.name", "ceotrades-bot"], cwd=ROOT, check=False)
        subprocess.run(["git", "config", "user.email",
                        "41898282+ceotrades-bot@users.noreply.github.com"], cwd=ROOT, check=False)
        subprocess.run(["git", "add", "--"] + paths, cwd=ROOT, check=False)
        staged = subprocess.run(["git", "diff", "--cached", "--quiet"],
                                cwd=ROOT, check=False).returncode != 0
        if not staged:
            log("No diagnostics to commit.")
            return
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        subprocess.run(["git", "commit", "-m",
                        f"chore(diagnostics): collection failure logs {stamp}"], cwd=ROOT, check=False)
        pushed = subprocess.run(["git", "push", "origin", f"HEAD:{ref}"], cwd=ROOT, check=False)
        if pushed.returncode != 0:
            subprocess.run(["git", "pull", "--rebase", "origin", ref], cwd=ROOT, check=False)
            subprocess.run(["git", "push", "origin", f"HEAD:{ref}"], cwd=ROOT, check=False)
    except OSError as e:  # noqa: PERF203
        log(f"diagnostics commit failed: {e}")


if __name__ == "__main__":
    try:
        _rc = main()
    except SystemExit:
        raise
    except BaseException:
        import traceback
        runlog.start("build_site")
        traceback.print_exc()
        _rc = 1
    if _rc != 0:
        commit_diagnostics()
    sys.exit(_rc)
