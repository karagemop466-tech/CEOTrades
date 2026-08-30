#!/usr/bin/env python3
"""CEOTrades network diagnostics.

Probes the exact official/market endpoints the collectors use, from wherever
this script runs (normally a GitHub Actions runner), and records the outcome
to collector/data/logs/diag_net.log. Used to diagnose upstream blocking
(e.g. SEC HTTP 403) before long collection runs are dispatched.

Standard library only. Polite: 11 requests total, ~1.2s apart.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
LOG_DIR = os.path.join(DATA, "logs")

UA_SEC = os.environ.get(
    "SEC_UA",
    "CEOTrades Insider-Trade Collector https://github.com/karagemop466-tech/CEOTrades "
    "karagemop466-tech@users.noreply.github.com",
)
UA_MKT = (
    "Mozilla/5.0 (compatible; CEOTrades/1.0; "
    "+https://github.com/karagemop466-tech/CEOTrades)"
)

# (label, url, headers, expect_prefix)
PROBES = [
    ("sec_master_idx_20260828",
     "https://www.sec.gov/Archives/edgar/daily-index/2026/QTR3/master.20260828.idx",
     {"User-Agent": UA_SEC, "Accept-Encoding": "gzip, deflate"},
     b"Description: Daily Index"),
    ("sec_efts_forms4_20260828",
     "https://efts.sec.gov/LATEST/search-index?dateRange=custom&startdt=2026-08-28"
     "&enddt=2026-08-28&forms=4%2C4%2FA%2C5%2C5%2FA&from=0&size=10",
     {"User-Agent": UA_SEC, "Accept-Encoding": "gzip, deflate"},
     None),
    ("sec_zip_2026q2",
     "https://www.sec.gov/files/datastandardsinnovation/data/"
     "insider-transactions-data-sets/2026q2_form345.zip",
     {"User-Agent": UA_SEC, "Accept-Encoding": "gzip, deflate"},
     b"PK"),
    ("sec_zip_2026q1",
     "https://www.sec.gov/files/structureddata/data/"
     "insider-transactions-data-sets/2026q1_form345.zip",
     {"User-Agent": UA_SEC, "Accept-Encoding": "gzip, deflate"},
     b"PK"),
    ("sec_zip_2025q4",
     "https://www.sec.gov/files/structureddata/data/"
     "insider-transactions-data-sets/2025q4_form345.zip",
     {"User-Agent": UA_SEC, "Accept-Encoding": "gzip, deflate"},
     b"PK"),
    ("sec_filing_submission_txt",
     "https://www.sec.gov/Archives/edgar/data/1672688/000167268826000049/"
     "0001672688-26-000049.txt",
     {"User-Agent": UA_SEC, "Accept-Encoding": "gzip, deflate"},
     b"<SEC-DOCUMENT>"),
    ("yahoo_chart_v8_ABSI",
     "https://query1.finance.yahoo.com/v8/finance/chart/ABSI?range=1mo&interval=1d",
     {"User-Agent": UA_MKT},
     b"chart"),
    ("stooq_csv_absi",
     "https://stooq.com/q/d/l/?s=absi.us&i=d",
     {"User-Agent": UA_MKT},
     b"Date"),
    ("nasdaq_historical_ABSI",
     "https://api.nasdaq.com/api/quote/ABSI/historical?assetclass=stocks&fromdate=2026-01-01"
     "&limit=20&todate=2026-08-28",
     {"User-Agent": UA_MKT, "Accept": "application/json, text/plain, */*",
      "Origin": "https://www.nasdaq.com",
      "Referer": "https://www.nasdaq.com/"},
     b"data"),
]


def probe(label: str, url: str, headers: dict, expect: bytes | None) -> dict:
    out = {"label": label, "url": url, "status": None, "ok": False,
           "detail": ""}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read(4096)
            out["status"] = resp.status
            out["content_type"] = resp.headers.get("Content-Type", "")
            enc = resp.headers.get("Content-Encoding")
            if enc == "gzip" and raw[:2] == b"\x1f\x8b":
                import gzip, io
                try:
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read(4096)
                except OSError:
                    raw = b"<gzip>"
            prefix = raw[:40]
            out["prefix"] = prefix.decode("latin-1", "replace")
            if expect is not None:
                out["ok"] = expect[: min(len(expect), 20)] in raw
            else:
                out["ok"] = resp.status == 200 and len(raw) > 0
    except urllib.error.HTTPError as e:
        out["status"] = e.code
        out["detail"] = f"HTTPError {e.code}: {e.reason}"
        body = e.read(300)
        out["prefix"] = body.decode("latin-1", "replace")[:200]
    except Exception as e:  # noqa: BLE001 - diagnostic probe
        out["detail"] = f"{type(e).__name__}: {e}"
    return out


def main() -> int:
    results = []
    for label, url, headers, expect in PROBES:
        results.append(probe(label, url, headers, expect))
        time.sleep(1.2)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = {"generated": stamp, "results": results}
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, "diag_net.log")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
        f.write("\n")
    print(json.dumps(doc, indent=1))
    ok = sum(1 for r in results if r["ok"])
    print(f"\n{ok}/{len(results)} probes OK")
    return 0  # probe outcome is data, not a pass/fail of the probe itself


if __name__ == "__main__":
    raise SystemExit(main())
