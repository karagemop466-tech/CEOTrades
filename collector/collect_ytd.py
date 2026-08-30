#!/usr/bin/env python3
"""Collect the target year's insider-trade corpus from official SEC sources.

For a current year, the SEC quarterly Insider Transactions Data Set is usually
available only for completed quarters. This orchestrator therefore:

  1. pulls SEC quarterly bulk archives for completed quarters in the target year;
  2. optionally tries the current quarter archive if published;
  3. collects current-quarter filings day-by-day from the EDGAR daily index;
  4. records a source manifest so completeness can be audited.

No trades are hard-coded here. If a source is unavailable it is recorded and the
audit will mark the local store incomplete/unproven rather than fabricate data.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import bulk_backfill as bb  # noqa: E402
import runlog  # noqa: E402

DATA = os.path.join(HERE, "data")


def log(msg: str):
    print(msg, flush=True)


def asof_today() -> date:
    override = os.environ.get("CEOTRADES_ASOF_DATE")
    if override:
        try:
            return date.fromisoformat(override[:10])
        except ValueError:
            pass
    return datetime.now(timezone.utc).date()


def quarter_for_day(d: date) -> int:
    return (d.month - 1) // 3 + 1


def quarter_start(year: int, q: int) -> date:
    return date(year, 3 * (q - 1) + 1, 1)


def quarter_end(year: int, q: int) -> date:
    if q == 4:
        return date(year, 12, 31)
    return quarter_start(year, q + 1) - timedelta(days=1)


def reset_year(data_dir: str, year: int):
    patterns = [
        re.compile(rf"^trades-{year}\.csv\.gz$"),
        re.compile(rf"^trades-{year}-\d{{2}}\.json\.gz$"),
    ]
    removed = []
    if not os.path.isdir(data_dir):
        return removed
    for fn in os.listdir(data_dir):
        if any(p.match(fn) for p in patterns):
            path = os.path.join(data_dir, fn)
            os.remove(path)
            removed.append(fn)

    # collect.py uses stats.json to skip days already marked complete. When a
    # year is explicitly replaced, clear those day markers too so the EDGAR
    # daily index is re-read instead of silently preserving gaps.
    stats_path = os.path.join(data_dir, "stats.json")
    if os.path.exists(stats_path):
        try:
            with open(stats_path, "r", encoding="utf-8") as f:
                stats = json.load(f)
            days = stats.get("days_collected") or {}
            kept = {d: info for d, info in days.items() if not str(d).startswith(f"{year}-")}
            if len(kept) != len(days):
                stats["days_collected"] = kept
                with open(stats_path, "w", encoding="utf-8") as f:
                    json.dump(stats, f, indent=1, sort_keys=True)
                removed.append("stats.json target-year day markers")
        except (OSError, json.JSONDecodeError):
            removed.append("stats.json unreadable; left unchanged")
    return removed


def run_daily_collect(start: date, end: date, data_dir: str, rate: float,
                      budget_min: float, force: bool) -> dict:
    if start > end:
        return {"status": "skipped", "reason": "empty daily window"}
    cmd = [
        sys.executable,
        os.path.join(HERE, "collect.py"),
        "--start", start.isoformat(),
        "--end", end.isoformat(),
        "--rate", str(rate),
        "--budget-min", str(budget_min),
        "--no-backfill",
        "--data-dir", data_dir,
    ]
    if force:
        cmd.append("--force")
    log("== daily EDGAR collection " + start.isoformat() + " .. " + end.isoformat())
    rc = subprocess.call(cmd)
    return {"status": "ok" if rc == 0 else "failed", "returncode": rc, "command": " ".join(cmd)}


def quarter_state_path(data_dir: str) -> str:
    return os.path.join(data_dir, "quarters.json")


def load_quarter_state(data_dir: str) -> dict:
    p = quarter_state_path(data_dir)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def save_quarter_state(data_dir: str, state: dict) -> None:
    with open(quarter_state_path(data_dir), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, sort_keys=True)


# A completed quarter is re-downloaded only inside this window after its
# quarter-end (amendments and late filings keep arriving for a few weeks).
# Outside the window an already-ingested quarter is skipped: re-merging it
# would rewrite a multi-MB store shard every night for no data change.
QUARTER_REFRESH_DAYS = 45


def quarter_needs_fetch(state: dict, y: int, q: int, today: date) -> bool:
    label = f"{y}q{q}"
    info = state.get(label) or {}
    if not info.get("ingested"):
        return True
    try:
        ended = date.fromisoformat(info["quarter_end"])
    except (KeyError, ValueError):
        return True
    return (today - ended).days <= QUARTER_REFRESH_DAYS


def main() -> int:
    runlog.start("collect_ytd")
    today = asof_today()
    ap = argparse.ArgumentParser(description="Collect official SEC insider trades for a target year-to-date window.")
    ap.add_argument("--year", type=int, default=today.year)
    ap.add_argument("--end", default=(today - timedelta(days=1)).isoformat(),
                    help="last filing date to collect from daily index; default yesterday UTC")
    ap.add_argument("--data-dir", default=DATA)
    ap.add_argument("--rate", type=float, default=8.0)
    ap.add_argument("--daily-budget-min", type=float, default=90.0)
    ap.add_argument("--replace-year", action="store_true",
                    help="remove existing target-year shards before collecting, eliminating stale/manual rows")
    ap.add_argument("--force-daily", action="store_true",
                    help="re-collect days already marked complete in collect.py stats")
    ap.add_argument("--refresh-quarters", action="store_true",
                    help="re-download completed-quarter archives even outside the refresh window")
    ap.add_argument("--try-current-quarter", action="store_true",
                    help="also try the current quarter bulk archive, if the SEC has already published it")
    args = ap.parse_args()

    os.makedirs(args.data_dir, exist_ok=True)
    original_data = bb.DATA
    bb.DATA = args.data_dir

    end = date.fromisoformat(args.end)
    if end.year != args.year:
        end = min(end, date(args.year, 12, 31))
    current_q = quarter_for_day(end if end.year == args.year else date(args.year, 12, 31))
    completed_qs = [q for q in range(1, 5) if quarter_end(args.year, q) <= end]
    q_to_try = list(completed_qs)
    current_quarter_is_partial = quarter_end(args.year, current_q) > end
    if current_quarter_is_partial and args.try_current_quarter and current_q not in q_to_try:
        q_to_try.append(current_q)

    manifest = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "target_year": args.year,
        "target_window": [date(args.year, 1, 1).isoformat(), end.isoformat()],
        "official_sources": [
            "SEC Insider Transactions Data Sets quarterly ZIP archives",
            "SEC EDGAR daily master index and ownership XML submissions",
        ],
        "replace_year": args.replace_year,
        "removed_before_collect": [],
        "quarters": [],
        "daily": {},
    }

    try:
        if args.replace_year:
            removed = reset_year(args.data_dir, args.year)
            manifest["removed_before_collect"] = removed
            log("Removed existing target-year shard(s): " + (", ".join(removed) if removed else "none"))

        missing_bulk_qs = []
        qstate = load_quarter_state(args.data_dir)
        for q in q_to_try:
            label = f"{args.year}q{q}"
            if not args.refresh_quarters and not args.replace_year \
                    and not quarter_needs_fetch(qstate, args.year, q, today):
                info = qstate[label]
                log(f"== SEC quarterly archive {label}: already ingested "
                    f"{info.get('rows_parsed', 0):,} rows on {info.get('at', '?')[:10]} "
                    f"(quarter ended >{QUARTER_REFRESH_DAYS}d ago) — skipping download")
                manifest["quarters"].append({
                    "label": label, "status": "ok", "skipped_download": True,
                    "previously_ingested": True,
                    "rows_parsed": info.get("rows_parsed", 0),
                })
                continue
            log(f"== SEC quarterly archive {label}")
            raw = bb.fetch_quarter(args.year, q)
            if not raw:
                log("   unavailable/not yet published")
                manifest["quarters"].append({"label": label, "status": "unavailable"})
                missing_bulk_qs.append(q)
                continue
            digest = hashlib.sha256(raw).hexdigest()
            log(f"   downloaded {len(raw) / 1e6:.1f} MB sha256={digest[:16]}…")
            rows = bb.quarter_rows(raw, f"{args.year}Q{q}")
            info = bb.merge_into_store(rows)
            qstate[label] = {
                "ingested": True,
                "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "quarter_end": quarter_end(args.year, q).isoformat(),
                "sha256": digest,
                "bytes": len(raw),
                "rows_parsed": len(rows),
                "rows_added": info.get("added", 0),
            }
            save_quarter_state(args.data_dir, qstate)
            manifest["quarters"].append({
                "label": label,
                "status": "ok",
                "bytes": len(raw),
                "sha256": digest,
                "rows_parsed": len(rows),
                "rows_added": info.get("added", 0),
                "rows_skipped_undated": info.get("undated", 0),
            })

        daily_candidates = []
        if current_quarter_is_partial:
            daily_candidates.append(quarter_start(args.year, current_q))
        daily_candidates.extend(quarter_start(args.year, q) for q in missing_bulk_qs)
        if daily_candidates:
            daily_start = min(daily_candidates)
            # Current partial quarters and any unavailable bulk quarters are
            # filled from the daily EDGAR index/XML path. It is idempotent and
            # deduped by store.row_key.
            manifest["daily"] = run_daily_collect(
                daily_start, end, args.data_dir, min(args.rate, 10.0),
                args.daily_budget_min, args.force_daily,
            )
        else:
            manifest["daily"] = {"status": "skipped", "reason": "all requested quarters covered by bulk archives"}

        path = os.path.join(args.data_dir, "source_manifest.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=1, sort_keys=True)
        log(f"Wrote source manifest: {os.path.relpath(path, ROOT)}")
    finally:
        bb.DATA = original_data

    failed = [q for q in manifest["quarters"] if q.get("status") not in {"ok", "unavailable"}]
    if manifest["daily"].get("status") == "failed" or failed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
