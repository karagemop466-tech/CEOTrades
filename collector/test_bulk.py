#!/usr/bin/env python3
"""Offline verification of bulk_backfill parsing/merging.

Builds a synthetic quarterly ZIP whose columns come verbatim from the SEC
readme (sections 5.1-5.6) and asserts every derived field. No network.
"""
from __future__ import annotations

import csv
import gzip
import io
import os
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bulk_backfill as bb  # noqa: E402

FAILED = []


def check(name, got, want):
    if got != want:
        FAILED.append(f"{name}: got {got!r}, want {want!r}")
        print(f"  FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"  ok   {name} = {got!r}")


def tsv(headers, rows):
    out = io.StringIO()
    w = csv.writer(out, delimiter="\t", lineterminator="\n")
    w.writerow(headers)
    for r in rows:
        w.writerow(r)
    return out.getvalue()


def build_zip() -> bytes:
    sub_h = ["ACCESSION_NUMBER", "FILING_DATE", "PERIOD_OF_REPORT", "DATE_OF_ORIG_SUB",
             "NO_SECURITIES_OWNED", "NOT_SUBJECT_SEC16", "FORM3_HOLDING_REPORTED",
             "FORM4_TRANS_REPORTED", "DOCUMENT_TYPE", "ISSUERCIK", "ISSUERNAME",
             "ISSUERTRADINGSYMBOL", "REMARKS"]
    sub_r = [
        ["0000320193-25-000060", "15-AUG-2025", "13-AUG-2025", "", "0", "0", "0", "1",
         "4", "0000320193", "Apple Inc.", "AAPL", ""],
        ["0000789019-25-000012", "02-JAN-2025", "31-DEC-2024", "20-DEC-2024", "0", "0",
         "0", "1", "4/A", "0000789019", "MICROSOFT CORP", "msft", "correction"],
        ["0001018724-25-000099", "10-MAR-2025", "10-MAR-2025", "", "0", "0", "0", "1",
         "5", "0001018724", "AMAZON COM INC", "AMZN", ""],
    ]

    own_h = ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP",
             "RPTOWNER_TITLE", "RPTOWNER_TXT", "RPTOWNER_STREET1", "RPTOWNER_CITY",
             "RPTOWNER_STATE", "RPTOWNER_ZIPCODE", "FILE_NUMBER"]
    own_r = [
        ["0000320193-25-000060", "0001214156", "COOK TIMOTHY D", "OFFICER",
         "Chief Executive Officer", "", "1 APPLE PARK WAY", "CUPERTINO", "CA", "95014", "001-36743"],
        # Joint filing: two owners, mixed relationships.
        ["0000789019-25-000012", "0001234567", "NADELLA SATYA", "OFFICER|DIRECTOR",
         "Chairman and CEO", "", "1 MICROSOFT WAY", "REDMOND", "WA", "98052", "001-37845"],
        ["0000789019-25-000012", "0007654321", "SMITH FAMILY TRUST", "TENPERCENTOWNER",
         "", "", "1 MICROSOFT WAY", "REDMOND", "WA", "98052", "001-37845"],
    ]  # note: no owner row for the Amazon accession -> exercises the empty-owner path

    nd_h = ["ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "SECURITY_TITLE", "TRANS_DATE",
            "DEEMED_EXECUTION_DATE", "TRANS_FORM_TYPE", "TRANS_CODE",
            "EQUITY_SWAP_INVOLVED", "TRANS_TIMELINESS", "TRANS_SHARES",
            "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD", "SHRS_OWND_FOLWNG_TRANS",
            "DIRECT_INDIRECT_OWNERSHIP", "NATURE_OF_OWNERSHIP"]
    nd_r = [
        # Purchase with price -> value must be shares*price.
        ["0000320193-25-000060", "1", "Common Stock", "13-AUG-2025", "", "4", "P",
         "0", "", "1000.00", "225.50", "A", "3280000.00", "D", ""],
        # Sale, late-filed.
        ["0000320193-25-000060", "2", "Common Stock", "13-AUG-2025", "", "4", "S",
         "0", "L", "500.00", "226.00", "D", "3279500.00", "D", ""],
        # Grant with no price -> value must be None, not 0.
        ["0000789019-25-000012", "3", "Common Stock", "31-DEC-2024", "", "4", "A",
         "0", "", "2500.00", "", "A", "100000.00", "I", "By Trust"],
        # Missing TRANS_DATE -> falls back to DEEMED_EXECUTION_DATE.
        ["0001018724-25-000099", "4", "Common Stock", "", "05-MAR-2025", "5", "G",
         "0", "E", "10.00", "", "D", "42.00", "D", ""],
        # Orphan: no SUBMISSION parent -> must be dropped, not crash.
        ["9999999999-99-999999", "5", "Common Stock", "01-JAN-2025", "", "4", "P",
         "0", "", "1.00", "1.00", "A", "1.00", "D", ""],
    ]

    dv_h = ["ACCESSION_NUMBER", "DERIV_TRANS_SK", "SECURITY_TITLE", "CONV_EXERCISE_PRICE",
            "TRANS_DATE", "DEEMED_EXECUTION_DATE", "TRANS_FORM_TYPE", "TRANS_CODE",
            "EQUITY_SWAP_INVOLVED", "TRANS_TIMELINESS", "TRANS_SHARES",
            "TRANS_TOTAL_VALUE", "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD",
            "EXCERCISE_DATE", "EXPIRATION_DATE", "UNDLYING_SEC_TITLE",
            "UNDLYING_SEC_SHARES", "UNDLYING_SEC_VALUE", "SHRS_OWND_FOLWNG_TRANS",
            "DIRECT_INDIRECT_OWNERSHIP", "NATURE_OF_OWNERSHIP"]
    dv_r = [
        # Option exercise: no per-share price, but TRANS_TOTAL_VALUE present.
        ["0000320193-25-000060", "1", "Stock Option (right to buy)", "95.00",
         "13-AUG-2025", "", "4", "M", "0", "", "4000.00", "380000.00", "", "D",
         "01-JAN-2020", "31-DEC-2029", "Common Stock", "4000.00", "", "0.00", "D", ""],
    ]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("SUBMISSION.tsv", tsv(sub_h, sub_r))
        z.writestr("REPORTINGOWNER.tsv", tsv(own_h, own_r))
        z.writestr("NONDERIV_TRANS.tsv", tsv(nd_h, nd_r))
        z.writestr("DERIV_TRANS.tsv", tsv(dv_h, dv_r))
        # NONDERIV_HOLDING intentionally omitted: absent members must be tolerated.
    return buf.getvalue()


def main() -> int:
    print("1. date parsing")
    check("DD-MON-YYYY", bb.parse_date("15-AUG-2025"), "2025-08-15")
    check("single digit day", bb.parse_date("5-JAN-2006"), "2006-01-05")
    check("iso passthrough", bb.parse_date("2025-08-15"), "2025-08-15")
    check("with time suffix", bb.parse_date("15-AUG-2025 00:00:00"), "2025-08-15")
    check("empty", bb.parse_date(""), "")
    check("garbage", bb.parse_date("not a date"), "")
    check("bad month", bb.parse_date("15-XXX-2025"), "")

    print("2. numeric parsing")
    check("plain", bb.fnum("1000.00"), 1000.0)
    check("commas", bb.fnum("1,234.5"), 1234.5)
    check("blank -> None", bb.fnum(""), None)
    check("NULL -> None", bb.fnum("NULL"), None)
    check("text -> None", bb.fnum("n/a"), None)
    check("zero kept", bb.fnum("0"), 0.0)

    print("3. relationship labels")
    check("officer+director", bb.rel_label("OFFICER|DIRECTOR", ""), "Director/Officer")
    check("tenpercent", bb.rel_label("TENPERCENTOWNER", ""), "10% Owner")
    check("empty", bb.rel_label("", ""), "Unknown")

    print("4. quarter parse")
    rows = bb.quarter_rows(build_zip(), "2025Q3")
    check("row count (5 kept, 1 orphan dropped)", len(rows), 5)

    by = {}
    for r in rows:
        by[(r["acc"], r["der"], r["code"])] = r

    p = by[("0000320193-25-000060", 0, "P")]
    print("  -- Apple purchase --")
    check("filing date", p["fd"], "2025-08-15")
    check("transaction date", p["td"], "2025-08-13")
    check("period", p["period"], "2025-08-13")
    check("form", p["form"], "4")
    check("amend", p["amend"], 0)
    check("company", p["co"], "Apple Inc.")
    check("ticker", p["tk"], "AAPL")
    check("issuer cik", p["icik"], "320193")
    check("insider", p["in"], "COOK TIMOTHY D")
    check("insider cik", p["pcik"], "1214156")
    check("relationship", p["rel"], "Officer")
    check("title", p["title"], "Chief Executive Officer")
    check("side", p["side"], "buy")
    check("shares", p["sh"], 1000.0)
    check("price", p["px"], 225.50)
    check("value = sh*px", p["val"], 225500.00)
    check("acquired", p["ad"], "A")
    check("shares after", p["af"], 3280000.0)
    check("direct", p["di"], "D")
    check("derivative flag", p["der"], 0)
    check("code text", p["ct"], bb.CODE_TEXT["P"])

    s = by[("0000320193-25-000060", 0, "S")]
    print("  -- Apple sale --")
    check("side", s["side"], "sell")
    check("timeliness late", s["timely"], "L")
    check("value", s["val"], 113000.00)

    a = by[("0000789019-25-000012", 0, "A")]
    print("  -- Microsoft grant (joint, amended) --")
    check("amend flag", a["amend"], 1)
    check("form root", a["form"], "4")
    check("ticker uppercased", a["tk"], "MSFT")
    check("joint name", a["in"], "NADELLA SATYA (+1 joint)")
    check("owner count", a["own_n"], 2)
    check("merged relationships", a["rel"], "Director/Officer/10% Owner")
    check("no price -> value None", a["val"], None)
    check("price None", a["px"], None)
    check("indirect", a["di"], "I")
    check("nature", a["nature"], "By Trust")
    check("side", a["side"], "grant")

    gf = by[("0001018724-25-000099", 0, "G")]
    print("  -- Amazon gift (no owner row, deemed date) --")
    check("td from deemed date", gf["td"], "2025-03-05")
    check("form 5", gf["form"], "5")
    check("no owners -> blank name", gf["in"], "")
    check("no owners -> Unknown rel", gf["rel"], "Unknown")
    check("owner count 0", gf["own_n"], 0)
    check("side gift", gf["side"], "gift")
    check("timeliness early", gf["timely"], "E")

    d = by[("0000320193-25-000060", 1, "M")]
    print("  -- Apple option exercise (derivative) --")
    check("derivative flag", d["der"], 1)
    check("security", d["sec"], "Stock Option (right to buy)")
    check("exercise price", d["xp"], 95.0)
    check("expiration", d["exp"], "2029-12-31")
    check("underlying", d["under"], "Common Stock")
    check("underlying shares", d["under_sh"], 4000.0)
    check("value falls back to TOTAL_VALUE", d["val"], 380000.0)
    check("side exercise", d["side"], "exercise")

    print("5. shard round-trip + idempotency")
    tmp = tempfile.mkdtemp()
    orig = bb.DATA
    try:
        bb.DATA = tmp
        info1 = bb.merge_into_store(rows)
        check("first merge adds all", info1["added"], 5)
        b1 = open(os.path.join(tmp, "trades-2025.csv.gz"), "rb").read()
        info2 = bb.merge_into_store(rows)
        check("re-merge adds nothing", info2["added"], 0)
        b2 = open(os.path.join(tmp, "trades-2025.csv.gz"), "rb").read()
        check("bytes identical (no git churn)", b1 == b2, True)
        with gzip.open(os.path.join(tmp, "trades-2025.csv.gz"), "rt", encoding="utf-8") as f:
            back = list(csv.DictReader(f))
        check("rows survive round-trip", len(back), 5)
        check("header matches FIELDS", list(back[0].keys()), bb.FIELDS)
        rt = [r for r in back if r["acc"] == "0000320193-25-000060"
              and r["der"] == "0" and r["code"] == "P"][0]
        check("value survives round-trip", rt["val"], "225500.0")
        check("empty price is blank", [r for r in back if r["code"] == "A"][0]["px"], "")
    finally:
        bb.DATA = orig
        shutil.rmtree(tmp)

    print()
    if FAILED:
        print(f"FAILED ({len(FAILED)}):")
        for f in FAILED:
            print("  - " + f)
        return 1
    print("ALL BULK BACKFILL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
