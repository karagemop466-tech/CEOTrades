#!/usr/bin/env python3
"""Automated irregularity scanner for CEOTrades.

The scanner flags items for human review from the verified SEC-derived local
store. It is not a legal conclusion and it does not enrich records with guessed
facts. Evidence lines are computed directly from stored row fields and link back
to SEC EDGAR accessions.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import audit as audit_mod  # noqa: E402
import store  # noqa: E402

LARGE_VALUE = 5_000_000.0
CLUSTER_DAYS = 7
CLUSTER_MIN_VALUE = 25_000.0
SYNC_SALE_MIN_VALUE = 1_000_000.0
LARGE_NONCASH_SHARES = 100_000.0
BUY_SELL_OVERLAP_MIN_VALUE = 25_000.0

SEVERITY_ORDER = {"High": 0, "Medium": 1, "Low": 2, "Informational": 3}


def fnum(v):
    return audit_mod.fnum(v)


def parse_day(s):
    return audit_mod.parse_day(s)


def money(x) -> str:
    if x is None:
        return "not reported"
    return f"${x:,.2f}"


def shares(x) -> str:
    if x is None:
        return "not reported"
    if abs(x - int(x)) < 1e-9:
        return f"{int(x):,}"
    return f"{x:,.4f}".rstrip("0").rstrip(".")


def sec_url(row: dict) -> str:
    return audit_mod.edgar_url(row.get("acc") or "", row.get("icik") or "")


def filing_list(rows: list[dict]) -> list[dict]:
    out = {}
    for r in rows:
        acc = r.get("acc") or ""
        if not acc:
            continue
        out[acc] = {
            "acc": acc,
            "icik": r.get("icik") or "",
            "url": sec_url(r),
        }
    return [out[k] for k in sorted(out)]


def evidence_line(r: dict) -> str:
    sh = fnum(r.get("sh"))
    px = fnum(r.get("px"))
    val = fnum(r.get("val"))
    px_txt = "no reported price" if px is None else money(px)
    val_txt = "no reported value" if val is None else money(abs(val))
    der = "Table II derivative" if r.get("der") else "Table I non-derivative"
    return (
        f"{r.get('acc')}: {r.get('fd')} filing; {r.get('td') or 'no transaction date'} transaction; "
        f"{r.get('tk') or 'no ticker'} {r.get('co')}; {r.get('in') or 'unknown insider'}; "
        f"{der}; code {r.get('code') or '?'}; {shares(sh)} shares @ {px_txt}; value {val_txt}."
    )


def base_item(category: str, severity: str, rows: list[dict], summary: str,
              details: str, rule: str, item_id: str = "") -> dict:
    vals = [abs(fnum(r.get("val")) or 0.0) for r in rows]
    shs = [abs(fnum(r.get("sh")) or 0.0) for r in rows]
    fds = sorted({r.get("fd") or "" for r in rows if r.get("fd")})
    tds = sorted({r.get("td") or "" for r in rows if r.get("td")})
    insiders = sorted({r.get("in") or "" for r in rows if r.get("in")})
    tickers = sorted({r.get("tk") or "" for r in rows if r.get("tk")})
    companies = sorted({r.get("co") or "" for r in rows if r.get("co")})
    iciks = sorted({str(r.get("icik") or "") for r in rows if r.get("icik")})
    accessions = sorted({r.get("acc") or "" for r in rows if r.get("acc")})
    return {
        "id": item_id,
        "category": category,
        "severity": severity,
        "tk": ", ".join(tickers[:4]),
        "co": companies[0] if companies else "",
        "icik": iciks[0] if len(iciks) == 1 else "",
        "fd": fds[0] if len(fds) == 1 else (f"{fds[0]} to {fds[-1]}" if fds else ""),
        "td": tds[0] if len(tds) == 1 else (f"{tds[0]} to {tds[-1]}" if tds else ""),
        "accessions": accessions,
        "filings": filing_list(rows),
        "insiders": insiders,
        "total_shares": round(sum(shs), 4),
        "total_value": round(sum(vals), 2),
        "summary": summary,
        "details": details,
        "rule": rule,
        "evidence": [evidence_line(r) for r in rows[:20]],
        "evidence_truncated": len(rows) > 20,
    }


def load_target_rows(data_dir: str, target_year: int) -> list[dict]:
    rows = []
    for r in store.iter_rows(data_dir):
        fd = parse_day(r.get("fd"))
        if fd and fd.year == target_year:
            rows.append(r)
    return rows


def add_data_quality(items: list[dict], audit: dict):
    comp = audit["completeness"]
    if not comp["complete"]:
        items.append({
            "id": "",
            "category": "Data coverage gap",
            "severity": "High",
            "tk": "",
            "co": "Local dataset",
            "icik": "",
            "fd": comp.get("target_observed_from") + " to " + comp.get("target_observed_to") if comp.get("target_observed_from") else "",
            "td": "",
            "accessions": [],
            "filings": [],
            "insiders": [],
            "total_shares": 0,
            "total_value": 0,
            "summary": "The local store does not prove full year-to-date coverage for the requested year.",
            "details": " ".join(comp.get("blockers") or []) or comp.get("note", ""),
            "rule": "Completeness guard: target-year rows should cover the SEC year-to-date filing window before the project claims a full list.",
            "evidence": comp.get("blockers") or [comp.get("note", "")],
            "evidence_truncated": False,
        })

    by_issue = {x["code"]: x["n"] for x in audit["integrity"].get("by_issue", [])}
    examples = audit["integrity"].get("issue_examples", [])
    grouped = defaultdict(list)
    for e in examples:
        grouped[e["code"]].append(e)
    for code, n in sorted(by_issue.items(), key=lambda kv: (-kv[1], kv[0]))[:15]:
        exs = grouped.get(code, [])
        severity = "Medium"
        if any(e.get("severity") == "high" for e in exs):
            severity = "High"
        elif any(e.get("severity") == "low" for e in exs):
            severity = "Low"
        items.append({
            "id": "",
            "category": "Row integrity issue: " + code,
            "severity": severity,
            "tk": ", ".join(sorted({e.get("tk", "") for e in exs if e.get("tk")}))[:40],
            "co": "Mechanical row audit",
            "icik": "",
            "fd": "",
            "td": "",
            "accessions": sorted({e.get("acc", "") for e in exs if e.get("acc")}),
            "filings": [{"acc": e.get("acc"), "icik": e.get("icik"), "url": e.get("source_url")}
                         for e in exs[:20] if e.get("acc")],
            "insiders": sorted({e.get("insider", "") for e in exs if e.get("insider")}),
            "total_shares": 0,
            "total_value": 0,
            "summary": f"{n:,} stored row(s) triggered the {code} mechanical check.",
            "details": "These are data-quality flags generated from stored fields. Review the linked SEC accessions before publishing conclusions.",
            "rule": code,
            "evidence": [e.get("message", "") + (f" ({e.get('acc')})" if e.get("acc") else "") for e in exs[:20]],
            "evidence_truncated": n > 20,
        })


def scan_large_transactions(rows: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for r in rows:
        code = (r.get("code") or "").upper()[:1]
        val = abs(fnum(r.get("val")) or 0.0)
        if code in {"P", "S"} and val >= LARGE_VALUE:
            groups[(r.get("acc"), code)].append(r)
    out = []
    for (_acc, code), rs in groups.items():
        total = sum(abs(fnum(r.get("val")) or 0.0) for r in rs)
        verb = "purchase" if code == "P" else "sale"
        out.append(base_item(
            f"Large disclosed {verb}",
            "Medium" if code == "P" else "Low",
            rs,
            f"SEC code {code} {verb} value totals {money(total)} in one filing.",
            "Large dollar transactions can dominate aggregate flow and should be reviewed at the accession level.",
            f"Aggregate code-{code} value per accession >= {money(LARGE_VALUE)}.",
        ))
    return out


def scan_cluster_buys(rows: list[dict]) -> list[dict]:
    by_issuer = defaultdict(list)
    for r in rows:
        if (r.get("code") or "").upper()[:1] == "P" and not r.get("der"):
            d = parse_day(r.get("td") or r.get("fd"))
            if d:
                by_issuer[r.get("icik") or r.get("tk") or r.get("co")].append((d, r))
    out = []
    for _issuer, pairs in by_issuer.items():
        pairs.sort(key=lambda x: x[0])
        best = []
        best_val = 0.0
        for i, (d0, _r0) in enumerate(pairs):
            win = [r for d, r in pairs if d0 <= d <= d0 + timedelta(days=CLUSTER_DAYS)]
            insiders = {r.get("pcik") or r.get("in") for r in win}
            val = sum(abs(fnum(r.get("val")) or 0.0) for r in win)
            if len(insiders) >= 2 and val >= CLUSTER_MIN_VALUE and val > best_val:
                best = win
                best_val = val
        if best:
            out.append(base_item(
                "Cluster buying",
                "Medium",
                best,
                f"{len({r.get('pcik') or r.get('in') for r in best})} insiders reported code-P purchases within {CLUSTER_DAYS} calendar days; total value {money(best_val)}.",
                "Cluster buying is a screening signal only. The scanner verifies same-issuer timing and reported code-P rows; it does not infer motive.",
                f"Same issuer, non-derivative code P, >=2 distinct reporting owners, <= {CLUSTER_DAYS} calendar days, total reported value >= {money(CLUSTER_MIN_VALUE)}.",
            ))
    return out


def scan_synchronized_sales(rows: list[dict]) -> list[dict]:
    by_key = defaultdict(list)
    for r in rows:
        if (r.get("code") or "").upper()[:1] == "S" and not r.get("der"):
            key = (r.get("icik") or r.get("tk") or r.get("co"), r.get("td") or "")
            by_key[key].append(r)
    out = []
    for (_issuer, td), rs in by_key.items():
        insiders = {r.get("pcik") or r.get("in") for r in rs}
        total = sum(abs(fnum(r.get("val")) or 0.0) for r in rs)
        if td and len(insiders) >= 2 and total >= SYNC_SALE_MIN_VALUE:
            out.append(base_item(
                "Synchronized same-day sales",
                "Medium",
                rs,
                f"{len(insiders)} insiders reported same-day code-S sales totaling {money(total)}.",
                "Same-day sales by multiple insiders may reflect pre-arranged trading plans or ordinary liquidity. Review the SEC filings and footnotes before drawing conclusions.",
                f"Same issuer + same transaction date + non-derivative code S + >=2 insiders + total value >= {money(SYNC_SALE_MIN_VALUE)}.",
            ))
    return out


def scan_exercise_then_sale(rows: list[dict]) -> list[dict]:
    by_acc = defaultdict(list)
    for r in rows:
        by_acc[r.get("acc") or ""].append(r)
    out = []
    for _acc, rs in by_acc.items():
        if not _acc:
            continue
        has_derivative_or_conversion = any(
            r.get("der") or (r.get("code") or "").upper()[:1] in {"M", "C", "X", "O"}
            for r in rs
        )
        sale_rows = [r for r in rs if (r.get("code") or "").upper()[:1] == "S"]
        if has_derivative_or_conversion and sale_rows:
            total = sum(abs(fnum(r.get("val")) or 0.0) for r in sale_rows)
            out.append(base_item(
                "Exercise/conversion with same-filing sale",
                "Low",
                rs,
                f"The filing contains derivative exercise/conversion activity and code-S sales totaling {money(total)}.",
                "This is common for option exercises, RSU vesting, tax planning and Rule 10b5-1 plans. It is flagged so the transaction is not misread as a standalone discretionary sale.",
                "Same accession contains derivative or conversion/exercise code(s) and at least one non-derivative sale row.",
            ))
    return out


def scan_buyer_seller_overlap(rows: list[dict]) -> list[dict]:
    """Flag same-insider same-issuer buy/sell overlap for review.

    This is not inherently suspicious: it may reflect tax planning, trading
    plans, diversification, option activity, or amended filings. It is useful
    because the user explicitly wants to track insiders who buy and also sell.
    """
    by_pair = defaultdict(list)
    for r in rows:
        code = (r.get("code") or "").upper()[:1]
        if code not in {"P", "S"} or r.get("der"):
            continue
        owner = r.get("pcik") or r.get("in") or ""
        issuer = r.get("icik") or r.get("tk") or r.get("co") or ""
        if owner and issuer:
            by_pair[(owner, issuer)].append(r)
    out = []
    for (_owner, _issuer), rs in by_pair.items():
        buys = [r for r in rs if (r.get("code") or "").upper()[:1] == "P"]
        sells = [r for r in rs if (r.get("code") or "").upper()[:1] == "S"]
        if not buys or not sells:
            continue
        buy_v = sum(abs(fnum(r.get("val")) or 0.0) for r in buys)
        sell_v = sum(abs(fnum(r.get("val")) or 0.0) for r in sells)
        if max(buy_v, sell_v) < BUY_SELL_OVERLAP_MIN_VALUE:
            continue
        all_rows = sorted(buys + sells, key=lambda r: (r.get("fd") or "", r.get("td") or "", r.get("acc") or ""))
        out.append(base_item(
            "Same-insider buy/sell overlap",
            "Low",
            all_rows,
            f"Same insider reported code-P purchases totaling {money(buy_v)} and code-S sales totaling {money(sell_v)} for the same issuer.",
            "Buy/sell overlap is a context flag, not a legal conclusion. Review the accessions and footnotes to determine whether sales are planned, tax-related, derivative-linked or discretionary.",
            f"Same reporting owner + same issuer + at least one non-derivative code-P row and one non-derivative code-S row in the target year; max(buy value, sell value) >= {money(BUY_SELL_OVERLAP_MIN_VALUE)}.",
        ))
    return out


def scan_large_noncash(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        code = (r.get("code") or "").upper()[:1]
        val = fnum(r.get("val"))
        sh = abs(fnum(r.get("sh")) or 0.0)
        if code in {"G", "A", "J"} and (val is None or val == 0) and sh >= LARGE_NONCASH_SHARES:
            out.append(base_item(
                "Large non-cash transaction",
                "Low" if code != "G" else "Medium",
                [r],
                f"Code {code} row reports {shares(sh)} shares with no cash value reported.",
                "Non-cash awards, gifts and administrative changes can be large in share count but are not open-market purchases or sales.",
                f"Code in G/A/J, no reported cash value, share count >= {shares(LARGE_NONCASH_SHARES)}.",
            ))
    return out


def scan_paper_price_anomalies(site_data: str) -> list[dict]:
    """Flag paper rows whose Yahoo open is not comparable to the as-filed price."""
    path = os.path.join(site_data, "paper", "positions.json")
    if not os.path.isfile(path):
        return []
    try:
        rows = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for p in rows:
        if p.get("status") != "open":
            continue
        gap = p.get("gap")
        ipx = p.get("insider_px")
        epx = p.get("entry_px")
        if gap is None or ipx is None or epx is None:
            continue
        if abs(float(gap)) < 1.0:
            continue
        fake_row = {
            "acc": p.get("acc") or "", "icik": p.get("icik") or "",
            "fd": p.get("fd") or "", "td": p.get("td") or "",
            "tk": p.get("tk") or "", "co": p.get("co") or "",
            "in": p.get("insider") or "", "code": "P",
            "sh": p.get("insider_sh"), "px": ipx, "val": p.get("insider_val"),
            "sec": p.get("sec") or "", "der": 0,
        }
        out.append(base_item(
            "Paper gap vs as-filed insider price (possible split adjustment)",
            "High",
            [fake_row],
            (f"{p.get('tk')} paper entry open {epx} vs Form 4 VWAP {ipx} "
             f"(gap {float(gap)*100:.1f}%). Yahoo bars are split-adjusted; "
             f"Form 4 prices are as-filed. Review before using gap/ROI."),
            "Do not treat this as the public follower paying a different cash price "
            "on the same share class until the split history is confirmed.",
            "Open paper position with |entry_open / insider_px - 1| >= 100%.",
        ))
    return out


def scan_paper_split_straddle(site_data: str) -> list[dict]:
    """Flag positions whose mark-to-market return is implausibly large for a
    short holding period — the signature of a corporate action (reverse/
    forward split, M&A cash-out) straddled by an internally inconsistent price
    series, OR of an actual takeover event. Either way the headline ROI must
    not be trusted until the corporate action is confirmed line by line.

    A real open-market follower cannot make >200% in fewer than ~60 sessions
    without a corporate action; such a return with a tiny entry price is the
    classic reverse-split adjustment artifact (as-filed vs split-adjusted)."""
    path = os.path.join(site_data, "paper", "positions.json")
    if not os.path.isfile(path):
        return []
    try:
        rows = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for p in rows:
        if p.get("status") != "open":
            continue
        roi = p.get("roi")
        hold = p.get("hold")
        # The engine itself withholds a straddled ROI (roi_review) — that is a
        # direct, authoritative signal that a corporate action was detected.
        if p.get("roi_review"):
            roi = p.get("roi_reported") if p.get("roi_reported") is not None else roi
        if roi is None:
            continue
        hold_sessions = hold if isinstance(hold, (int, float)) else 9999
        # >200% in <=90 sessions, any >500% move, or an engine roi_review flag:
        # all require a corporate-action check.
        extreme = bool(p.get("roi_review")) or (roi > 2.0 and hold_sessions <= 90) or roi > 5.0
        if not extreme:
            continue
        fake_row = {
            "acc": p.get("acc") or "", "icik": p.get("icik") or "",
            "fd": p.get("fd") or "", "td": p.get("td") or "",
            "tk": p.get("tk") or "", "co": p.get("co") or "",
            "in": p.get("insider") or "", "code": "P",
            "sh": p.get("insider_sh"), "px": p.get("insider_px"),
            "val": p.get("insider_val"), "sec": p.get("sec") or "", "der": 0,
        }
        epx, lpx = p.get("entry_px"), p.get("last_px")
        out.append(base_item(
            "Implausible mark-to-market ROI — corporate action / split-straddle review",
            "High",
            [fake_row],
            (f"{p.get('tk')} shows a {float(roi)*100:,.0f}% mark-to-market ROI over "
             f"~{hold_sessions} sessions (entry {epx} on {p.get('entry_d')} -> "
             f"{lpx} on {p.get('last_d')}, price source {p.get('price_src')}). "
             f"A move this large over this few sessions implies a corporate action "
             f"(reverse/forward split, merger cash-out, or a series adjustment mismatch)."),
            ("Verify the split/takeover history against SEC filings (8-K/DEF merger) "
             "and an adjusted historical price source. Reverse splits leave a follower's "
             "DOLLAR value roughly unchanged, so a multi-bagger ROI from a sub-$5 entry is "
             "usually an adjustment artifact and must NOT be reported as a gain. The as-filed "
             "Form 4 price and the market bars may be on different share bases until confirmed."),
            "Open paper position with roi>2.0 within <=60 held sessions, or roi>5.0 at any horizon.",
        ))
    return out


def scan_paper_na_price_consistency(site_data: str) -> list[dict]:
    """Flag positions whose price series lacks enough post-entry history to
    trust the mark (e.g. the cached bars stop short or are a stale tail), which
    can silently mix pre- and post-corporate-action prices."""
    path = os.path.join(site_data, "paper", "positions.json")
    if not os.path.isfile(path):
        return []
    try:
        rows = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for p in rows:
        if p.get("status") != "open":
            continue
        # A verified entry but NO r1 (next-session close) means the bar series
        # ends at/around the entry — the last_px mark may be the entry bar
        # itself or stale, so ROI is not a real forward mark.
        if p.get("entry_px") and p.get("r1") is None and p.get("hold") in (0, None):
            fake_row = {
                "acc": p.get("acc") or "", "icik": p.get("icik") or "",
                "fd": p.get("fd") or "", "tk": p.get("tk") or "",
                "co": p.get("co") or "", "in": p.get("insider") or "",
                "code": "P", "px": p.get("insider_px"), "val": p.get("insider_val"),
                "sh": p.get("insider_sh"), "der": 0,
            }
            out.append(base_item(
                "Paper mark not independently supported (no post-entry session bar)",
                "Medium",
                [fake_row],
                (f"{p.get('tk')} has a verified entry {p.get('entry_px')} on "
                 f"{p.get('entry_d')} but no next-session close in the cached series; "
                 f"the mark/ROI rests on a bar that may equal the entry bar."),
                "Refresh the price history before trusting ROI; a series that stops at "
                "entry cannot produce a real mark-to-market return.",
                "Verified entry but r1 is blank and hold==0.",
            ))
    return out


def dedupe(items: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for item in items:
        key = (
            item.get("category"),
            tuple(item.get("accessions") or []),
            item.get("fd"),
            item.get("td"),
            item.get("summary"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def scan(data_dir: str = store.DATA, target_year: int | None = None,
         audit: dict | None = None, cap: int = 250) -> list[dict]:
    target_year = target_year or audit_mod.asof_today().year
    audit = audit or audit_mod.scan(data_dir=data_dir, target_year=target_year)
    rows = load_target_rows(data_dir, target_year)
    items: list[dict] = []
    add_data_quality(items, audit)
    items.extend(scan_large_transactions(rows))
    items.extend(scan_cluster_buys(rows))
    items.extend(scan_synchronized_sales(rows))
    items.extend(scan_buyer_seller_overlap(rows))
    items.extend(scan_exercise_then_sale(rows))
    items.extend(scan_large_noncash(rows))
    site_data = os.path.join(ROOT, "data")
    items.extend(scan_paper_price_anomalies(site_data))
    items.extend(scan_paper_split_straddle(site_data))
    items.extend(scan_paper_na_price_consistency(site_data))

    items = dedupe(items)
    items.sort(key=lambda x: (
        SEVERITY_ORDER.get(x.get("severity", "Informational"), 9),
        -float(x.get("total_value") or 0),
        x.get("category") or "",
        x.get("tk") or "",
    ))
    for i, item in enumerate(items[:cap], 1):
        item["id"] = f"AUTO-{i:04d}"
        item["generated_by"] = "collector/irregularities.py"
        item["review_status"] = "needs_human_review"
    return items[:cap]


def write_json(obj, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def write_irregularities(data_dir: str = store.DATA, out_dir: str | None = None,
                         target_year: int | None = None, audit: dict | None = None) -> list[dict]:
    out_dir = out_dir or os.path.join(ROOT, "data")
    items = scan(data_dir=data_dir, target_year=target_year, audit=audit)
    write_json(items, os.path.join(out_dir, "irregularities.json"))
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate automated insider-trade irregularity flags.")
    ap.add_argument("--data", default=store.DATA)
    ap.add_argument("--out", default=os.path.join(ROOT, "data"))
    ap.add_argument("--year", type=int, default=audit_mod.asof_today().year)
    args = ap.parse_args()
    audit = audit_mod.write_audit(args.data, args.out, args.year,
                                  os.path.join(ROOT, "INSIDER_TRADING_FORENSIC_REPORT.md"))
    items = write_irregularities(args.data, args.out, args.year, audit)
    counts = Counter(x["severity"] for x in items)
    print(f"irregularities: {len(items):,} flags " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
