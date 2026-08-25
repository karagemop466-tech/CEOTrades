#!/usr/bin/env python3
"""
Self-test for the CEOTrades collector parser (no network access needed).

Exercises parse_ownership() against synthetic ownership documents built to
match the EDGAR Form 4/5 XML schema (schemaVersion X0306..X0609), covering:
  - non-derivative purchase (P) and sale (S) rows
  - derivative grant + exercise with underlying security
  - multiple transactions in one filing
  - missing price, fractional shares, footnotes, unicode names
  - Form 5 document type
  - malformed XML and non-ownership XML (must return None)
  - amendment de-duplication in merge_dataset()

Run: python3 collector/selftest.py   (exits non-zero on any failure)
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect import parse_ownership, merge_dataset  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "ok " if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


BASE = """<?xml version="1.0"?>
<ownershipDocument>
  <schemaVersion>X0609</schemaVersion>
  <documentType>{docType}</documentType>
  <periodOfReporting>{period}</periodOfReporting>
  <issuer>
    <issuerCik>{icik}</issuerCik>
    <issuerName>{iname}</issuerName>
    <issuerTradingSymbol>{itk}</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>{pcik}</rptOwnerCik>
      <rptOwnerName>{pname}</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>{d}</isDirector>
      <isOfficer>{o}</isOfficer>
      <isTenPercentOwner>{t}</isTenPercentOwner>
      <isOther>0</isOther>
      <officerTitle>{title}</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    {non_deriv}
  </nonDerivativeTable>
  <derivativeTable>
    {deriv}
  </derivativeTable>
  <footnotes>
    <footnote id="F1">10b5-1 plan adopted 2026-01-15.</footnote>
  </footnotes>
  <ownerSignature>
    <signatureName>/s/ Someone</signatureName>
    <signatureDate>{sig}</signatureDate>
  </ownerSignature>
</ownershipDocument>
"""

NON_DERIV_TX = """<nonDerivativeTransaction>
  <securityTitle><value>Common Stock</value><footnoteId id="F1"/></securityTitle>
  <transactionDate><value>2026-08-18</value></transactionDate>
  <transactionCoding>
    <transactionFormType>4</transactionFormType>
    <transactionCode>P</transactionCode>
    <equitySwapInvolved>0</equitySwapInvolved>
  </transactionCoding>
  <sharesTransactioned><value>1250</value></sharesTransactioned>
  <pricePerShare><value>95.25</value></pricePerShare>
  <sharesAcquiredOrDisposed><value>1250</value></sharesAcquiredOrDisposed>
  <acquisitionOrDispositionCode><value>A</value></acquisitionOrDispositionCode>
  <sharesOwnedFollowingTransaction><value>150000</value></sharesOwnedFollowingTransaction>
  <directOrIndirectOwnership><value>D</value></directOrIndirectOwnership>
  <derivedFromTransactions>0</derivedFromTransactions>
</nonDerivativeTransaction>"""

NON_DERIV_TX2 = """<nonDerivativeTransaction>
  <securityTitle><value>Common Stock</value></securityTitle>
  <transactionDate><value>2026-08-19</value></transactionDate>
  <transactionCoding>
    <transactionFormType>4</transactionFormType>
    <transactionCode>S</transactionCode>
    <equitySwapInvolved>0</equitySwapInvolved>
  </transactionCoding>
  <sharesTransactioned><value>625.5</value></sharesTransactioned>
  <pricePerShare><value>101</value></pricePerShare>
  <sharesAcquiredOrDisposed><value>625.5</value></sharesAcquiredOrDisposed>
  <acquisitionOrDispositionCode><value>D</value></acquisitionOrDispositionCode>
  <sharesOwnedFollowingTransaction><value>149374.5</value></sharesOwnedFollowingTransaction>
  <directOrIndirectOwnership><value>I</value></directOrIndirectOwnership>
</nonDerivativeTransaction>"""

NO_PRICE_TX = """<nonDerivativeTransaction>
  <securityTitle><value>Restricted Stock</value></securityTitle>
  <transactionDate><value>2026-07-01</value></transactionDate>
  <transactionCoding>
    <transactionFormType>4</transactionFormType>
    <transactionCode>A</transactionCode>
    <equitySwapInvolved>0</equitySwapInvolved>
  </transactionCoding>
  <sharesTransactioned><value>20000</value></sharesTransactioned>
  <pricePerShare><value></value></pricePerShare>
  <sharesAcquiredOrDisposed><value>20000</value></sharesAcquiredOrDisposed>
  <acquisitionOrDispositionCode><value>A</value></acquisitionOrDispositionCode>
  <sharesOwnedFollowingTransaction><value>20000</value></sharesOwnedFollowingTransaction>
  <directOrIndirectOwnership><value>D</value></directOrIndirectOwnership>
</nonDerivativeTransaction>"""

DERIV_GRANT = """<derivativeTransaction>
  <securityTitle><value>Stock Option (right to buy)</value></securityTitle>
  <conversionOrExercisePrice><value>10.5</value></conversionOrExercisePrice>
  <transactionDate><value>2026-08-18</value></transactionDate>
  <transactionCoding>
    <transactionFormType>4</transactionFormType>
    <transactionCode>A</transactionCode>
    <equitySwapInvolved>0</equitySwapInvolved>
  </transactionCoding>
  <sharesTransactioned><value>50000</value></sharesTransactioned>
  <sharesAcquiredOrDisposed><value>50000</value></sharesAcquiredOrDisposed>
  <acquisitionOrDispositionCode><value>A</value></acquisitionOrDispositionCode>
  <putCall><value>Call</value></putCall>
  <expirationDate><value>2036-08-18</value></expirationDate>
  <sharesOwnedFollowingTransaction><value>50000</value></sharesOwnedFollowingTransaction>
  <underlyingSecurity>
    <securityTitle><value>Common Stock</value></securityTitle>
    <conversionOrExerciseRate><value>1.0</value></conversionOrExerciseRate>
  </underlyingSecurity>
  <directOrIndirectOwnership><value>D</value></directOrIndirectOwnership>
</derivativeTransaction>"""

DERIV_EXERCISE = """<derivativeTransaction>
  <securityTitle><value>Stock Option</value></securityTitle>
  <conversionOrExercisePrice><value>10.5</value></conversionOrExercisePrice>
  <transactionDate><value>2026-08-20</value></transactionDate>
  <transactionCoding>
    <transactionFormType>4</transactionFormType>
    <transactionCode>M</transactionCode>
    <equitySwapInvolved>0</equitySwapInvolved>
  </transactionCoding>
  <sharesTransactioned><value>50000</value></sharesTransactioned>
  <sharesAcquiredOrDisposed><value>50000</value></sharesAcquiredOrDisposed>
  <acquisitionOrDispositionCode><value>A</value></acquisitionOrDispositionCode>
  <putCall><value>Call</value></putCall>
  <expirationDate><value>2036-08-18</value></expirationDate>
  <sharesOwnedFollowingTransaction><value>0</value></sharesOwnedFollowingTransaction>
  <underlyingSecurity>
    <securityTitle><value>Common Stock</value></securityTitle>
    <conversionOrExerciseRate><value>1</value></conversionOrExerciseRate>
  </underlyingSecurity>
  <directOrIndirectOwnership><value>D</value></directOrIndirectOwnership>
</derivativeTransaction>"""


def make(docType="4", period="2026-08-18", icik="0002021728", iname="Cerebras Systems Inc.",
         itk="CBRS", pcik="0002039572", pname="Mallick Dhiraj", d="0", o="1", t="0",
         title="Chief Executive Officer", non_deriv="", deriv="", sig="2026-08-20"):
    return BASE.format(
        docType=docType, period=period, icik=icik, iname=iname, itk=itk,
        pcik=pcik, pname=pname, d=d, o=o, t=t, title=title,
        non_deriv=non_deriv, deriv=deriv, sig=sig,
    ).encode("utf-8")


def test_basic_filing():
    p = parse_ownership(make(non_deriv=NON_DERIV_TX, deriv=DERIV_GRANT))
    check("parse: root fields", p is not None and p["icik"] == "0002021728"
          and p["iname"] == "Cerebras Systems Inc." and p["iticker"] == "CBRS"
          and p["pcik"] == "0002039572" and p["pname"] == "Mallick Dhiraj"
          and p["docType"] == "4" and p["period"] == "2026-08-18"
          and p["schema"] == "X0609", str(p)[:200] if p else "None")
    check("parse: relationships", p["rels"] == ["Officer"] and p["title"] == "Chief Executive Officer",
          f"rels={p['rels']} title={p['title']}")
    check("parse: 2 trades", len(p["trades"]) == 2)
    t0, t1 = p["trades"]
    check("parse: P row", t0["code"] == "P" and t0["sh"] == 1250 and t0["px"] == 95.25
          and t0["val"] == 119062.5 and t0["td"] == "2026-08-18" and t0["ad"] == "A"
          and t0["af"] == 150000 and t0["di"] == "D" and t0["der"] == 0
          and t0["sec"] == "Common Stock", str(t0))
    check("parse: derivative grant", t1["der"] == 1 and t1["code"] == "A"
          and t1["sh"] == 50000 and t1["px"] == 10.5 and t1["under"] == "Common Stock"
          and t1["putcall"] == "Call" and t1["exp"] == "2036-08-18" and t1["val"] == 525000, str(t1))


def test_sale_fractional():
    p = parse_ownership(make(non_deriv=NON_DERIV_TX2))
    t = p["trades"][0]
    check("parse: fractional S row", t["code"] == "S" and t["sh"] == 625.5 and t["px"] == 101
          and t["val"] == 63175.5 and t["di"] == "I" and t["ad"] == "D", str(t))


def test_no_price():
    p = parse_ownership(make(non_deriv=NO_PRICE_TX))
    t = p["trades"][0]
    check("parse: grant w/o price", t["code"] == "A" and t["px"] is None and t["val"] is None
          and t["sh"] == 20000, str(t))


def test_malformed():
    check("parse: malformed xml -> None", parse_ownership(b"<ownershipDocument>broken") is None)
    check("parse: non-ownership xml -> None",
          parse_ownership(b"<html><body>hi</body></html>") is None)
    check("parse: empty -> None", parse_ownership(b"") is None)


def test_form5_and_flags():
    p = parse_ownership(make(docType="5", period="2026-06-30", d="1", o="1", t="1",
                             non_deriv=NON_DERIV_TX))
    check("parse: form5 + all flags", p["docType"] == "5"
          and p["rels"] == ["Director", "Officer", "10% Owner"], str(p["rels"]))


def test_unicode_and_whitespace():
    xml = make(pname="  José  García-López ", iname="Compañía \n Internacional S.A.")
    p = parse_ownership(xml)
    check("parse: unicode/whitespace names", p["pname"] == "José García-López"
          and p["iname"] == "Compañía Internacional S.A.", str(p["pname"]))


def make_row(acc, icik, pcik, fd, period="2026-08-18", **kw):
    row = {"acc": acc, "form": "4", "fd": fd, "td": "2026-08-18", "period": period,
           "icik": icik, "co": "Cerebras Systems Inc.", "tk": "CBRS",
           "pcik": pcik, "in": "Mallick Dhiraj", "rel": "Officer", "title": "CEO",
           "code": "P", "ct": "Open market or private purchase of securities",
           "side": "buy", "sec": "Common Stock", "sh": 100, "px": 10.0, "val": 1000,
           "ad": "A", "af": 1000, "di": "D", "der": 0, "under": "", "putcall": "", "exp": ""}
    row.update(kw)
    return row


def test_merge():
    existing = [make_row("0001-26-000001", "1", "2", "2026-08-20")]
    new = [make_row("0001-26-000002", "1", "2", "2026-08-22")]  # amendment, later
    merged = merge_dataset(existing, new)
    check("merge: amendment supersedes", [r["acc"] for r in merged] == ["0001-26-000002"],
          str([r["acc"] for r in merged]))

    existing = [make_row("0001-26-000001", "1", "2", "2026-08-20")]
    new = [make_row("0001-26-000003", "1", "3", "2026-08-21")]  # different insider
    merged = merge_dataset(existing, new)
    check("merge: different insider kept", len(merged) == 2, str([r["acc"] for r in merged]))

    existing = [make_row("0001-26-000001", "1", "2", "2026-08-20")]
    new = [make_row("0001-26-000001", "1", "2", "2026-08-20", sh=999)]  # re-collect same acc
    merged = merge_dataset(existing, new)
    check("merge: re-collect replaces, no dup", len(merged) == 1 and merged[0]["sh"] == 999,
          str(merged))

    existing = [make_row("0001-26-000001", "1", "2", "2026-08-20", period="2026-08-18"),
                make_row("0001-26-000004", "1", "2", "2026-08-23", period="2026-08-21")]
    merged = merge_dataset(existing, [])
    check("merge: distinct periods kept", len(merged) == 2, str([r["acc"] for r in merged]))


def test_roundtrip_json():
    p = parse_ownership(make(non_deriv=NON_DERIV_TX, deriv=DERIV_EXERCISE))
    blob = json.dumps(p, ensure_ascii=False, separators=(",", ":"))
    back = json.loads(blob)
    check("roundtrip: json stable", back["trades"][0]["val"] == 119062.5
          and back["trades"][1]["code"] == "M")


if __name__ == "__main__":
    print("== CEOTrades collector self-test ==")
    test_basic_filing()
    test_sale_fractional()
    test_no_price()
    test_malformed()
    test_form5_and_flags()
    test_unicode_and_whitespace()
    test_merge()
    test_roundtrip_json()
    print()
    if FAILURES:
        print(f"SELF-TEST FAILED: {len(FAILURES)} failure(s): {FAILURES}")
        sys.exit(1)
    print("SELF-TEST PASSED: all checks green.")
