#!/usr/bin/env python3
"""Offline validation of the generated docs/data JSON files.

Recomputes every aggregate independently from trades.json and compares it
against summary.json / manifest.json / companies.json. Any mismatch or
structural problem fails the run (exit code 1) so a broken dataset can never
be published to the site.
"""

from __future__ import annotations

import json
import os
import re
import sys

DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docs", "data",
)
ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
CIK_RE = re.compile(r"^\d{10}$")
KINDS = {"non-derivative", "derivative"}
ROLE_KEYS = ("director", "officer", "ten_percent_owner", "other")

CHECKS: list = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def load(name):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        check(f"{name} exists", False, "file missing")
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        check(f"{name} valid JSON", False, str(exc))
        return None


def main():
    trades = load("trades.json")
    companies = load("companies.json")
    summary = load("summary.json")
    manifest = load("manifest.json")

    if trades is None or summary is None or manifest is None:
        return 1

    # --- structural checks on every record -------------------------------
    check("trades.json is a list", isinstance(trades, list))
    if not isinstance(trades, list):
        return 1
    check("trades.json non-empty", len(trades) > 0, f"{len(trades)} records")

    bad_ids = []
    bad_accessions = []
    bad_ciks = []
    bad_kinds = []
    bad_value = []
    bad_dates = []
    bad_urls = []
    missing_keys = []
    required = ("id", "accession", "filing_url", "filing_date", "kind",
                "code", "company", "owner", "shares", "price_per_share", "value")

    for idx, rec in enumerate(trades):
        for key in required:
            if key not in rec:
                missing_keys.append(f"{rec.get('id')}:{key}")
        acc = rec.get("accession", "")
        if not ACCESSION_RE.fullmatch(acc):
            bad_accessions.append(acc)
        if rec.get("id") != f"{acc}#{idx}":
            bad_ids.append(rec.get("id"))
        cik = (rec.get("company") or {}).get("cik", "")
        if not CIK_RE.fullmatch(cik):
            bad_ciks.append(cik)
        if rec.get("kind") not in KINDS:
            bad_kinds.append(rec.get("kind"))
        if not str(rec.get("filing_url", "")).startswith(
                "https://www.sec.gov/Archives/edgar/data/"):
            bad_urls.append(rec.get("filing_url"))
        fd = rec.get("filing_date") or ""
        if fd and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fd):
            bad_dates.append(fd)
        shares, price, value = rec.get("shares"), rec.get("price_per_share"), rec.get("value")
        for label, v in (("shares", shares), ("price", price), ("value", value)):
            if v is not None and not isinstance(v, (int, float)):
                bad_value.append(f"{rec.get('id')}:{label}")
        if shares is not None and price is not None:
            expected = round(shares * price, 2)
            if value is None or abs(value - expected) > 1e-6:
                bad_value.append(f"{rec.get('id')}:value={value} expected={expected}")

    check("ids are accession#index", not bad_ids, f"{len(bad_ids)} bad")
    ids = [r.get("id") for r in trades]
    check("record ids unique", len(ids) == len(set(ids)), f"{len(ids) - len(set(ids))} dupes")
    check("accession format", not bad_accessions, f"{len(bad_accessions)} bad")
    check("issuer CIK format", not bad_ciks, f"{len(bad_ciks)} bad")
    check("kind values", not bad_kinds, str(sorted(set(bad_kinds))))
    check("filing_url points at SEC archives", not bad_urls, f"{len(bad_urls)} bad")
    check("filing_date format", not bad_dates, f"{len(bad_dates)} bad")
    check("numeric fields + value = shares x price", not bad_value,
          f"{len(bad_value)} bad; first: {bad_value[:3]}")
    check("required keys present", not missing_keys,
          f"{len(missing_keys)} missing; first: {missing_keys[:3]}")

    # --- independent recomputation of summary ----------------------------
    n_nd = sum(1 for r in trades if r["kind"] == "non-derivative")
    n_d = sum(1 for r in trades if r["kind"] == "derivative")
    n_plan = sum(1 for r in trades if r.get("plan_10b5_1"))
    val_all = round(sum(r["value"] or 0 for r in trades
                        if r["kind"] == "non-derivative"), 2)
    val_buy = round(sum(r["value"] or 0 for r in trades
                        if r["kind"] == "non-derivative"
                        and r.get("acquired_disposed") == "A"), 2)
    val_sell = round(sum(r["value"] or 0 for r in trades
                         if r["kind"] == "non-derivative"
                         and r.get("acquired_disposed") == "D"), 2)
    filings_with = len({r["accession"] for r in trades})
    comp_count = len({r["company"]["cik"] for r in trades})

    rec = summary.get("records", {})
    check("summary.records.total", rec.get("total") == len(trades),
          f"{rec.get('total')} vs {len(trades)}")
    check("summary.records.non_derivative", rec.get("non_derivative") == n_nd)
    check("summary.records.derivative", rec.get("derivative") == n_d)
    check("summary.records.with_10b5_1_plan", rec.get("with_10b5_1_plan") == n_plan)
    v = summary.get("value", {})
    check("summary.value.all", v.get("all") == val_all, f"{v.get('all')} vs {val_all}")
    check("summary.value.buy", v.get("buy") == val_buy, f"{v.get('buy')} vs {val_buy}")
    check("summary.value.sell", v.get("sell") == val_sell, f"{v.get('sell')} vs {val_sell}")
    f = summary.get("filings", {})
    check("summary.filings.with_trades", f.get("with_trades") == filings_with)
    check("summary.filings.companies", f.get("companies") == comp_count)

    # daily values recomputed
    daily = summary.get("daily", [])
    daily_ok = True
    for slot in daily:
        rs = [r for r in trades if (r["filing_date"] or "unknown") == slot["date"]]
        if len(rs) != slot["trades"]:
            daily_ok = False
            break
        if len({r["accession"] for r in rs}) != slot["filings"]:
            daily_ok = False
            break
    check("summary.daily counts", daily_ok)

    if companies is not None:
        cik_set = {c["cik"] for c in companies}
        check("companies.json non-empty", len(companies) > 0)
        check("companies cover all issuers",
              all(r["company"]["cik"] in cik_set for r in trades))
        from collections import Counter
        expected = Counter(r["company"]["cik"] for r in trades)
        bad = [c["cik"] for c in companies if c.get("trades") != expected.get(c["cik"], 0)]
        check("companies.json trade counts", not bad, f"{len(bad)} mismatched")
        ticker_ok = all(c.get("ticker") is None or isinstance(c.get("ticker"), str)
                        for c in companies)
        check("companies.json ticker types", ticker_ok)

    m = manifest or {}
    check("manifest.transactions == trades length",
          m.get("transactions") == len(trades),
          f"{m.get('transactions')} vs {len(trades)}")
    check("manifest.filings_with_trades == summary",
          m.get("filings_with_trades") == f.get("with_trades"))

    fails = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(fails)}/{len(CHECKS)} checks passed")

    import datetime as dt
    report = {
        "checked_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "checks": [{"name": name, "passed": ok, "detail": detail}
                   for name, ok, detail in CHECKS],
        "passed": len(CHECKS) - len(fails),
        "failed": len(fails),
    }
    out = os.path.join(DATA, "validation.json")
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, out)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
