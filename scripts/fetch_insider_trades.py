#!/usr/bin/env python3
"""Fetch, parse, and normalize Form 4 insider-trade filings from SEC EDGAR.

Source of truth
---------------
All data comes from SEC EDGAR:
  * Filing enumeration: EDGAR full-text search (efts.sec.gov) -- every Form 4
    filed in the requested window. Falls back to the EDGAR daily index.
  * Transaction data: the machine-readable ownershipDocument XML inside each
    filing's primary document on www.sec.gov/Archives/edgar/data/...

Every file written to --out is JSON derived only from those sources; the run
manifest records exactly which endpoints and dates were used so every number
on the dashboard can be traced back to SEC filings.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from seclib import (  # noqa: E402
    ACCESSION_RE,
    CIK_RE,
    DAILY_INDEX_TMPL,
    FetchError,
    NotFoundError,
    ParseError,
    cik_nodash,
    extract_ownership_xml_from_submission,
    fetch_json,
    fetch_text,
    filing_index_url,
    filing_xml_url,
    hit_to_filing,
    parse_ownership_xml,
    qtr_for_date,
    search_form4,
)

OUT_DEFAULT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
)


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------

def enumerate_day_fts(day: dt.date, log) -> tuple[list[dict], dict]:
    """All Form 4 accession numbers filed on `day`, via EDGAR full-text search."""
    filings: dict = {}
    first = None
    from_ = 0
    page_size = 100
    while True:
        result = search_form4(day, from_=from_, size=page_size)
        if first is None:
            first = result
        hits = result.get("hits", {}) or {}
        items = hits.get("hits") or []
        if not items:
            break
        for item in items:
            filing = hit_to_filing(item)
            if filing["accession"]:
                filings[filing["accession"]] = filing
        if len(items) < page_size:
            break
        from_ += page_size
        if from_ > 100_000:
            log(f"  {day}: stopped paging after {from_} results")
            break

    total_reported = (first or {}).get("hits", {}).get("total", {}).get("value")
    status = "ok"
    if total_reported is not None and len(filings) != total_reported:
        status = f"mismatch(index={total_reported},collected={len(filings)})"
    return list(filings.values()), {
        "date": day.isoformat(),
        "source": "efts-full-text-search",
        "reported_total": total_reported,
        "collected": len(filings),
        "status": status,
    }


def enumerate_day_daily_index(day: dt.date, log) -> tuple[list[dict], dict]:
    """Fallback: parse the EDGAR daily index file for Form 4s on `day`."""
    url = DAILY_INDEX_TMPL.format(
        year=day.year, qtr=qtr_for_date(day), date=day.strftime("%Y%m%d")
    )
    try:
        text = fetch_text(url)
    except NotFoundError:
        return [], {"date": day.isoformat(), "source": "daily-index",
                    "reported_total": 0, "collected": 0, "status": "no-file"}
    filings: dict = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "Form Type")):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 6:
            continue
        form, cik, name, filed, accession, filename = parts[:6]
        if form.strip() != "4":
            continue
        filings[accession] = {
            "accession": accession,
            "filing_date": filed,
            "period_end": "",
            "owner_cik": "",
            "owner_name": "",
            "issuer_cik": cik,
            "issuer_name": name,
            "sic": None,
            "xml_filename": filename,
        }
    return list(filings.values()), {
        "date": day.isoformat(),
        "source": "daily-index",
        "reported_total": len(filings),
        "collected": len(filings),
        "status": "ok",
    }


# ---------------------------------------------------------------------------
# Download + parse
# ---------------------------------------------------------------------------

def download_and_parse(filing: dict) -> list:
    """Fetch one filing's XML and return normalised trade records.

    Returns (records, meta) where meta describes the outcome so failures are
    visible in the run report rather than silently dropped. Raises only for
    programming errors; network/parse problems are reported.
    """
    accession = filing["accession"]
    base_meta = {
        "accession": accession,
        "cik": filing.get("issuer_cik", ""),
        "issuer": filing.get("issuer_name", ""),
    }
    if not ACCESSION_RE.fullmatch(accession):
        return [], {**base_meta, "outcome": "invalid-accession",
                    "error": f"bad accession {accession!r}"}

    issuer_cik = filing["issuer_cik"]
    filename = filing["xml_filename"]
    xml_text = None
    try:
        if filename:
            url = filing_xml_url(issuer_cik, accession, filename)
            xml_text = fetch_text(url)
        if xml_text is None or "<ownershipDocument" not in xml_text:
            # Fallback: combined submission .txt (verified to embed the XML).
            txt_url = f"{filing_index_url(issuer_cik, accession)}".replace(
                f"{accession}-index.htm", f"{accession}.txt")
            xml_text = extract_ownership_xml_from_submission(fetch_text(txt_url))
        doc = parse_ownership_xml(xml_text)
    except (FetchError, ParseError, NotFoundError) as exc:
        return [], {**base_meta, "outcome": "error",
                    "error": f"{type(exc).__name__}: {exc}"}

    issuer = doc["issuer"]
    if not CIK_RE.fullmatch(issuer["cik"]):
        return [], {**base_meta, "outcome": "error",
                    "error": f"issuer CIK {issuer['cik']!r} not 10 digits"}

    owner = doc["owner"]
    rel = doc["relationship"]
    company = {
        "cik": issuer["cik"],
        "name": issuer["name"],
        "ticker": issuer["ticker"],
        "sic": filing["sic"],
    }
    owner_rec = {
        "cik": owner["cik"],
        "name": owner["name"],
        "director": rel.get("director", False),
        "officer": rel.get("officer", False),
        "ten_percent_owner": rel.get("ten_percent_owner", False),
        "other": rel.get("other", False),
        "officer_title": rel.get("officer_title", ""),
        "other_description": rel.get("other_description", ""),
    }
    filing_url = filing_index_url(issuer["cik"], accession)
    records = []
    for raw in doc["non_derivative"] + doc["derivative"]:
        shares = raw.get("shares")
        price = raw.get("price_per_share")
        value = round(shares * price, 2) if shares is not None and price is not None else None
        rec = {
            "id": f"{accession}#{len(records)}",
            "accession": accession,
            "filing_url": filing_url,
            "filing_date": filing["filing_date"] or doc["period_of_report"],
            "period_end": doc["period_of_report"],
            "kind": raw["kind"],
            "company": company,
            "owner": owner_rec,
            "plan_10b5_1": doc["10b5-1_plan"],
            "code": raw["code"] or "",
            "date": raw["date"],
            "acquired_disposed": raw["acquired_disposed"] or "",
            "shares": shares,
            "price_per_share": price,
            "value": value,
            "shares_owned_after": raw.get("shares_owned_after"),
            "direct_indirect": raw.get("direct_indirect") or "",
            "security_title": raw.get("security_title") or "",
            "equity_swap": raw.get("equity_swap", False),
            "timeliness": raw.get("timeliness") or "",
            "deemed_execution_date": raw.get("deemed_execution_date"),
            "underlying_title": raw.get("underlying_title") or "",
            "underlying_shares": raw.get("underlying_shares"),
            "underlying_price": raw.get("underlying_price"),
            "conversion_price": raw.get("conversion_price"),
            "exercise_date": raw.get("exercise_date"),
            "expiration_date": raw.get("expiration_date"),
            "footnotes": raw.get("footnotes", []),
        }
        records.append(rec)
    return records, {
        "accession": accession,
        "outcome": "parsed",
        "trades": len(records),
        "holdings_only": len(records) == 0,
    }


# ---------------------------------------------------------------------------
# Aggregation & output
# ---------------------------------------------------------------------------

def aggregate(records, filings, companies, parsed_count):
    """Compute the site's summary stats directly from the normalized records."""
    non_deriv = [r for r in records if r["kind"] == "non-derivative"]
    deriv = [r for r in records if r["kind"] == "derivative"]

    def money(rows):
        return round(sum(r["value"] or 0.0 for r in rows), 2)

    shares = {
        "all": sum(r["shares"] or 0.0 for r in records),
        "non_derivative": sum(r["shares"] or 0.0 for r in non_deriv),
        "derivative": sum(r["shares"] or 0.0 for r in deriv),
    }
    values = {
        "all": money(non_deriv),
        "buy": money([r for r in non_deriv if r["acquired_disposed"] == "A"]),
        "sell": money([r for r in non_deriv if r["acquired_disposed"] == "D"]),
    }

    by_date: dict = {}
    for r in records:
        d = r["filing_date"] or "unknown"
        slot = by_date.setdefault(d, {"date": d, "filings": set(), "trades": 0,
                                       "shares": 0.0, "value": 0.0})
        slot["filings"].add(r["accession"])
        slot["trades"] += 1
        slot["shares"] += r["shares"] or 0.0
        if r["kind"] == "non-derivative":
            slot["value"] += r["value"] or 0.0
    daily = []
    for d in sorted(by_date):
        slot = by_date[d]
        daily.append({"date": d, "filings": len(slot["filings"]),
                      "trades": slot["trades"],
                      "shares": round(slot["shares"], 2),
                      "value": round(slot["value"], 2)})

    by_type: dict = {}
    for r in records:
        code = r["code"] or "(none)"
        slot = by_type.setdefault(code, {"code": code, "count": 0, "shares": 0.0,
                                         "value": 0.0, "kinds": set()})
        slot["count"] += 1
        slot["shares"] += r["shares"] or 0.0
        if r["kind"] == "non-derivative":
            slot["value"] += r["value"] or 0.0
        slot["kinds"].add(r["kind"])
    by_type_out = []
    for slot in by_type.values():
        slot["kinds"] = sorted(slot["kinds"])
        slot["shares"] = round(slot["shares"], 2)
        slot["value"] = round(slot["value"], 2)
        by_type_out.append(slot)
    by_type_out.sort(key=lambda s: (-s["count"], s["code"]))

    def role_flags(r):
        return r["owner"]

    roles = {"officer": 0, "director": 0, "ten_percent_owner": 0, "other": 0}
    titles: dict = {}
    for r in records:
        o = role_flags(r)
        if o["officer"]:
            roles["officer"] += 1
        if o["director"]:
            roles["director"] += 1
        if o["ten_percent_owner"]:
            roles["ten_percent_owner"] += 1
        if o["other"]:
            roles["other"] += 1
        if o["officer_title"]:
            titles[o["officer_title"]] = titles.get(o["officer_title"], 0) + 1
    by_role = [{"role": k, "trades": v} for k, v in roles.items()] + [
        {"role": "multiple", "trades": sum(
            1 for r in records
            if sum(bool(x) for x in (r["owner"]["director"],
                                     r["owner"]["officer"],
                                     r["owner"]["ten_percent_owner"],
                                     r["owner"]["other"])) > 1)}]
    by_role.sort(key=lambda s: (-s["trades"], s["role"]))
    top_titles = sorted(titles.items(), key=lambda kv: (-kv[1], kv[0]))[:15]

    by_company: dict = {}
    for r in records:
        cik = r["company"]["cik"]
        slot = by_company.setdefault(cik, {
            "cik": cik, "name": r["company"]["name"], "ticker": r["company"]["ticker"],
            "sic": r["company"]["sic"], "trades": 0, "filings": set(),
            "shares": 0.0, "value": 0.0,
        })
        slot["trades"] += 1
        slot["filings"].add(r["accession"])
        slot["shares"] += r["shares"] or 0.0
        if r["kind"] == "non-derivative":
            slot["value"] += r["value"] or 0.0
    top_companies = []
    for slot in by_company.values():
        slot["filings"] = len(slot["filings"])
        slot["shares"] = round(slot["shares"], 2)
        slot["value"] = round(slot["value"], 2)
        top_companies.append(slot)
    top_companies.sort(key=lambda s: (-s["value"], -s["trades"], s["name"]))
    top_companies_names = [c["name"] for c in top_companies[:10]]

    by_owner: dict = {}
    for r in records:
        key = (r["owner"]["cik"], r["owner"]["name"])
        slot = by_owner.setdefault(key, {"cik": r["owner"]["cik"],
                                         "name": r["owner"]["name"],
                                         "trades": 0, "value": 0.0})
        slot["trades"] += 1
        if r["kind"] == "non-derivative":
            slot["value"] += r["value"] or 0.0
    top_owners = []
    for slot in by_owner.values():
        slot["value"] = round(slot["value"], 2)
        top_owners.append(slot)
    top_owners.sort(key=lambda s: (-s["value"], -s["trades"], s["name"]))
    top_owners = top_owners[:10]

    by_sic: dict = {}
    for r in records:
        sic = r["company"]["sic"] or "unknown"
        slot = by_sic.setdefault(sic, {"sic": sic, "trades": 0, "companies": set(),
                                       "value": 0.0})
        slot["trades"] += 1
        slot["companies"].add(r["company"]["cik"])
        if r["kind"] == "non-derivative":
            slot["value"] += r["value"] or 0.0
    sectors = []
    for slot in by_sic.values():
        slot["companies"] = len(slot["companies"])
        slot["value"] = round(slot["value"], 2)
        sectors.append(slot)
    sectors.sort(key=lambda s: (-s["trades"], s["sic"]))

    return {
        "records": {
            "total": len(records),
            "non_derivative": len(non_deriv),
            "derivative": len(deriv),
            "with_10b5_1_plan": sum(1 for r in records if r["plan_10b5_1"]),
            "with_footnotes": sum(1 for r in records if r["footnotes"]),
            "equity_swap": sum(1 for r in records if r["equity_swap"]),
        },
        "filings": {
            "indexed": len(filings),
            "parsed": parsed_count,
            "with_trades": len({r["accession"] for r in records}),
            "companies": len(companies),
        },
        "shares": shares,
        "value": values,
        "daily": daily,
        "by_type": by_type_out,
        "by_role": by_role,
        "top_titles": [{"title": t, "trades": n} for t, n in top_titles],
        "top_companies": top_companies[:10],
        "top_owners": top_owners[:10],
        "sectors": sectors,
        "recent": sorted(records, key=lambda r: (r["filing_date"] or "", r["accession"]),
                         reverse=True)[:20],
    }


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7,
                        help="number of calendar days to fetch (default 7)")
    parser.add_argument("--end", type=str, default="",
                        help="last day of window, YYYY-MM-DD (default: yesterday, UTC)")
    parser.add_argument("--out", type=str, default=OUT_DEFAULT,
                        help="output directory for JSON files")
    parser.add_argument("--limit", type=int, default=0,
                        help="debug: only process the first N filings")
    parser.add_argument("--enumerator", choices=("fts", "daily"), default="fts",
                        help="which filing index to use (default fts)")
    parser.add_argument("--workers", type=int, default=4,
                        help="parallel downloads (default 4)")
    parser.add_argument("--sleep", type=float, default=0.25,
                        help="minimum seconds between request starts")
    args = parser.parse_args(argv)

    log = lambda msg: print(msg, flush=True)  # noqa: E731

    if args.end:
        end = dt.date.fromisoformat(args.end)
    else:
        end = dt.date.today() - dt.timedelta(days=1)
    days = [end - dt.timedelta(days=i) for i in range(args.days - 1, -1, -1)]
    log(f"Window: {days[0]} .. {days[-1]} ({len(days)} days)")

    # --- 1. Enumerate -----------------------------------------------------
    filings: dict = {}
    per_day = []
    for day in days:
        if args.enumerator == "fts":
            hits, meta = enumerate_day_fts(day, log)
        else:
            hits, meta = enumerate_day_daily_index(day, log)
        per_day.append(meta)
        for f in hits:
            filings[f["accession"]] = f
        log(f"  {day}: {meta['collected']} Form 4 filings "
            f"({meta['status']})")
    filing_list = sorted(filings.values(), key=lambda f: f["filing_date"])
    log(f"Total unique filings: {len(filing_list)}")

    if args.limit:
        filing_list = filing_list[: args.limit]
        log(f"DEBUG --limit: processing first {len(filing_list)} filings")

    # --- 2. Download + parse ---------------------------------------------
    records: list = []
    outcomes: list = []
    parsed_accessions: set = set()
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for recs, meta in pool.map(download_and_parse, filing_list):
            outcomes.append(meta)
            if meta["outcome"] == "parsed":
                parsed_accessions.add(meta["accession"])
            records.extend(recs)
    log(f"Parsed {len(parsed_accessions)}/{len(outcomes)} filings in "
        f"{time.time() - t0:.0f}s, {len(records)} transactions")

    # --- 3. Companies -----------------------------------------------------
    companies: dict = {}
    for f in filing_list:
        cik = f["issuer_cik"]
        if not companies.get(cik):
            companies[cik] = {"cik": cik, "name": f["issuer_name"], "ticker": None,
                              "sic": f.get("sic")}
    for r in records:
        c = companies.setdefault(r["company"]["cik"], {
            "cik": r["company"]["cik"], "name": r["company"]["name"],
            "ticker": r["company"]["ticker"], "sic": r["company"]["sic"]})
        if r["company"]["ticker"]:
            c["ticker"] = r["company"]["ticker"]
    for c in companies.values():
        rows = [r for r in records if r["company"]["cik"] == c["cik"]]
        c["trades"] = len(rows)
        c["value"] = round(sum(
            r["value"] or 0.0 for r in rows if r["kind"] == "non-derivative"), 2)
        c["filings"] = len({r["accession"] for r in rows})

    # SIC descriptions (official SEC list, fetched by build_sic_map.py)
    sic_path = os.path.join(args.out, "sic_codes.json")
    sic_map = {}
    if os.path.exists(sic_path):
        with open(sic_path, encoding="utf-8") as fh:
            sic_map = json.load(fh).get("codes", {})
    for c in companies.values():
        entry = sic_map.get(str(c["sic"])) if c["sic"] else None
        c["sic_desc"] = (entry or {}).get("title") if entry else None

    # --- 4. Summary -------------------------------------------------------
    summary = aggregate(records, filing_list, companies, len(parsed_accessions))
    errors = [o for o in outcomes if o["outcome"] != "parsed"]

    os.makedirs(args.out, exist_ok=True)
    write_json(os.path.join(args.out, "trades.json"), records)
    write_json(os.path.join(args.out, "companies.json"),
               sorted(companies.values(), key=lambda c: -c["trades"]))
    write_json(os.path.join(args.out, "summary.json"), summary)
    write_json(os.path.join(args.out, "errors.json"), errors)
    manifest = {
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window": {"start": days[0].isoformat(), "end": days[-1].isoformat(),
                   "days": len(days)},
        "enumerator": args.enumerator,
        "endpoint_enumeration": "https://efts.sec.gov/LATEST/search-index"
                                if args.enumerator == "fts"
                                else "https://www.sec.gov/Archives/edgar/daily-index/",
        "endpoint_filings": "https://www.sec.gov/Archives/edgar/data/",
        "per_day": per_day,
        "filings_indexed": len(filing_list),
        "filings_parsed": summary["filings"]["parsed"],
        "filings_with_trades": summary["filings"]["with_trades"],
        "transactions": summary["records"]["total"],
        "companies": summary["filings"]["companies"],
        "errors": len(errors),
        "notes": [
            "All figures are derived from the SEC filings listed in errors.json"
            " and linked from each trade record's filing_url.",
            "value = shares x price_per_share and is only computed when both"
            " fields are present in the filing.",
        ],
    }
    write_json(os.path.join(args.out, "manifest.json"), manifest)

    log("--- run summary ---")
    log(json.dumps({
        "filings_indexed": manifest["filings_indexed"],
        "filings_parsed": manifest["filings_parsed"],
        "transactions": manifest["transactions"],
        "companies": manifest["companies"],
        "errors": len(errors),
    }, indent=2))
    if errors:
        log(f"{len(errors)} filings could not be parsed; see errors.json")
        for o in errors[:10]:
            log(f"  {o['accession']}: {o['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
