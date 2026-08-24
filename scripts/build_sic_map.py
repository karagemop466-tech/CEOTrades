#!/usr/bin/env python3
"""Build docs/data/sic_codes.json from the official SEC SIC code list.

Source: https://www.sec.gov/search-filings/standard-industrial-classification-sic-code-list
(the SEC-published table of SIC codes --> industry title).

The output records the source URL and fetch time so the mapping is auditable.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from seclib import SIC_LIST_URL, fetch_text  # noqa: E402

OUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docs", "data", "sic_codes.json",
)


def parse_sic_table(page: str) -> dict:
    """Parse the SEC table (columns: SIC Code | Office | Industry Title)."""
    codes = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
        if len(cells) < 3:
            continue
        raw_code = re.sub(r"<[^>]+>", "", cells[0]).strip()
        if not re.fullmatch(r"\d{3,4}", raw_code):
            continue
        office = html.unescape(re.sub(r"<[^>]+>", "", cells[1])).strip()
        title = html.unescape(re.sub(r"<[^>]+>", "", cells[2])).strip()
        codes[raw_code] = {"office": office, "title": title}
    return codes


def main():
    page = fetch_text(SIC_LIST_URL)
    codes = parse_sic_table(page)
    if not codes:
        print("ERROR: parsed 0 SIC codes", file=sys.stderr)
        return 1
    doc = {
        "source": SIC_LIST_URL,
        "fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(codes),
        "codes": codes,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)
    print(f"Wrote {len(codes)} SIC codes to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
