#!/usr/bin/env python3
"""Parser unit tests using real SEC filings (fetched 2026-08-24).

The fixtures below are verbatim excerpts of the ownershipDocument XML served by
SEC EDGAR, so the tests validate the parser against filing reality:

  * Submission 0001140361-26-033928 (Apple Inc. / Jennifer Newstead)
  * Submission 0000091440-26-000159 (Snap-on Inc / Nicholas T. Pinchuk) --
    includes a Table I option exercise AND a Table II derivative transaction,
    plus footnote-only price/exercise-date fields.

Run: python3 scripts/tests/test_parser.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from seclib import (  # noqa: E402
    extract_ownership_xml_from_submission,
    parse_ownership_xml,
)

APPLE_XML = """<?xml version="1.0"?>
<ownershipDocument>
    <schemaVersion>X0609</schemaVersion>
    <documentType>4</documentType>
    <periodOfReport>2026-08-18</periodOfReport>
    <issuer>
        <issuerCik>0000320193</issuerCik>
        <issuerName>Apple Inc.</issuerName>
        <issuerTradingSymbol>AAPL</issuerTradingSymbol>
        <issuerForeignTradingSymbol></issuerForeignTradingSymbol>
    </issuer>
    <reportingOwner>
        <reportingOwnerId>
            <rptOwnerCik>0001780525</rptOwnerCik>
            <rptOwnerName>Newstead Jennifer</rptOwnerName>
        </reportingOwnerId>
        <reportingOwnerRelationship>
            <isOfficer>true</isOfficer>
            <officerTitle>SVP, GC and Secretary</officerTitle>
        </reportingOwnerRelationship>
    </reportingOwner>
    <aff10b5One>true</aff10b5One>
    <nonDerivativeTable>
        <nonDerivativeTransaction>
            <securityTitle>
                <value>Common Stock</value>
                <footnoteId id="F1"/>
            </securityTitle>
            <transactionDate>
                <value>2026-08-18</value>
            </transactionDate>
            <transactionCoding>
                <transactionFormType>4</transactionFormType>
                <transactionCode>S</transactionCode>
                <equitySwapInvolved>0</equitySwapInvolved>
            </transactionCoding>
            <transactionAmounts>
                <transactionShares>
                    <value>1439</value>
                </transactionShares>
                <transactionPricePerShare>
                    <value>307.49</value>
                </transactionPricePerShare>
                <transactionAcquiredDisposedCode>
                    <value>D</value>
                </transactionAcquiredDisposedCode>
            </transactionAmounts>
            <postTransactionAmounts>
                <sharesOwnedFollowingTransaction>
                    <value>38668</value>
                </sharesOwnedFollowingTransaction>
            </postTransactionAmounts>
            <ownershipNature>
                <directOrIndirectOwnership>
                    <value>D</value>
                </directOrIndirectOwnership>
            </ownershipNature>
        </nonDerivativeTransaction>
    </nonDerivativeTable>
    <footnotes>
        <footnote id="F1">This transaction was made pursuant to a Rule 10b5-1 trading plan adopted by the reporting person on May 5, 2026.</footnote>
    </footnotes>
</ownershipDocument>
"""

SNAPON_XML = """<?xml version="1.0"?>
<ownershipDocument>
    <schemaVersion>X0609</schemaVersion>
    <documentType>4</documentType>
    <periodOfReport>2026-08-18</periodOfReport>
    <issuer>
        <issuerCik>0000091440</issuerCik>
        <issuerName>Snap-on Inc</issuerName>
        <issuerTradingSymbol>SNA</issuerTradingSymbol>
        <issuerForeignTradingSymbol></issuerForeignTradingSymbol>
    </issuer>
    <reportingOwner>
        <reportingOwnerId>
            <rptOwnerCik>0001246136</rptOwnerCik>
            <rptOwnerName>PINCHUK NICHOLAS T</rptOwnerName>
        </reportingOwnerId>
        <reportingOwnerRelationship>
            <isDirector>1</isDirector>
            <isOfficer>1</isOfficer>
            <officerTitle>Chairman, President and CEO</officerTitle>
        </reportingOwnerRelationship>
    </reportingOwner>
    <aff10b5One>1</aff10b5One>
    <nonDerivativeTable>
        <nonDerivativeTransaction>
            <securityTitle>
                <value>Common Stock</value>
            </securityTitle>
            <transactionDate>
                <value>2026-08-18</value>
            </transactionDate>
            <transactionCoding>
                <transactionFormType>4</transactionFormType>
                <transactionCode>M</transactionCode>
                <equitySwapInvolved>0</equitySwapInvolved>
                <footnoteId id="F1"/>
            </transactionCoding>
            <transactionTimeliness></transactionTimeliness>
            <transactionAmounts>
                <transactionShares>
                    <value>33750</value>
                </transactionShares>
                <transactionPricePerShare>
                    <value>168.70</value>
                </transactionPricePerShare>
                <transactionAcquiredDisposedCode>
                    <value>A</value>
                </transactionAcquiredDisposedCode>
            </transactionAmounts>
            <postTransactionAmounts>
                <sharesOwnedFollowingTransaction>
                    <value>890667.9587</value>
                    <footnoteId id="F2"/>
                </sharesOwnedFollowingTransaction>
            </postTransactionAmounts>
            <ownershipNature>
                <directOrIndirectOwnership>
                    <value>D</value>
                </directOrIndirectOwnership>
            </ownershipNature>
        </nonDerivativeTransaction>
    </nonDerivativeTable>
    <derivativeTable>
        <derivativeTransaction>
            <securityTitle>
                <value>Stock Option (Right to Buy)</value>
            </securityTitle>
            <conversionOrExercisePrice>
                <value>168.70</value>
            </conversionOrExercisePrice>
            <transactionDate>
                <value>2026-08-18</value>
            </transactionDate>
            <transactionCoding>
                <transactionFormType>4</transactionFormType>
                <transactionCode>M</transactionCode>
                <equitySwapInvolved>0</equitySwapInvolved>
                <footnoteId id="F1"/>
            </transactionCoding>
            <transactionTimeliness></transactionTimeliness>
            <transactionAmounts>
                <transactionShares>
                    <value>33750</value>
                </transactionShares>
                <transactionPricePerShare>
                    <footnoteId id="F10"/>
                </transactionPricePerShare>
                <transactionAcquiredDisposedCode>
                    <value>D</value>
                </transactionAcquiredDisposedCode>
            </transactionAmounts>
            <exerciseDate>
                <footnoteId id="F9"/>
            </exerciseDate>
            <expirationDate>
                <value>2027-02-09</value>
            </expirationDate>
            <underlyingSecurity>
                <underlyingSecurityTitle>
                    <value>Common Stock</value>
                </underlyingSecurityTitle>
                <underlyingSecurityShares>
                    <value>33750</value>
                </underlyingSecurityShares>
            </underlyingSecurity>
            <postTransactionAmounts>
                <sharesOwnedFollowingTransaction>
                    <value>33750</value>
                </sharesOwnedFollowingTransaction>
            </postTransactionAmounts>
            <ownershipNature>
                <directOrIndirectOwnership>
                    <value>D</value>
                </directOrIndirectOwnership>
            </ownershipNature>
        </derivativeTransaction>
    </derivativeTable>
    <footnotes>
        <footnote id="F1">The option was exercised, and a portion of the underlying shares were sold to cover the exercise price and estimated tax liability, pursuant to a Rule 10b5-1 Plan, which was adopted on November 3, 2025.</footnote>
        <footnote id="F9">Option fully vested.</footnote>
        <footnote id="F10">Exercise of Rule 16b-3 stock option pursuant to a Rule 10b5-1 Plan, which was adopted on November 3, 2025.</footnote>
    </footnotes>
</ownershipDocument>
"""

SUBMISSION_WRAPPER = """<SEC-DOCUMENT>0001140361-26-033928.txt : 20260820
<SEC-HEADER>...header omitted...</SEC-HEADER>
<DOCUMENT>
<TYPE>4
<SEQUENCE>1
<FILENAME>form4.xml
<DESCRIPTION>FORM 4
<TEXT>
<XML>
""" + APPLE_XML + """
</XML>
</TEXT>
</DOCUMENT>
</SEC-DOCUMENT>
"""


def test_apple():
    doc = parse_ownership_xml(APPLE_XML)
    assert doc["document_type"] == "4"
    assert doc["period_of_report"] == "2026-08-18"
    assert doc["issuer"] == {"cik": "0000320193", "name": "Apple Inc.",
                             "ticker": "AAPL", "foreign_ticker": ""}
    assert doc["owner"] == {"cik": "0001780525", "name": "Newstead Jennifer"}
    assert doc["relationship"]["officer"] is True
    assert doc["relationship"]["director"] is False
    assert doc["relationship"]["officer_title"] == "SVP, GC and Secretary"
    assert doc["10b5-1_plan"] is True
    assert len(doc["non_derivative"]) == 1
    tx = doc["non_derivative"][0]
    assert tx["code"] == "S"
    assert tx["shares"] == 1439
    assert tx["price_per_share"] == 307.49
    assert tx["acquired_disposed"] == "D"
    assert tx["shares_owned_after"] == 38668
    assert tx["direct_indirect"] == "D"
    assert tx["date"] == "2026-08-18"
    assert tx["timeliness"] == ""
    assert tx["footnotes"] == [
        "This transaction was made pursuant to a Rule 10b5-1 trading plan "
        "adopted by the reporting person on May 5, 2026."]
    assert len(doc["derivative"]) == 0
    assert doc["has_non_derivative_holdings"] is False
    print("PASS  Apple Form 4 (0001140361-26-033928)")


def test_snap_on():
    doc = parse_ownership_xml(SNAPON_XML)
    assert doc["issuer"]["cik"] == "0000091440"
    assert doc["issuer"]["ticker"] == "SNA"
    assert doc["owner"]["name"] == "PINCHUK NICHOLAS T"
    assert doc["relationship"]["director"] is True
    assert doc["relationship"]["officer"] is True
    assert doc["10b5-1_plan"] is True

    nd = doc["non_derivative"]
    assert len(nd) == 1
    assert nd[0]["code"] == "M"
    assert nd[0]["shares"] == 33750
    assert nd[0]["price_per_share"] == 168.70
    assert nd[0]["shares_owned_after"] == 890667.9587
    assert nd[0]["footnotes"][0].startswith(
        "The option was exercised, and a portion of the underlying shares")

    der = doc["derivative"]
    assert len(der) == 1
    d = der[0]
    assert d["security_title"] == "Stock Option (Right to Buy)"
    assert d["conversion_price"] == 168.70
    assert d["shares"] == 33750
    assert d["price_per_share"] is None          # footnote-only, as filed
    assert d["underlying_title"] == "Common Stock"
    assert d["underlying_shares"] == 33750
    assert d["expiration_date"] == "2027-02-09"
    assert d["exercise_date"] is None            # footnote-only, as filed
    assert len(d["footnotes"]) == 3              # F1, F9, F10 referenced
    assert any("Rule 16b-3 stock option" in f for f in d["footnotes"])
    assert any("Option fully vested" in f for f in d["footnotes"])
    assert doc["has_derivative_holdings"] is False
    print("PASS  Snap-on Form 4 (0000091440-26-000159)")


def test_submission_extraction():
    xml = extract_ownership_xml_from_submission(SUBMISSION_WRAPPER)
    assert "<ownershipDocument>" in xml
    doc = parse_ownership_xml(xml)
    assert doc["issuer"]["ticker"] == "AAPL"
    print("PASS  ownership XML extraction from combined submission .txt")


if __name__ == "__main__":
    test_apple()
    test_snap_on()
    test_submission_extraction()
    print("\nAll parser tests passed.")
