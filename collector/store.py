#!/usr/bin/env python3
"""
CEOTrades unified trade store.

The dataset is far too large to hold in memory (the full 2006->present SEC
insider-transaction corpus is on the order of 15 million rows), so every
consumer reads it as a *stream* of dict rows via `iter_rows()`.

Two on-disk formats are supported transparently:

  collector/data/trades-YYYY.csv.gz        bulk backfill shards (bulk_backfill.py)
  collector/data/trades-YYYY-MM.json.gz    nightly forward-collection shards (collect.py)

Both use the same canonical field names (see bulk_backfill.FIELDS). Rows are
yielded with numeric fields coerced to float/int/None so downstream code never
has to care which shard format a row came from.

Duplicate suppression: a filing collected nightly will later also appear in the
quarterly bulk archive. Rows are de-duplicated on the same identity key used by
the backfill merger, with the CSV (SEC-published) copy winning.

Standard library only.
"""

from __future__ import annotations

import csv
import gzip
import json
import os
import re
import sys
from typing import Iterator

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

CSV_SHARD = re.compile(r"^trades-(\d{4})\.csv\.gz$")
JSON_SHARD = re.compile(r"^trades-(\d{4})-(\d{2})\.json\.gz$")

# Fields coerced to float (None when blank/unparseable).
FLOAT_FIELDS = ("sh", "px", "val", "af", "under_sh", "xp")
# Fields coerced to int.
INT_FIELDS = ("amend", "own_n", "der")


def _f(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _i(v, default=0):
    f = _f(v)
    return default if f is None else int(f)


def normalize(r: dict) -> dict:
    for k in FLOAT_FIELDS:
        if k in r:
            r[k] = _f(r[k])
    for k in INT_FIELDS:
        if k in r:
            r[k] = _i(r[k])
    for k in ("fd", "td", "co", "tk", "in", "code", "sec", "rel", "acc"):
        if r.get(k) is None:
            r[k] = ""
    return r


def row_key(r: dict) -> str:
    def kv(k):
        v = r.get(k)
        if v is None:
            return ""
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        return str(v)
    return "|".join(kv(k) for k in
                    ("acc", "der", "td", "code", "sec", "sh", "px", "ad", "pcik"))


def shard_files(data_dir: str = DATA) -> list[str]:
    if not os.path.isdir(data_dir):
        return []
    out = []
    for fn in sorted(os.listdir(data_dir)):
        if CSV_SHARD.match(fn) or JSON_SHARD.match(fn):
            out.append(os.path.join(data_dir, fn))
    return out


def iter_shard(path: str) -> Iterator[dict]:
    base = os.path.basename(path)
    if CSV_SHARD.match(base):
        with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                yield normalize(dict(r))
    elif JSON_SHARD.match(base):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            try:
                rows = json.load(f)
            except json.JSONDecodeError as e:
                print(f"! bad JSON shard {base}: {e}", file=sys.stderr)
                return
        for r in rows:
            yield normalize(dict(r))


def iter_rows(data_dir: str = DATA, dedupe: bool = True) -> Iterator[dict]:
    """Stream every trade row. CSV (SEC bulk) shards are read first so that
    they win over any overlapping nightly JSON rows."""
    files = shard_files(data_dir)
    csvs = [p for p in files if CSV_SHARD.match(os.path.basename(p))]
    jsons = [p for p in files if JSON_SHARD.match(os.path.basename(p))]

    if not dedupe:
        for p in csvs + jsons:
            yield from iter_shard(p)
        return

    seen: set[str] = set()
    for p in csvs:
        for r in iter_shard(p):
            k = row_key(r)
            if k in seen:
                continue
            seen.add(k)
            yield r
    for p in jsons:
        for r in iter_shard(p):
            k = row_key(r)
            if k in seen:
                continue
            seen.add(k)
            yield r


def count(data_dir: str = DATA) -> int:
    return sum(1 for _ in iter_rows(data_dir))


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else DATA
    files = shard_files(d)
    print(f"{len(files)} shard(s) in {d}")
    n = 0
    years: dict[str, int] = {}
    for r in iter_rows(d):
        n += 1
        years[(r.get("fd") or "?")[:4]] = years.get((r.get("fd") or "?")[:4], 0) + 1
    print(f"{n} unique rows")
    for y in sorted(years):
        print(f"  {y}: {years[y]}")
