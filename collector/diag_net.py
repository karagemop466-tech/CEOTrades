#!/usr/bin/env python3
"""Upstream connectivity diagnostics for the CEOTrades pipeline.

Polite, bounded probe set (<= 12 requests) covering every upstream the
pipeline depends on: SEC EDGAR daily index, EDGAR full-text search, SEC
quarterly insider-transaction ZIPs, an actual Form 4 submission text, plus
the market-data providers (Yahoo chart, Stooq, Nasdaq). The result is
appended to collector/data/logs/diag_net.log so every run carries the
evidence of what the runner could and could not reach (e.g. SEC HTTP 403
blocks) — the log travels in the repo and is inspectable after the fact.

Exit code is always 0: this is a probe, not a gate.
"""
from __future__ import annotations

import datetime as _dt
import gzip
import io
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "data", "logs", "diag_net.log")

UA = ("CEOTrades pipeline diagnostics "
      "https://github.com/karagemop466-tech/CEOTrades "
      "karagemop466-tech@users.noreply.github.com")


def _probe(name: str, url: str, headers: dict | None = None, read: bool = False) -> dict:
    out = {"name": name, "url": url}
    req = urllib.request.Request(url, headers=dict({"User-Agent": UA}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            out["status"] = resp.status
            out["content_type"] = resp.headers.get("Content-Type", "")
            out["content_length"] = resp.headers.get("Content-Length", "")
            if read:
                body = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip" or body[:2] == b"\x1f\x8b":
                    body = gzip.GzipFile(fileobj=io.BytesIO(body)).read()
                out["bytes"] = len(body)
                out["preview"] = body[:80].decode("utf-8", "replace").replace("\n", " ")
    except urllib.error.HTTPError as e:
        out["status"] = e.code
        out["error"] = f"HTTP {e.code}"
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        out["status"] = None
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def main() -> int:
    # A known-published daily index (verified present on SEC as of 2026-08-30).
    days = (date.today() - timedelta(days=2)).strftime("%Y%m%d")
    year_qtr = f"{date.today().year}Q{(date.today().month - 1) // 3 + 1}"
    probes = [
        _probe("sec_daily_index", f"https://www.sec.gov/Archives/edgar/daily-index/master.{days}.idx"),
        _probe("sec_efts_search", ("https://efts.sec.gov/LATEST/search-index?q=%22&dateRange=custom"
                                   f"&startdt={date.today() - timedelta(days=2):%Y-%m-%d}"
                                   f"&enddt={date.today() - timedelta(days=2):%Y-%m-%d}&forms=4"),
               headers={"Accept": "application/json"}, read=True),
        _probe("sec_quarter_zip_new", "https://www.sec.gov/files/datastandardsinnovation/data/"
                                      f"insider-transactions-data-sets/{year_qtr.lower()}_form345.zip"),
        _probe("sec_quarter_zip_old", "https://www.sec.gov/files/structureddata/data/"
                                      f"insider-transactions-data-sets/{year_qtr.lower()}_form345.zip"),
        _probe("sec_filing_text", "https://www.sec.gov/Archives/edgar/data/1855509/"
                                  "000173112226001019/0001731122-26-001019-index.htm", read=True),
        _probe("yahoo_chart", "https://query1.finance.yahoo.com/v8/finance/chart/ABSI"
                              "?interval=1d&range=5y&events=split", read=True),
        _probe("yahoo_chart_2", "https://query2.finance.yahoo.com/v8/finance/chart/ABSI"
                                "?interval=1d&range=5y&events=split"),
        _probe("stooq_csv", "https://stooq.com/q/d/l/?s=absi.us&i=d", read=True),
        _probe("nasdaq_api", "https://api.nasdaq.com/api/quote/ABSI/historical"
                             "?assetclass=stocks&limit=5",
               headers={"Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/",
                        "Accept": "application/json, text/plain, */*"}, read=True),
    ]
    stamp = _dt.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    lines = [f"== diag_net run {stamp}"]
    for p in probes:
        lines.append(json.dumps(p, separators=(",", ":")))
        print(json.dumps(p, separators=(",", ":")), flush=True)
    lines.append("")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
