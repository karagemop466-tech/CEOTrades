/* CEOTrades -- pure data helpers (no DOM access).
   Loaded by the browser and by the Node test suite so every aggregation
   used on the site can be verified programmatically. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.TradeData = factory();
  }
}(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function fmtNum(n) {
    if (n == null || isNaN(n)) return "—";
    return Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 });
  }

  function fmtMoney(n) {
    if (n == null || isNaN(n)) return "—";
    return "$" + Number(n).toLocaleString("en-US",
      { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function fmtCompact(n) {
    if (n == null || isNaN(n)) return "—";
    const abs = Math.abs(n);
    const sign = n < 0 ? "-" : "";
    if (abs >= 1e9) return sign + "$" + (abs / 1e9).toFixed(2) + "B";
    if (abs >= 1e6) return sign + "$" + (abs / 1e6).toFixed(2) + "M";
    if (abs >= 1e3) return sign + "$" + (abs / 1e3).toFixed(1) + "K";
    return sign + "$" + abs.toFixed(2);
  }

  function fmtDate(s) {
    if (!s) return "—";
    const parts = String(s).split("-");
    if (parts.length !== 3) return s;
    return parts[1] + "/" + parts[2] + "/" + parts[0];
  }

  function fmtDateTime(s) {
    if (!s) return "—";
    const m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
    if (!m) return s;
    return m[2] + "/" + m[3] + "/" + m[1] + " " + m[4] + ":" + m[5] + " UTC";
  }

  /* Direction badge class: the filing's own acquired/disposed code. */
  function directionClass(rec) {
    if (rec.acquired_disposed === "A") return "b-buy";
    if (rec.acquired_disposed === "D") return "b-sell";
    return "b-neutral";
  }

  function directionLabel(rec) {
    if (rec.acquired_disposed === "A") return "Acquired";
    if (rec.acquired_disposed === "D") return "Disposed";
    return "—";
  }

  /* Primary role of the reporting person on this filing. */
  function roles(owner) {
    const out = [];
    if (owner.officer) out.push("Officer");
    if (owner.director) out.push("Director");
    if (owner.ten_percent_owner) out.push("10% Owner");
    if (owner.other) out.push("Other");
    return out.length ? out : ["N/A"];
  }

  function roleLabel(owner) {
    return roles(owner).join(", ");
  }

  function roleClass(owner) {
    if (owner.officer) return "b-acc";
    if (owner.director) return "b-grant";
    if (owner.ten_percent_owner) return "b-neutral";
    if (owner.other) return "b-neutral";
    return "b-neutral";
  }

  /* ---------------------------------------------------------------- filters */
  function filterTrades(trades, opts) {
    const q = (opts.q || "").trim().toLowerCase();
    return trades.filter(function (r) {
      if (opts.code && r.code !== opts.code) return false;
      if (opts.kind && r.kind !== opts.kind) return false;
      if (opts.direction && r.acquired_disposed !== opts.direction) return false;
      if (opts.role) {
        const owner = r.owner;
        const match = opts.role === "multiple"
          ? (owner.officer ? 1 : 0) + (owner.director ? 1 : 0) +
            (owner.ten_percent_owner ? 1 : 0) + (owner.other ? 1 : 0) > 1
          : (opts.role === "officer" && owner.officer) ||
            (opts.role === "director" && owner.director) ||
            (opts.role === "ten_percent_owner" && owner.ten_percent_owner) ||
            (opts.role === "other" && owner.other);
        if (!match) return false;
      }
      const date = r.filing_date || "";
      if (opts.dateFrom && date < opts.dateFrom) return false;
      if (opts.dateTo && date > opts.dateTo) return false;
      if (q) {
        const hay = [
          r.company.ticker, r.company.name, r.owner.name,
          r.owner.officer_title, r.security_title, r.accession
        ].join(" ").toLowerCase();
        if (hay.indexOf(q) === -1 && (r.footnotes || []).join(" ").toLowerCase().indexOf(q) === -1) {
          return false;
        }
      }
      return true;
    });
  }

  function sortTrades(trades, key, dir) {
    const sign = dir === "desc" ? -1 : 1;
    const val = function (r) {
      if (key === "value") return r.value == null ? -Infinity : r.value;
      if (key === "shares") return r.shares == null ? -Infinity : r.shares;
      if (key === "price") return r.price_per_share == null ? -Infinity : r.price_per_share;
      if (key === "ticker") return (r.company.ticker || "").toUpperCase();
      if (key === "owner") return (r.owner.name || "").toLowerCase();
      if (key === "company") return (r.company.name || "").toLowerCase();
      return r[key] || "";
    };
    return trades.slice().sort(function (a, b) {
      const av = val(a), bv = val(b);
      if (av < bv) return -1 * sign;
      if (av > bv) return 1 * sign;
      return 0;
    });
  }

  function paginate(arr, page, perPage) {
    const total = arr.length;
    const pages = Math.max(1, Math.ceil(total / perPage));
    const p = Math.min(Math.max(1, page), pages);
    const start = (p - 1) * perPage;
    return { rows: arr.slice(start, start + perPage), page: p, pages: pages,
             total: total, start: start + 1, end: Math.min(start + perPage, total) };
  }

  /* ---------------------------------------------------------- aggregations */
  function groupByFiling(trades) {
    const map = {};
    trades.forEach(function (r) {
      const acc = r.accession;
      if (!map[acc]) {
        map[acc] = {
          accession: acc, filing_date: r.filing_date, kind: r.kind,
          company: r.company, owner: r.owner, filing_url: r.filing_url,
          period_end: r.period_end, trades: 0,
          kinds: {}, codes: {}
        };
      }
      map[acc].trades += 1;
      map[acc].kinds[r.kind] = (map[acc].kinds[r.kind] || 0) + 1;
      map[acc].codes[r.code || ""] = (map[acc].codes[r.code || ""] || 0) + 1;
    });
    return Object.keys(map).map(function (acc) { return map[acc]; });
  }

  /* Independent recomputation of the daily series (used by tests and the
     dashboard; must match summary.json from the Python pipeline). */
  function dailySeries(trades) {
    const byDate = {};
    trades.forEach(function (r) {
      const d = r.filing_date || "unknown";
      if (!byDate[d]) byDate[d] = { date: d, filings: {}, trades: 0,
                                    shares: 0, value: 0 };
      byDate[d].filings[r.accession] = 1;
      byDate[d].trades += 1;
      byDate[d].shares += r.shares || 0;
      if (r.kind === "non-derivative") byDate[d].value += r.value || 0;
    });
    return Object.keys(byDate).sort().map(function (d) {
      const s = byDate[d];
      return { date: d, filings: Object.keys(s.filings).length, trades: s.trades,
               shares: Math.round(s.shares * 100) / 100,
               value: Math.round(s.value * 100) / 100 };
    });
  }

  function csv(rows, columns) {
    const escCsv = function (v) {
      const s = v == null ? "" : String(v);
      return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    const lines = [columns.map(function (c) { return escCsv(c.label); }).join(",")];
    rows.forEach(function (r) {
      lines.push(columns.map(function (c) {
        return escCsv(c.get(r));
      }).join(","));
    });
    return lines.join("\n");
  }

  return {
    esc: esc, fmtNum: fmtNum, fmtMoney: fmtMoney, fmtCompact: fmtCompact,
    fmtDate: fmtDate, fmtDateTime: fmtDateTime,
    directionClass: directionClass, directionLabel: directionLabel,
    roles: roles, roleLabel: roleLabel, roleClass: roleClass,
    filterTrades: filterTrades, sortTrades: sortTrades, paginate: paginate,
    groupByFiling: groupByFiling, dailySeries: dailySeries, csv: csv
  };
}));
