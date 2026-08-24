"""Shared, dependency-free helpers for the CEOTrades insider-trade pipeline.

Every helper in this module talks to public SEC EDGAR endpoints and returns
only data that the SEC actually published. Values that cannot be parsed are
returned as None and recorded in the per-run error log -- nothing is
synthesized or guessed.

Endpoints used
--------------
* https://efts.sec.gov/LATEST/search-index        (EDGAR full-text search)
* https://www.sec.gov/Archives/edgar/data/...     (raw filings & indexes)
* https://www.sec.gov/search-filings/standard-industrial-classification-sic-code-list
"""

from __future__ import annotations

import datetime as dt
import gzip
import html
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# SEC asks automated clients to identify themselves. Override in CI with a
# project-specific contact if desired.
SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT",
    "CEOTrades insider-trade research dashboard "
    "(https://github.com/karagemop466-tech/CEOTrades)",
)

ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
FTS_ENDPOINT = "https://efts.sec.gov/LATEST/search-index"
SUBMISSIONS_ENDPOINT = "https://data.sec.gov/submissions/CIK{cik}.json"
DAILY_INDEX_TMPL = (
    "https://www.sec.gov/Archives/edgar/daily-index/{year}/{qtr}/form.4.{date}.idx"
)
SIC_LIST_URL = (
    "https://www.sec.gov/search-filings/"
    "standard-industrial-classification-sic-code-list"
)
FORM4_PDF_URL = "https://www.sec.gov/files/form4.pdf"

ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
CIK_RE = re.compile(r"^\d{10}$")

# ---------------------------------------------------------------------------
# HTTP (stdlib only, polite rate limiting, gzip, retries)
# ---------------------------------------------------------------------------

class FetchError(RuntimeError):
    """Raised when a request ultimately fails after retries."""


class RetryableError(FetchError):
    """Transient network/HTTP failure worth retrying."""


class NotFoundError(FetchError):
    """HTTP 404 -- the resource does not exist (not retried)."""


class RateLimiter:
    """Minimal global rate limiter: at most one request start per interval."""

    def __init__(self, min_interval: float = 0.25):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next_allowed - now
            if delay > 0:
                time.sleep(delay)
            self._next_allowed = max(now, self._next_allowed) + self.min_interval


RATE_LIMITER = RateLimiter()


def http_get(url: str, timeout: float = 30.0, tries: int = 4,
             accept: str | None = None) -> bytes:
    """GET a URL and return raw bytes.

    Retries transient failures with exponential backoff. Raises
    NotFoundError for HTTP 404 and FetchError for anything else that
    does not succeed after `tries` attempts.
    """
    last_error: Exception | None = None
    for attempt in range(tries):
        RATE_LIMITER.wait()
        req = urllib.request.Request(url)
        req.add_header("User-Agent", SEC_USER_AGENT)
        req.add_header("Accept-Encoding", "gzip")
        if accept:
            req.add_header("Accept", accept)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                return raw
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NotFoundError(f"404 {url}") from exc
            if 500 <= exc.code < 600:
                last_error = RetryableError(f"HTTP {exc.code} for {url}")
            else:
                raise FetchError(f"HTTP {exc.code} for {url}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError,
                OSError) as exc:
            last_error = RetryableError(f"{type(exc).__name__}: {exc} for {url}")
        if attempt < tries - 1:
            time.sleep(min(2.0 ** attempt, 8.0))
    raise FetchError(str(last_error) if last_error else f"failed: {url}")


def fetch_text(url: str, **kw) -> str:
    return http_get(url, **kw).decode("utf-8", errors="replace")


def fetch_json(url: str, **kw):
    return json.loads(fetch_text(url, **kw))


# ---------------------------------------------------------------------------
# Filing URL helpers
# ---------------------------------------------------------------------------

def accession_nodash(accession: str) -> str:
    return accession.replace("-", "")


def cik_nodash(cik: str) -> str:
    """CIK as used in archive paths (no leading zeroes)."""
    return str(int(cik))


def filing_index_url(cik: str, accession: str) -> str:
    return (f"{ARCHIVES_BASE}/{cik_nodash(cik)}/{accession_nodash(accession)}/"
            f"{accession}-index.htm")


def filing_xml_url(cik: str, accession: str, filename: str) -> str:
    return (f"{ARCHIVES_BASE}/{cik_nodash(cik)}/{accession_nodash(accession)}/"
            f"{filename}")


def qtr_for_date(date: dt.date) -> str:
    q = (date.month - 1) // 3 + 1
    return f"QTR{q}"


# ---------------------------------------------------------------------------
# XML helpers (namespace agnostic)
# ---------------------------------------------------------------------------

def lname(tag: str) -> str:
    """Local element name, ignoring any XML namespace."""
    return tag.rsplit("}", 1)[-1]


def children(el, name: str) -> list:
    return [c for c in el if lname(c.tag) == name]


def first_child(el, name: str):
    for c in el:
        if lname(c.tag) == name:
            return c
    return None


def text_of(el) -> str:
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


def num(text: str | None):
    """Parse a decimal from a filing value; return None if absent/blank."""
    if text is None:
        return None
    t = text.strip().replace(",", "").replace("$", "").strip()
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def boolish(text: str | None) -> bool:
    return (text or "").strip().lower() in {"true", "1", "yes", "t"}


def parse_date(text: str | None) -> str | None:
    if not text:
        return None
    t = text.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
        return t
    return None


def strip_tags(raw: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()


# ---------------------------------------------------------------------------
# Form 4 parsing
# ---------------------------------------------------------------------------

class ParseError(ValueError):
    pass


def _parse_footnotes(root) -> dict:
    notes: dict = {}
    fn = first_child(root, "footnotes")
    if fn is not None:
        for note in children(fn, "footnote"):
            nid = note.attrib.get("id")
            if nid:
                notes[nid] = text_of(note)
    return notes


def _parse_relationship(owner_el):
    rel = first_child(owner_el, "reportingOwnerRelationship")
    if rel is None:
        return {}
    return {
        "director": boolish(text_of(first_child(rel, "isDirector"))),
        "officer": boolish(text_of(first_child(rel, "isOfficer"))),
        "ten_percent_owner": boolish(text_of(first_child(rel, "isTenPercentOwner"))),
        "other": boolish(text_of(first_child(rel, "isOther"))),
        "officer_title": text_of(first_child(rel, "officerTitle")),
        "other_description": text_of(first_child(rel, "otherText")),
    }


def _footnote_ids(tx_el) -> list:
    ids = []
    for el in tx_el.iter():
        if lname(el.tag) == "footnoteId":
            nid = el.attrib.get("id")
            if nid and nid not in ids:
                ids.append(nid)
    return ids


def _parse_transaction(tx_el, kind: str, footnotes: dict) -> dict:
    coding = first_child(tx_el, "transactionCoding") or tx_el
    amounts = first_child(tx_el, "transactionAmounts") or tx_el
    post = first_child(tx_el, "postTransactionAmounts") or {}
    nature = first_child(tx_el, "ownershipNature") or {}

    rec = {
        "kind": kind,
        "security_title": text_of(first_child(tx_el, "securityTitle")),
        "date": parse_date(
            text_of(first_child(first_child(tx_el, "transactionDate") or {}, "value"))
        ),
        "deemed_execution_date": parse_date(
            text_of(first_child(first_child(tx_el, "deemedExecutionDate") or {}, "value"))
        ),
        "code": text_of(first_child(coding, "transactionCode")),
        "form_type": text_of(first_child(coding, "transactionFormType")),
        "timeliness": text_of(first_child(coding, "transactionTimeliness")),
        "equity_swap": boolish(text_of(first_child(coding, "equitySwapInvolved"))),
        "shares": num(text_of(first_child(amounts, "transactionShares"))),
        "price_per_share": num(text_of(first_child(amounts, "transactionPricePerShare"))),
        "acquired_disposed": text_of(
            first_child(amounts, "transactionAcquiredDisposedCode")
        ),
        "shares_owned_after": num(
            text_of(first_child(post, "sharesOwnedFollowingTransaction"))
        ),
        "direct_indirect": text_of(
            first_child(first_child(nature, "directOrIndirectOwnership") or {}, "value")
        ),
        "footnotes": [footnotes[nid] for nid in _footnote_ids(tx_el)
                      if nid in footnotes],
    }

    if kind == "derivative":
        rec.update({
            "conversion_price": num(
                text_of(first_child(first_child(tx_el, "conversionOrExercisePrice") or {},
                                    "value"))
            ),
            "exercise_date": parse_date(
                text_of(first_child(tx_el, "exerciseDate"))
            ),
            "expiration_date": parse_date(
                text_of(first_child(tx_el, "expirationDate"))
            ),
            "underlying_title": text_of(
                first_child(first_child(tx_el, "underlyingSecurity") or {},
                            "underlyingSecurityTitle")
            ),
            "underlying_shares": num(
                text_of(first_child(first_child(tx_el, "underlyingSecurity") or {},
                                    "underlyingSecurityShares"))
            ),
            "underlying_price": num(
                text_of(first_child(first_child(tx_el, "underlyingSecurity") or {},
                                    "underlyingSecurityPrice"))
            ),
        })
    return rec


def parse_ownership_xml(xml_text: str) -> dict:
    """Parse a Form 4 ownershipDocument and return normalized raw fields.

    Raises ParseError when the document is not a Form 4 ownership document.
    """
    text = xml_text.lstrip("\ufeff")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ParseError(f"XML parse error: {exc}") from exc
    if lname(root.tag) != "ownershipDocument":
        raise ParseError(f"unexpected root element <{lname(root.tag)}>")

    issuer = first_child(root, "issuer") or {}
    owner = first_child(root, "reportingOwner") or {}
    owner_id = first_child(owner, "reportingOwnerId") or {}
    footnotes = _parse_footnotes(root)

    doc = {
        "document_type": text_of(first_child(root, "documentType")),
        "period_of_report": parse_date(text_of(first_child(root, "periodOfReport"))),
        "10b5-1_plan": boolish(text_of(first_child(root, "aff10b5One"))),
        "issuer": {
            "cik": text_of(first_child(issuer, "issuerCik")),
            "name": text_of(first_child(issuer, "issuerName")),
            "ticker": text_of(first_child(issuer, "issuerTradingSymbol")),
            "foreign_ticker": text_of(
                first_child(issuer, "issuerForeignTradingSymbol")
            ),
        },
        "owner": {
            "cik": text_of(first_child(owner_id, "rptOwnerCik")),
            "name": text_of(first_child(owner_id, "rptOwnerName")),
        },
        "relationship": _parse_relationship(owner),
        "non_derivative": [
            _parse_transaction(tx, "non-derivative", footnotes)
            for tx in children(first_child(root, "nonDerivativeTable") or {},
                               "nonDerivativeTransaction")
        ],
        "derivative": [
            _parse_transaction(tx, "derivative", footnotes)
            for tx in children(first_child(root, "derivativeTable") or {},
                               "derivativeTransaction")
        ],
        "has_non_derivative_holdings": bool(
            children(first_child(root, "nonDerivativeTable") or {},
                     "nonDerivativeHolding")
        ),
        "has_derivative_holdings": bool(
            children(first_child(root, "derivativeTable") or {},
                     "derivativeHolding")
        ),
    }

    if doc["document_type"] not in (None, "", "4"):
        raise ParseError(f"document type {doc['document_type']!r} is not Form 4")
    return doc


def extract_ownership_xml_from_submission(text: str) -> str:
    """Pull the ownershipDocument XML out of a combined submission .txt."""
    match = re.search(
        r"<ownershipDocument[\s>].*?</ownershipDocument>", text, re.S
    )
    if not match:
        raise ParseError("no <ownershipDocument> found in submission text")
    return match.group(0)


# ---------------------------------------------------------------------------
# EDGAR full-text search enumeration of Form 4 filings
# ---------------------------------------------------------------------------

def search_form4(day: dt.date, from_: int = 0, size: int = 100) -> dict:
    """One page of EDGAR full-text search results for Form 4s filed on `day`."""
    params = {
        "q": "",
        "forms": "4",
        "dateRange": "custom",
        "startdt": day.isoformat(),
        "enddt": day.isoformat(),
        "from": str(from_),
        "size": str(size),
    }
    query = urllib.parse.urlencode(params)
    return fetch_json(f"{FTS_ENDPOINT}?{query}")


def hit_to_filing(hit: dict) -> dict:
    """Normalize one full-text-search hit into a filing descriptor."""
    src = hit.get("_source", {})
    doc_id = hit.get("_id", "")
    # _id looks like "0001140361-26-033928:form4.xml"
    filename = doc_id.split(":", 1)[1] if ":" in doc_id else ""
    ciks = src.get("ciks") or []
    names = src.get("display_names") or []
    return {
        "accession": src.get("adsh", ""),
        "filing_date": src.get("file_date", ""),
        "period_end": src.get("period_ending", ""),
        "owner_cik": ciks[0] if ciks else "",
        "owner_name": _clean_entity_name(names[0]) if names else "",
        "issuer_cik": ciks[1] if len(ciks) > 1 else "",
        "issuer_name": _clean_entity_name(names[1]) if len(names) > 1 else "",
        "sic": (src.get("sics") or [None])[0],
        "xml_filename": filename,
    }


def _clean_entity_name(name: str) -> str:
    """Strip the ' (CIK 0001234567)' suffix EDGAR appends to names."""
    return re.sub(r"\s*\(CIK\s+\d+\)\s*$", "", name or "").strip()
