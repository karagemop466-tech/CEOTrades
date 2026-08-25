#!/usr/bin/env python3
"""
Self-test for the CEOTrades collector (no network access needed).

Fixtures are modeled 1:1 on REAL EDGAR artifacts verified live on 2026-08-25:
  - ownership XML structure from accession 0001189770-26-000008
    (ESTEE LAUDER COMPANIES INC, Form 4 filed 2026-08-24, schema X0609)
  - daily master index line format from
    /Archives/edgar/daily-index/2026/QTR3/master.20260824.idx
  - full-submission wrapping (<SEC-DOCUMENT>/<DOCUMENT>/<TEXT>/<XML>)

Covers:
  - non-derivative purchase (P) and sale (S) rows
  - derivative exercise with underlying security and expiration
  - grant without a price (value must be None, not 0)
  - multiple reporting owners, unicode, whitespace
  - malformed XML / non-ownership XML (must return None)
  - master.idx parsing incl. per-owner duplicate rows and 4/A
  - ownership-XML extraction from a full submission .txt
  - amendment de-duplication in merge_dataset()

Run: python3 collector/selftest.py   (exits non-zero on any failure)
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect import (  # noqa: E402
    extract_ownership_xml,
    merge_dataset,
    parse_master_idx,
    parse_ownership,
)

FAILURES = []


def check(name, cond, detail=""):
    status = "ok " if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ---------------------------------------------------------------------------
# Fixtures — element names match live EDGAR ownership XML exactly.
# ---------------------------------------------------------------------------

BASE = """<?xml version="1.0"?>
<ownershipDocument>
    <schemaVersion>X0609</schemaVersion>
    <documentType>{docType}</documentType>
    <periodOfReport>{period}</periodOfReport>
    <notSubjectToSection16>0</notSubjectToSection16>
    <issuer>
        <issuerCik>{icik}</issuerCik>
        <issuerName>{iname}</issuerName>
        <issuerTradingSymbol>{itk}</issuerTradingSymbol>
        <issuerForeignTradingSymbol></issuerForeignTradingSymbol>
    </issuer>
{owners}
    <aff10b5One>0</aff10b5One>
    <nonDerivativeTable>
    {non_deriv}
    </nonDerivativeTable>
    <derivativeTable>
    {deriv}
    </derivativeTable>
    <footnotes>
        <footnote id="F1">Shares held by LLC.</footnote>
    </footnotes>
    <remarks></remarks>
    <ownerSignature>
        <signatureName>/s/ Someone, Attorney-in-fact</signatureName>
        <signatureDate>{sig}</signatureDate>
    </ownerSignature>
</ownershipDocument>
"""

OWNER = """    <reportingOwner>
        <reportingOwnerId>
            <rptOwnerCik>{pcik}</rptOwnerCik>
            <rptOwnerName>{pname}</rptOwnerName>
        </reportingOwnerId>
        <reportingOwnerAddress>
            <rptOwnerStreet1>767 FIFTH AVE</rptOwnerStreet1>
            <rptOwnerCity>NEW YORK</rptOwnerCity>
            <rptOwnerState>NY</rptOwnerState>
            <rptOwnerZipCode>10153</rptOwnerZipCode>
        </reportingOwnerAddress>
        <reportingOwnerRelationship>
            <isDirector>{d}</isDirector>
            <isOfficer>{o}</isOfficer>
            <isTenPercentOwner>{t}</isTenPercentOwner>
            <isOther>0</isOther>
            {title_el}
        </reportingOwnerRelationship>
    </reportingOwner>
"""

PURCHASE_TX = """<nonDerivativeTransaction>
    <securityTitle><value>Class A Common Stock</value></securityTitle>
    <transactionDate><value>2026-08-21</value></transactionDate>
    <transactionCoding>
        <transactionFormType>4</transactionFormType>
        <transactionCode>P</transactionCode>
        <equitySwapInvolved>0</equitySwapInvolved>
    </transactionCoding>
    <transactionAmounts>
        <transactionShares><value>1250</value></transactionShares>
        <transactionPricePerShare><value>95.25</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
    </transactionAmounts>
    <postTransactionAmounts>
        <sharesOwnedFollowingTransaction><value>150000</value></sharesOwnedFollowingTransaction>
    </postTransactionAmounts>
    <ownershipNature>
        <directOrIndirectOwnership><value>D</value></directOrIndirectOwnership>
    </ownershipNature>
</nonDerivativeTransaction>"""

SALE_TX = """<nonDerivativeTransaction>
    <securityTitle><value>Common Stock</value></securityTitle>
    <transactionDate><value>2026-08-19</value></transactionDate>
    <transactionCoding>
        <transactionFormType>4</transactionFormType>
        <transactionCode>S</transactionCode>
        <equitySwapInvolved>0</equitySwapInvolved>
    </transactionCoding>
    <transactionAmounts>
        <transactionShares><value>625.5</value></transactionShares>
        <transactionPricePerShare><value>101</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
    </transactionAmounts>
    <postTransactionAmounts>
        <sharesOwnedFollowingTransaction><value>149374.5</value></sharesOwnedFollowingTransaction>
    </postTransactionAmounts>
    <ownershipNature>
        <directOrIndirectOwnership><value>I</value></directOrIndirectOwnership>
        <natureOfOwnership><value>by LLC</value><footnoteId id="F1"/></natureOfOwnership>
    </ownershipNature>
</nonDerivativeTransaction>"""

GRANT_NO_PRICE_TX = """<nonDerivativeTransaction>
    <securityTitle><value>Restricted Stock Units</value></securityTitle>
    <transactionDate><value>2026-07-01</value></transactionDate>
    <transactionCoding>
        <transactionFormType>4</transactionFormType>
        <transactionCode>A</transactionCode>
        <equitySwapInvolved>0</equitySwapInvolved>
    </transactionCoding>
    <transactionAmounts>
        <transactionShares><value>20000</value></transactionShares>
        <transactionPricePerShare><footnoteId id="F1"/></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
    </transactionAmounts>
    <postTransactionAmounts>
        <sharesOwnedFollowingTransaction><value>20000</value></sharesOwnedFollowingTransaction>
    </postTransactionAmounts>
    <ownershipNature>
        <directOrIndirectOwnership><value>D</value></directOrIndirectOwnership>
    </ownershipNature>
</nonDerivativeTransaction>"""

# Mirrors the derivative leg of accession 0001189770-26-000008 exactly.
DERIV_EXERCISE_TX = """<derivativeTransaction>
    <securityTitle><value>Stock Option (Right to Buy)</value></securityTitle>
    <conversionOrExercisePrice><value>78.36</value></conversionOrExercisePrice>
    <transactionDate><value>2026-08-21</value></transactionDate>
    <transactionCoding>
        <transactionFormType>4</transactionFormType>
        <transactionCode>M</transactionCode>
        <equitySwapInvolved>0</equitySwapInvolved>
    </transactionCoding>
    <transactionAmounts>
        <transactionShares><value>4697</value></transactionShares>
        <transactionPricePerShare><footnoteId id="F1"/></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
    </transactionAmounts>
    <exerciseDate><value>2017-11-11</value></exerciseDate>
    <expirationDate><value>2026-11-11</value></expirationDate>
    <underlyingSecurity>
        <underlyingSecurityTitle><value>Class A Common Stock</value></underlyingSecurityTitle>
        <underlyingSecurityShares><value>4697</value></underlyingSecurityShares>
    </underlyingSecurity>
    <postTransactionAmounts>
        <sharesOwnedFollowingTransaction><value>0</value></sharesOwnedFollowingTransaction>
    </postTransactionAmounts>
    <ownershipNature>
        <directOrIndirectOwnership><value>I</value></directOrIndirectOwnership>
        <natureOfOwnership><value>by LLC</value><footnoteId id="F1"/></natureOfOwnership>
    </ownershipNature>
</derivativeTransaction>"""


def make(docType="4", period="2026-08-21", icik="0001001250",
         iname="ESTEE LAUDER COMPANIES INC", itk="EL",
         owners=None, non_deriv="", deriv="", sig="2026-08-24"):
    if owners is None:
        owners = [dict(pcik="0001189770", pname="ZANNINO RICHARD F",
                       d="1", o="0", t="0", title="")]
    owner_xml = ""
    for ow in owners:
        title_el = (f"<officerTitle>{ow['title']}</officerTitle>"
                    if ow.get("title") else "<officerTitle></officerTitle>")
        owner_xml += OWNER.format(pcik=ow["pcik"], pname=ow["pname"],
                                  d=ow["d"], o=ow["o"], t=ow["t"],
                                  title_el=title_el)
    return BASE.format(docType=docType, period=period, icik=icik, iname=iname,
                       itk=itk, owners=owner_xml, non_deriv=non_deriv,
                       deriv=deriv, sig=sig).encode("utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_real_shape_filing():
    p = parse_ownership(make(non_deriv=PURCHASE_TX, deriv=DERIV_EXERCISE_TX))
    check("parse: root fields", p is not None and p["icik"] == "1001250"
          and p["iname"] == "ESTEE LAUDER COMPANIES INC" and p["iticker"] == "EL"
          and p["docType"] == "4" and p["period"] == "2026-08-21"
          and p["schema"] == "X0609", str(p)[:200] if p else "None")
    o = p["owners"][0]
    check("parse: owner", o["cik"] == "1189770" and o["name"] == "ZANNINO RICHARD F"
          and o["rels"] == ["Director"], str(o))
    check("parse: 2 trades", len(p["trades"]) == 2, str(len(p["trades"])))
    t0 = next(t for t in p["trades"] if t["der"] == 0)
    t1 = next(t for t in p["trades"] if t["der"] == 1)
    check("parse: P row", t0["code"] == "P" and t0["sh"] == 1250 and t0["px"] == 95.25
          and t0["val"] == 119062.5 and t0["td"] == "2026-08-21" and t0["ad"] == "A"
          and t0["af"] == 150000 and t0["di"] == "D"
          and t0["sec"] == "Class A Common Stock", str(t0))
    check("parse: M derivative row", t1["code"] == "M" and t1["sh"] == 4697
          and t1["px"] is None and t1["val"] is None and t1["xp"] == 78.36
          and t1["under"] == "Class A Common Stock" and t1["exp"] == "2026-11-11"
          and t1["ad"] == "D" and t1["af"] == 0 and t1["di"] == "I", str(t1))


def test_sale_fractional():
    p = parse_ownership(make(non_deriv=SALE_TX))
    t = p["trades"][0]
    check("parse: fractional S row", t["code"] == "S" and t["sh"] == 625.5
          and t["px"] == 101 and t["val"] == 63175.5 and t["di"] == "I"
          and t["ad"] == "D" and t["af"] == 149374.5, str(t))


def test_no_price():
    p = parse_ownership(make(non_deriv=GRANT_NO_PRICE_TX))
    t = p["trades"][0]
    check("parse: grant w/o price -> val None", t["code"] == "A" and t["px"] is None
          and t["val"] is None and t["sh"] == 20000, str(t))


def test_malformed():
    check("parse: malformed xml -> None", parse_ownership(b"<ownershipDocument>broken") is None)
    check("parse: non-ownership xml -> None",
          parse_ownership(b"<html><body>hi</body></html>") is None)
    check("parse: empty -> None", parse_ownership(b"") is None)


def test_form5_flags_titles():
    owners = [dict(pcik="0000123456", pname="DOE JANE", d="1", o="1", t="1",
                   title="Chief Executive Officer")]
    p = parse_ownership(make(docType="5", period="2026-06-30", owners=owners,
                             non_deriv=PURCHASE_TX))
    o = p["owners"][0]
    check("parse: form5 + all flags", p["docType"] == "5"
          and o["rels"] == ["Director", "Officer", "10% Owner"]
          and o["title"] == "Chief Executive Officer", str(o))


def test_multiple_owners():
    owners = [dict(pcik="0000000001", pname="ALPHA LLC", d="0", o="0", t="1", title=""),
              dict(pcik="0000000002", pname="BETA GP", d="0", o="0", t="1", title="")]
    p = parse_ownership(make(owners=owners, non_deriv=PURCHASE_TX))
    check("parse: multiple reporting owners", len(p["owners"]) == 2
          and p["owners"][1]["name"] == "BETA GP", str(p["owners"]))


def test_unicode_whitespace():
    owners = [dict(pcik="0000000009", pname="  José  García-López ",
                   d="1", o="0", t="0", title="")]
    p = parse_ownership(make(owners=owners, iname="Compañía \n Internacional S.A.",
                             non_deriv=PURCHASE_TX))
    check("parse: unicode/whitespace", p["owners"][0]["name"] == "José García-López"
          and p["iname"] == "Compañía Internacional S.A.", str(p["owners"][0]))


# Real line format from master.20260824.idx (verified live).
MASTER_IDX = b"""Description:           Daily Index of EDGAR Dissemination Feed
Last Data Received:    Aug 24, 2026
Comments:              webmaster@sec.gov
Anonymous FTP:         ftp://ftp.sec.gov/edgar/

CIK|Company Name|Form Type|Date Filed|File Name
--------------------------------------------------------------------------------
1000184|SAP SE|S-8|20260824|edgar/data/1000184/0001104659-26-100300.txt
1001085|BROOKFIELD Corp /ON/|4|20260824|edgar/data/1001085/0001193125-26-362969.txt
1001250|ESTEE LAUDER COMPANIES INC|4|20260824|edgar/data/1001250/0001189770-26-000008.txt
1189770|ZANNINO RICHARD F|4|20260824|edgar/data/1001250/0001189770-26-000008.txt
1006830|CONSUMERS BANCORP INC /OH/|4|20260824|edgar/data/1006830/0001437749-26-028799.txt
2000001|SOME FILER|4/A|20260824|edgar/data/2000001/0009999999-26-000001.txt
2000002|ANNUAL GUY|5|20260824|edgar/data/2000002/0009999999-26-000002.txt
1002910|AMEREN CORP|8-K|20260824|edgar/data/1002910/0001104659-26-100236.txt
"""


def test_master_idx():
    recs = parse_master_idx(MASTER_IDX)
    accs = [r["acc"] for r in recs]
    check("idx: only Form 4/5 kept, deduped", accs == [
        "0001193125-26-362969", "0001189770-26-000008",
        "0001437749-26-028799", "0009999999-26-000001",
        "0009999999-26-000002"], str(accs))
    r_el = next(r for r in recs if r["acc"] == "0001189770-26-000008")
    check("idx: fields", r_el["form"] == "4" and r_el["amend"] == 0
          and r_el["fd"] == "2026-08-24"
          and r_el["path"] == "edgar/data/1001250/0001189770-26-000008.txt", str(r_el))
    r_a = next(r for r in recs if r["acc"] == "0009999999-26-000001")
    check("idx: 4/A flagged as amendment", r_a["form"] == "4" and r_a["amend"] == 1, str(r_a))
    r_5 = next(r for r in recs if r["acc"] == "0009999999-26-000002")
    check("idx: form 5 kept", r_5["form"] == "5" and r_5["amend"] == 0, str(r_5))


def test_submission_extraction():
    xml = make(non_deriv=PURCHASE_TX).decode()
    submission = ("<SEC-DOCUMENT>0001189770-26-000008.txt : 20260824\n"
                  "<SEC-HEADER>...</SEC-HEADER>\n"
                  "<DOCUMENT>\n<TYPE>4\n<SEQUENCE>1\n<FILENAME>form4.xml\n"
                  "<DESCRIPTION>FORM 4\n<TEXT>\n<XML>\n" + xml +
                  "\n</XML>\n</TEXT>\n</DOCUMENT>\n</SEC-DOCUMENT>\n").encode()
    extracted = extract_ownership_xml(submission)
    check("extract: xml block found", extracted is not None)
    p = parse_ownership(extracted)
    check("extract: parses to same doc", p is not None
          and p["icik"] == "1001250" and len(p["trades"]) == 1,
          str(p)[:150] if p else "None")
    check("extract: no ownership xml -> None",
          extract_ownership_xml(b"<DOCUMENT><TEXT>plain text</TEXT></DOCUMENT>") is None)


def make_row(acc, icik, pcik, fd, period="2026-08-21", amend=0, **kw):
    row = {"acc": acc, "form": "4", "amend": amend, "fd": fd, "td": "2026-08-21",
           "period": period, "icik": icik, "co": "ESTEE LAUDER COMPANIES INC",
           "tk": "EL", "pcik": pcik, "in": "ZANNINO RICHARD F", "own_n": 1,
           "rel": "Director", "title": "",
           "code": "P", "ct": "Open market or private purchase of securities",
           "side": "buy", "sec": "Class A Common Stock", "sh": 100, "px": 10.0,
           "val": 1000, "ad": "A", "af": 1000, "di": "D", "der": 0,
           "under": "", "exp": ""}
    row.update(kw)
    return row


def test_merge():
    existing = [make_row("0001-26-000001", "1", "2", "2026-08-20")]
    new = [make_row("0001-26-000002", "1", "2", "2026-08-22", amend=1)]
    merged = merge_dataset(existing, new)
    check("merge: later amendment supersedes",
          [r["acc"] for r in merged] == ["0001-26-000002"],
          str([r["acc"] for r in merged]))

    existing = [make_row("0001-26-000001", "1", "2", "2026-08-20")]
    new = [make_row("0001-26-000003", "1", "3", "2026-08-21")]
    merged = merge_dataset(existing, new)
    check("merge: different insider kept", len(merged) == 2,
          str([r["acc"] for r in merged]))

    existing = [make_row("0001-26-000001", "1", "2", "2026-08-20")]
    new = [make_row("0001-26-000001", "1", "2", "2026-08-20", sh=999)]
    merged = merge_dataset(existing, new)
    check("merge: re-collect replaces, no dup", len(merged) == 1
          and merged[0]["sh"] == 999, str(merged))

    existing = [make_row("0001-26-000001", "1", "2", "2026-08-20", period="2026-08-18"),
                make_row("0001-26-000004", "1", "2", "2026-08-23", period="2026-08-21")]
    merged = merge_dataset(existing, [])
    check("merge: distinct periods kept", len(merged) == 2,
          str([r["acc"] for r in merged]))

    # same day: amendment beats original
    existing = [make_row("0001-26-000005", "1", "2", "2026-08-20", amend=0)]
    new = [make_row("0001-26-000006", "1", "2", "2026-08-20", amend=1)]
    merged = merge_dataset(existing, new)
    check("merge: same-day amendment wins",
          [r["acc"] for r in merged] == ["0001-26-000006"],
          str([r["acc"] for r in merged]))

    # multi-row filing stays together
    existing = []
    new = [make_row("0001-26-000007", "1", "2", "2026-08-20"),
           make_row("0001-26-000007", "1", "2", "2026-08-20", code="S", side="sell")]
    merged = merge_dataset(existing, new)
    check("merge: multi-row filing intact", len(merged) == 2, str(merged))


def test_roundtrip_json():
    p = parse_ownership(make(non_deriv=PURCHASE_TX, deriv=DERIV_EXERCISE_TX))
    blob = json.dumps(p, ensure_ascii=False, separators=(",", ":"))
    back = json.loads(blob)
    vals = sorted((t["code"] for t in back["trades"]))
    check("roundtrip: json stable", vals == ["M", "P"], str(vals))


if __name__ == "__main__":
    print("== CEOTrades collector self-test ==")
    test_real_shape_filing()
    test_sale_fractional()
    test_no_price()
    test_malformed()
    test_form5_flags_titles()
    test_multiple_owners()
    test_unicode_whitespace()
    test_master_idx()
    test_submission_extraction()
    test_merge()
    test_roundtrip_json()
    print()
    if FAILURES:
        print(f"SELF-TEST FAILED: {len(FAILURES)} failure(s): {FAILURES}")
        sys.exit(1)
    print("SELF-TEST PASSED: all checks green.")
