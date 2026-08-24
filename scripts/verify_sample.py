#!/usr/bin/env python3
"""Live cross-verification of published data against SEC EDGAR.

Picks a deterministic random sample of trade records from docs/data/trades.json,
re-downloads each record's actual filing from SEC, re-parses it, and confirms
the issuer CIK, owner CIK, accession and the specific transaction fields
(code / date / shares / price / kind) match what the dashboard publishes.

Writes docs/data/verification.json and exits non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from seclib import (  # noqa: E402
    ARCHIVES_BASE,
    FetchError,
    ParseError,
    cik_nodash,
    extract_ownership_xml_from_submission,
    fetch_text,
    parse_ownership_xml,
)

DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docs", "data",
)


def fetch_filing_xml(accession: str, cik: str) -> str:
    """Download the combined submission and pull out the ownership XML."""
    base = f"{ARCHIVES_BASE}/{cik_nodash(cik)}/{accession.replace('-', '')}"
    text = fetch_text(f"{base}/{accession}.txt")
    return extract_ownership_xml_from_submission(text)


def verify_record(rec: dict) -> dict:
    acc = rec["accession"]
    issuer_cik = rec["company"]["cik"]
    result = {"accession": acc, "filing_url": rec["filing_url"], "checks": {}}
    try:
        xml_text = fetch_filing_xml(acc, issuer_cik)
        doc = parse_ownership_xml(xml_text)
    except (FetchError, ParseError, ValueError) as exc:
        result["pass"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["checks"]["download_and_parse"] = False
        return result

    checks = result["checks"]
    checks["issuer_cik"] = doc["issuer"]["cik"] == issuer_cik
    checks["owner_cik"] = doc["owner"]["cik"] == rec["owner"]["cik"]
    checks["period_end"] = (doc["period_of_report"] == rec["period_end"]
                            or not rec["period_end"])

    # Find the same transaction inside the freshly parsed filing.
    all_tx = doc["non_derivative"] + doc["derivative"]
    wanted = rec["kind"]
    candidates = [tx for tx in all_tx if tx["kind"] == wanted]
    matches = [tx for tx in candidates if (
        tx.get("code") == rec.get("code")
        and tx.get("date") == rec.get("date")
        and tx.get("shares") == rec.get("shares")
        and tx.get("price_per_share") == rec.get("price_per_share")
    )]
    checks["transaction_present"] = bool(matches)
    result["matched_transactions"] = len(matches)

    result["pass"] = all(checks.values())
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=20, help="records to verify")
    parser.add_argument("--seed", type=int, default=20260824)
    parser.add_argument("--sleep", type=float, default=0.6,
                        help="seconds between SEC requests")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    with open(os.path.join(DATA, "trades.json"), encoding="utf-8") as fh:
        trades = json.load(fh)
    if not trades:
        print("no trades to verify", file=sys.stderr)
        return 1

    rng = random.Random(args.seed)
    sample = rng.sample(trades, min(args.n, len(trades)))
    print(f"Verifying {len(sample)} of {len(trades)} records (seed={args.seed})")

    import time
    from seclib import RATE_LIMITER  # respect global pacing

    results = []
    for rec in sample:
        RATE_LIMITER.min_interval = args.sleep
        res = verify_record(rec)
        results.append(res)
        status = "ok  " if res["pass"] else "FAIL"
        print(f"  {status} {rec['accession']} "
              f"({rec['company']['ticker']} {rec['code']} "
              f"{rec.get('date')})")
        if not res["pass"]:
            print(f"        {json.dumps(res.get('error') or res['checks'])}")
        time.sleep(args.sleep)

    doc = {
        "checked_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": "re-download each sampled filing from "
                  "https://www.sec.gov/Archives/edgar/data/ and re-parse the "
                  "ownership XML; compare issuer CIK, owner CIK, period and "
                  "transaction fields against docs/data/trades.json",
        "sample_size": len(results),
        "passed": sum(1 for r in results if r["pass"]),
        "failed": sum(1 for r in results if not r["pass"]),
        "seed": args.seed,
        "results": results,
    }
    out = os.path.join(DATA, "verification.json")
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, out)
    print(f"\n{doc['passed']}/{doc['sample_size']} verified; wrote {out}")
    return 0 if doc["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
