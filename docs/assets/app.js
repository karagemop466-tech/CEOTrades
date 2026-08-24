/* CEOTrades -- dashboard renderer.
   Every number shown comes from docs/data/*.json, which the Python pipeline
   generated exclusively from SEC EDGAR filings. This file only formats and
   renders that data; it contains no data of its own. */
(function () {
  "use strict";

  var D = window.TradeData;
  var page = document.body.dataset.page;
  var store = {
    manifest: null, summary: null, trades: [], companies: [],
    tCodes: {}, sicCodes: {}, verification: null, validation: null, errors: []
  };

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function el(id) { return document.getElementById(id); }

  function loadJSON(name) {
    return fetch("data/" + name, { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error("Could not load data/" + name + " (HTTP " + r.status + ")");
      return r.json();
    });
  }

  var EXTERNAL = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2.4" stroke-linecap="round" ' +
    'stroke-linejoin="round" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>' +
    '<polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>';

  function filingLink(rec, label) {
    return '<a class="filing-link" href="' + D.esc(rec.filing_url) +
      '" target="_blank" rel="noopener noreferrer" title="Open the original SEC Form 4 filing">' +
      (label || "Form 4") + " " + EXTERNAL + "</a>";
  }

  function codeChip(r) {
    var cls = r.acquired_disposed === "A" ? "b-buy" :
              r.acquired_disposed === "D" ? "b-sell" : "b-neutral";
    var label = store.tCodes[r.code] ? store.tCodes[r.code].desc : "";
    return '<span class="code-chip ' + cls + '" title="' + D.esc(label || "Transaction code " + r.code) + '">' +
      D.esc(r.code || "?") + "</span>";
  }

  function statCard(label, value, hint) {
    return '<div class="stat"><div class="label">' + D.esc(label) + '</div>' +
      '<div class="value">' + value + '</div>' +
      (hint ? '<div class="hint">' + hint + "</div>" : "") + "</div>";
  }

  function barChart(container, daily, opts) {
    opts = opts || {};
    var maxTrades = Math.max.apply(null, [1].concat(daily.map(function (d) { return d.trades; })));
    var maxValue = Math.max.apply(null, [1].concat(daily.map(function (d) { return d.value; })));
    var html = '<div class="bars" role="img" aria-label="Daily filings and traded value">';
    daily.forEach(function (d) {
      var hT = Math.max(2, Math.round((d.trades / maxTrades) * 100));
      var hV = Math.max(2, Math.round((d.value / maxValue) * 100));
      var tip = d.date + ": " + d.trades + " transactions, " +
        (d.value != null ? D.fmtCompact(d.value) : "n/a") + " reported value";
      html += '<div class="bar-col" title="' + D.esc(tip) + '">' +
        '<div class="bar" style="height:' + hT + '%" title="' + D.esc(d.trades + " transactions") + '"></div>' +
        '<div class="bar alt" style="height:' + hV + '%" title="' + D.esc(D.fmtCompact(d.value)) + '"></div>' +
        '<div class="bar-label">' + D.esc(d.date.slice(5)) + "</div></div>";
    });
    html += "</div>";
    if (!opts.noLegend) {
      html += '<div class="legend"><span><i style="background:var(--accent)"></i> Transactions</span>' +
        '<span><i style="background:#60a5fa"></i> Reported value (non-derivative)</span></div>';
    }
    container.innerHTML = html;
  }

  function hbarList(container, items, valueFn, nameFn, subFn) {
    var max = Math.max.apply(null, [1].concat(items.map(function (i) { return valueFn(i) || 0; })));
    container.innerHTML = '<div class="hbars">' + items.map(function (i) {
      var v = valueFn(i) || 0;
      var width = Math.max(1, Math.round((v / max) * 100));
      return '<div class="hbar-row"><div class="top">' +
        '<span class="name">' + D.esc(nameFn(i)) + (subFn ? " <span class=\"muted\">" + D.esc(subFn(i)) + "</span>" : "") + "</span>" +
        '<span class="amt">' + (typeof v === "number" ? D.fmtCompact(v) : D.esc(v)) + "</span></div>" +
        '<div class="hbar-track"><div class="hbar-fill" style="width:' + width + '%"></div></div>' +
        "</div>";
    }).join("") + "</div>";
  }

  /* ---------------------------------------------------------------- header */
  function initHeader() {
    $$(".main-nav a").forEach(function (a) {
      if (a.getAttribute("href") === page + ".html" || (page === "index" && a.getAttribute("href") === "index.html")) {
        a.classList.add("active");
      }
    });
    return loadJSON("manifest.json").then(function (m) {
      store.manifest = m;
      var badge = el("fresh-text");
      if (badge) {
        badge.textContent = "Updated " + D.fmtDate(m.window.end) +
          " · " + m.transactions + " trades";
        badge.title = "Data generated at " + m.generated_at +
          " from SEC EDGAR (" + m.endpoint_enumeration + ")";
      }
    }).catch(function () { /* header stays generic if missing */ });
  }

  /* ------------------------------------------------------- shared tables */
  function tradeRows(rows) {
    return rows.map(function (r) {
      var footnote = (r.footnotes || []).join(" | ");
      var ftTitle = footnote ? ' title="' + D.esc(footnote) + '"' : "";
      var kind = r.kind === "derivative" ? " <span class=\"muted\" title=\"Derivative security, Table II\">(deriv.)</span>" : "";
      var plan = r.plan_10b5_1 ? ' <span class="badge b-acc" title="Filed as made pursuant to a Rule 10b5-1(c) plan">10b5-1</span>' : "";
      return "<tr>" +
        '<td class="num">' + D.fmtDate(r.filing_date) + "</td>" +
        "<td><span class=\"ticker\">" + D.esc(r.company.ticker || "—") + "</span>" + kind + "<br>" +
          '<span class="muted">' + D.esc(r.company.name) + "</span></td>" +
        "<td>" + D.esc(r.owner.name) + "<br>" +
          '<span class="badge ' + D.roleClass(r.owner) + '">' + D.esc(D.roleLabel(r.owner)) + "</span></td>" +
        "<td>" + codeChip(r) + "</td>" +
        '<td><span class="badge ' + D.directionClass(r) + '">' + D.esc(D.directionLabel(r)) + "</span></td>" +
        '<td class="num"' + ftTitle + ">" + (r.shares == null ? "—" : D.fmtNum(r.shares)) + "</td>" +
        '<td class="num">' + (r.price_per_share == null ? "—" : D.fmtMoney(r.price_per_share)) + "</td>" +
        '<td class="num">' + (r.kind === "non-derivative" ? D.fmtMoney(r.value) : "—") + "</td>" +
        "<td>" + filingLink(r, "4") + "</td>" +
        "</tr>";
    }).join("");
  }

  function tradeTable(container, rows, caption) {
    container.innerHTML =
      '<div class="table-wrap"><table class="data-table">' +
      "<caption class=\"empty\">" + D.esc(caption || "") + "</caption>" +
      "<thead><tr><th>Filed</th><th>Ticker / Company</th><th>Reporting person</th>" +
      "<th>Code</th><th>Acq / Disp</th><th class=\"num\">Shares</th>" +
      '<th class="num">Price</th><th class="num">Value</th><th>Source</th></tr></thead>' +
      "<tbody>" + tradeRows(rows) + "</tbody></table></div>";
  }

  /* ------------------------------------------------------------------ home */
  function renderHome() {
    return Promise.all([
      loadJSON("summary.json"), loadJSON("trades.json"),
      loadJSON("transaction_codes.json"), loadJSON("sic_codes.json")
    ]).then(function (data) {
      store.summary = data[0]; store.trades = data[1];
      store.tCodes = data[2].codes || {}; store.sicCodes = data[3] || {};
      var s = store.summary, m = store.manifest || {};
      el("hero-window").textContent = m.window ?
        D.fmtDate(m.window.start) + " – " + D.fmtDate(m.window.end) : "";
      el("hero-count").textContent = s.records.total.toLocaleString("en-US");

      el("stats").innerHTML =
        statCard("Form 4 filings indexed", D.fmtNum(s.filings.indexed), m.window ? "SEC EDGAR, " + m.window.days + " days" : "SEC EDGAR") +
        statCard("Insider transactions", D.fmtNum(s.records.total),
          D.fmtNum(s.records.non_derivative) + " equity · " + D.fmtNum(s.records.derivative) + " derivative") +
        statCard("Reported value (equity)", D.fmtMoney(s.value.all),
          "per-share price × shares as filed") +
        statCard("Companies", D.fmtNum(s.filings.companies), "unique issuers") +
        statCard("Rule 10b5-1 plans", D.fmtNum(s.records.with_10b5_1_plan),
          "trades marked as under a Rule 10b5-1(c) plan") +
        statCard("Buy / Sell value", D.fmtCompact(s.value.buy) + " / " + D.fmtCompact(s.value.sell),
          "acquisitions vs dispositions");

      barChart(el("daily-chart"), s.daily);

      var buys = s.by_type.filter(function (t) { return t.kinds.indexOf("non-derivative") !== -1; }).slice(0, 8);
      hbarList(el("type-bars"), buys,
        function (t) { return t.value; },
        function (t) { return "Code " + (t.code || "—"); },
        function (t) { return "· " + t.count + " trades"; });

      hbarList(el("company-bars"), s.top_companies.slice(0, 8),
        function (c) { return c.value; },
        function (c) { return (c.ticker || "—") + " · " + c.name; },
        function (c) { return "· " + D.fmtNum(c.trades) + " trades"; });

      el("company-list-note").textContent =
        "Sums shares × reported per-share price for non-derivative trades only.";

      tradeTable(el("recent"), s.recent.slice(0, 12));
    });
  }

  /* ---------------------------------------------------------------- trades */
  var tradesState = { q: "", code: "", kind: "", direction: "", role: "",
                      dateFrom: "", dateTo: "", sort: "filing_date", dir: "desc",
                      page: 1, perPage: 25 };

  function renderTradesTable() {
    var filtered = D.filterTrades(store.trades, tradesState);
    var sorted = D.sortTrades(filtered, tradesState.sort, tradesState.dir);
    var p = D.paginate(sorted, tradesState.page, tradesState.perPage);
    tradesState.page = p.page;

    el("trades-count").textContent =
      p.total.toLocaleString("en-US") + " transactions (showing " +
      (p.total ? p.start + "–" + p.end : "0") + ")";

    el("trades-table").innerHTML =
      '<div class="table-wrap"><table class="data-table">' +
      "<thead><tr><th class=\"sortable\" data-sort=\"filing_date\">Filed" + sortArrows("filing_date") + "</th>" +
      '<th class="sortable" data-sort="ticker">Ticker / Company' + sortArrows("ticker") + "</th>" +
      '<th class="sortable" data-sort="owner">Reporting person' + sortArrows("owner") + "</th>" +
      "<th>Code</th><th>Acq / Disp</th>" +
      '<th class="num sortable" data-sort="shares">Shares' + sortArrows("shares") + "</th>" +
      '<th class="num sortable" data-sort="price">Price' + sortArrows("price") + "</th>" +
      '<th class="num sortable" data-sort="value">Value' + sortArrows("value") + "</th>" +
      "<th>Source</th></tr></thead><tbody>" +
      (tradeRows(p.rows) || '<tr><td colspan="9"><div class="empty">No transactions match your filters.</div></td></tr>') +
      "</tbody></table></div>";

    el("trades-pager").innerHTML =
      '<button class="pg-prev" ' + (p.page <= 1 ? "disabled" : "") + ">‹ Previous</button>" +
      '<span class="page-info">Page ' + p.page + " of " + p.pages + "</span>" +
      '<button class="pg-next" ' + (p.page >= p.pages ? "disabled" : "") + ">Next ›</button>" +
      '<span class="page-info">Rows per page</span>' +
      '<select class="pg-size"><option value="25">25</option><option value="50">50</option>' +
      '<option value="100">100</option></select>';
    var sizeSel = $(".pg-size", el("trades-pager"));
    sizeSel.value = String(tradesState.perPage);
    sizeSel.addEventListener("change", function () {
      tradesState.perPage = parseInt(sizeSel.value, 10);
      tradesState.page = 1; renderTradesTable();
    });
    $(".pg-prev", el("trades-pager")).addEventListener("click", function () {
      tradesState.page -= 1; renderTradesTable();
    });
    $(".pg-next", el("trades-pager")).addEventListener("click", function () {
      tradesState.page += 1; renderTradesTable();
    });
    $$("th.sortable", el("trades-table")).forEach(function (th) {
      th.addEventListener("click", function () {
        var key = th.dataset.sort;
        if (tradesState.sort === key) {
          tradesState.dir = tradesState.dir === "desc" ? "asc" : "desc";
        } else { tradesState.sort = key; tradesState.dir = "desc"; }
        tradesState.page = 1;
        renderTradesTable();
      });
    });
  }

  function sortArrows(key) {
    if (tradesState.sort !== key) return "";
    return ' <span class="arrow">' + (tradesState.dir === "desc" ? "▼" : "▲") + "</span>";
  }

  function renderTrades() {
    return Promise.all([loadJSON("trades.json"), loadJSON("transaction_codes.json")])
      .then(function (data) {
        store.trades = data[0]; store.tCodes = data[1].codes || {};
        el("type-count").textContent = store.trades.length.toLocaleString("en-US");

        // build code / role / direction / kind filter options
        var codes = {}, roles = { officer: 1, director: 1, "ten_percent_owner": 1, other: 1 };
        store.trades.forEach(function (r) {
          codes[r.code || "(none)"] = 1;
          var o = r.owner;
          if (o.officer) roles.officer++;
          if (o.director) roles.director++;
          if (o.ten_percent_owner) roles["ten_percent_owner"]++;
          if (o.other) roles.other++;
        });
        var codeSel = el("f-code");
        codeSel.innerHTML = '<option value="">All codes</option>' +
          Object.keys(codes).sort().map(function (c) {
            return '<option value="' + D.esc(c) + '">' + D.esc(c) + "</option>";
          }).join("");
        var roleSel = el("f-role");
        roleSel.innerHTML = '<option value="">All roles</option>' +
          Object.keys(roles).map(function (r) {
            return '<option value="' + r + '">' + D.esc(r === "ten_percent_owner" ? "10% owner" : r) + "</option>";
          }).join("");

        ["f-code", "f-role", "f-kind", "f-direction"].forEach(function (id) {
          el(id).addEventListener("change", function () {
            var v = el(id).value;
            if (id === "f-code") tradesState.code = v;
            if (id === "f-role") tradesState.role = v;
            if (id === "f-kind") tradesState.kind = v;
            if (id === "f-direction") tradesState.direction = v;
            tradesState.page = 1; renderTradesTable();
          });
        });
        var q = el("f-q");
        q.addEventListener("input", function () {
          tradesState.q = q.value; tradesState.page = 1;
          renderTradesTable();
        });
        ["f-from", "f-to"].forEach(function (id) {
          el(id).addEventListener("change", function () {
            if (id === "f-from") tradesState.dateFrom = el(id).value;
            else tradesState.dateTo = el(id).value;
            tradesState.page = 1; renderTradesTable();
          });
        });
        el("f-clear").addEventListener("click", function () {
          tradesState = { q: "", code: "", kind: "", direction: "", role: "",
                          dateFrom: "", dateTo: "", sort: "filing_date",
                          dir: "desc", page: 1, perPage: 25 };
          q.value = ""; codeSel.value = ""; roleSel.value = "";
          el("f-kind").value = ""; el("f-direction").value = "";
          el("f-from").value = ""; el("f-to").value = "";
          renderTradesTable();
        });
        el("f-csv").addEventListener("click", function () {
          var filtered = D.filterTrades(store.trades, tradesState);
          var sorted = D.sortTrades(filtered, tradesState.sort, tradesState.dir);
          var csv = D.csv(sorted, [
            { label: "filing_date", get: function (r) { return r.filing_date; } },
            { label: "accession_number", get: function (r) { return r.accession; } },
            { label: "filing_url", get: function (r) { return r.filing_url; } },
            { label: "ticker", get: function (r) { return r.company.ticker; } },
            { label: "company", get: function (r) { return r.company.name; } },
            { label: "owner", get: function (r) { return r.owner.name; } },
            { label: "role", get: function (r) { return D.roleLabel(r.owner); } },
            { label: "officer_title", get: function (r) { return r.owner.officer_title; } },
            { label: "kind", get: function (r) { return r.kind; } },
            { label: "code", get: function (r) { return r.code; } },
            { label: "acquired_disposed", get: function (r) { return r.acquired_disposed; } },
            { label: "transaction_date", get: function (r) { return r.date; } },
            { label: "shares", get: function (r) { return r.shares; } },
            { label: "price_per_share", get: function (r) { return r.price_per_share; } },
            { label: "value", get: function (r) { return r.value; } },
            { label: "shares_owned_after", get: function (r) { return r.shares_owned_after; } },
            { label: "rule_10b5_1_plan", get: function (r) { return r.plan_10b5_1; } },
            { label: "footnotes", get: function (r) { return (r.footnotes || []).join(" | "); } }
          ]);
          var blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
          var a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = "insider-trades.csv";
          a.click();
          URL.revokeObjectURL(a.href);
        });
        renderTradesTable();
      });
  }

  /* ----------------------------------------------------------------- types */
  function renderTypes() {
    return Promise.all([loadJSON("summary.json"), loadJSON("transaction_codes.json")])
      .then(function (data) {
        var s = data[0]; store.tCodes = data[1].codes || {};
        var byCode = {};
        s.by_type.forEach(function (t) { byCode[t.code] = t; });

        el("types-grid").innerHTML = s.by_type.map(function (t) {
          var meta = store.tCodes[t.code];
          var group = meta ? meta.group : "Code not listed in Form 4 Instruction 8";
          var desc = meta ? meta.desc : "See the original filing for the explanation of this code.";
          var kinds = t.kinds.map(function (k) {
            return k === "non-derivative" ? "Equity" : "Derivative";
          }).join(" & ");
          return '<div class="type-card">' +
            '<div class="code"><span class="code-chip b-neutral" style="width:44px;height:44px;font-size:1.1rem">' +
            D.esc(t.code || "?") + "</span></div>" +
            '<div class="meta"><div class="muted" style="font-size:.72rem;text-transform:uppercase;letter-spacing:.05em">' +
            D.esc(group) + "</div>" +
            "<h3 style=\"margin:4px 0 4px\">" + D.esc(desc) + "</h3>" +
            '<div class="stats">' +
            "<div><b>" + D.fmtNum(t.count) + "</b>trades</div>" +
            "<div><b>" + D.fmtNum(t.shares) + "</b>units</div>" +
            "<div><b>" + D.fmtMoney(t.value) + "</b>value</div>" +
            "<div><b>" + D.esc(kinds) + "</b></div></div></div></div>";
        }).join("") || '<div class="empty">No transactions in this window.</div>';

        el("types-reference").innerHTML = Object.keys(store.tCodes).sort().map(function (c) {
          var m = store.tCodes[c];
          var t = byCode[c];
          return "<tr><td><span class=\"code-chip b-neutral\">" + D.esc(c) + "</span></td>" +
            "<td>" + D.esc(m.desc) + "</td>" +
            "<td>" + D.esc(m.group) + "</td>" +
            '<td class="num">' + (t ? D.fmtNum(t.count) : "0") + "</td></tr>";
        }).join("");
      });
  }

  /* ------------------------------------------------------------------ role */
  function renderRoles() {
    return loadJSON("summary.json").then(function (s) {
      store.summary = s;
      var total = s.records.total || 1;
      var roleNames = {
        officer: "Officers", director: "Directors", ten_percent_owner: "10% owners",
        other: "Other insiders", multiple: "Insiders with several roles"
      };
      var colors = { officer: "b-acc", director: "b-grant",
                     ten_percent_owner: "b-neutral", other: "b-neutral",
                     multiple: "b-buy" };
      el("roles-grid").innerHTML = s.by_role.map(function (r) {
        if (!roleNames[r.role]) return "";
        var pct = Math.round((r.trades / total) * 100);
        return '<div class="card"><span class="badge ' + colors[r.role] + '">' +
          D.esc(roleNames[r.role]) + "</span>" +
          '<div style="font-size:1.7rem;font-weight:700;margin:8px 0 2px">' +
          D.fmtNum(r.trades) + "</div>" +
          '<div class="muted" style="font-size:.82rem">' + pct + "% of transactions</div></div>";
      }).join("");

      el("titles").innerHTML = s.top_titles.map(function (t) {
        return '<div class="card" style="display:flex;justify-content:space-between;align-items:center;padding:12px 16px">' +
          "<span>" + D.esc(t.title) + "</span><b>" + D.fmtNum(t.trades) + "</b></div>";
      }).join("");
    });
  }

  /* -------------------------------------------------------------- companies */
  var compState = { q: "", page: 1, perPage: 40 };

  function renderCompaniesTable() {
    var q = compState.q.toLowerCase();
    var rows = store.companies.filter(function (c) {
      if (!q) return true;
      return (c.ticker + " " + c.name + " " + (c.sic || "") + " " + (c.sic_desc || ""))
        .toLowerCase().indexOf(q) !== -1;
    });
    rows.sort(function (a, b) {
      return (b.trades - a.trades) || a.name.localeCompare(b.name);
    });
    var p = D.paginate(rows, compState.page, compState.perPage);
    compState.page = p.page;
    el("companies-count").textContent = p.total.toLocaleString("en-US") +
      " companies (showing " + (p.total ? p.start + "–" + p.end : "0") + ")";
    var sic = store.sicCodes.codes || {};
    el("companies-table").innerHTML =
      '<div class="table-wrap"><table class="data-table"><thead><tr>' +
      "<th>Ticker</th><th>Company</th><th>SIC</th><th class=\"num\">Trades</th>" +
      '<th class="num">Value</th><th>SEC filings</th></tr></thead><tbody>' +
      (p.rows.map(function (c) {
        var entry = c.sic ? sic[String(c.sic)] : null;
        return "<tr><td><span class=\"ticker\">" + D.esc(c.ticker || "—") + "</span></td>" +
          "<td>" + D.esc(c.name) + "</td>" +
          "<td>" + (c.sic ? D.esc(c.sic + (entry ? " · " + entry.title : "")) : '<span class="muted">n/a</span>') + "</td>" +
          '<td class="num">' + D.fmtNum(c.trades) + "</td>" +
          '<td class="num">' + D.fmtMoney(c.value) + " </td>" +
          "<td><a href=\"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=" +
          D.esc(c.cik.slice(0, 10)) + "&amp;type=4&amp;dateb=&amp;owner=include&amp;count=40\" " +
          'target="_blank" rel="noopener noreferrer">Form 4s ' + EXTERNAL + "</a></td></tr>";
      }).join("") || '<tr><td colspan="6"><div class="empty">No companies match.</div></td></tr>') +
      "</tbody></table></div>";

    el("companies-pager").innerHTML =
      '<button class="pg-prev" ' + (p.page <= 1 ? "disabled" : "") + ">‹ Previous</button>" +
      '<span class="page-info">Page ' + p.page + " of " + p.pages + "</span>" +
      '<button class="pg-next" ' + (p.page >= p.pages ? "disabled" : "") + ">Next ›</button>";
    $(".pg-prev", el("companies-pager")).addEventListener("click", function () {
      compState.page -= 1; renderCompaniesTable();
    });
    $(".pg-next", el("companies-pager")).addEventListener("click", function () {
      compState.page += 1; renderCompaniesTable();
    });
  }

  function renderCompanies() {
    return Promise.all([loadJSON("companies.json"), loadJSON("sic_codes.json")])
      .then(function (data) {
        store.companies = data[0]; store.sicCodes = data[1] || {};
        var q = el("c-q");
        q.addEventListener("input", function () {
          compState.q = q.value; compState.page = 1; renderCompaniesTable();
        });
        renderCompaniesTable();
      });
  }

  /* ---------------------------------------------------------------- sectors */
  function renderSectors() {
    return Promise.all([loadJSON("summary.json"), loadJSON("sic_codes.json")])
      .then(function (data) {
        var s = data[0]; var sic = data[1].codes || {};
        var rows = s.sectors.map(function (x) {
          var entry = x.sic !== "unknown" ? sic[String(x.sic)] : null;
          return {
            sic: x.sic, title: entry ? entry.title : (x.sic === "unknown" ? "Unclassified" : "Unknown SIC"),
            trades: x.trades, companies: x.companies, value: x.value
          };
        });
        var max = Math.max.apply(null, [1].concat(rows.map(function (r) { return r.trades; })));
        el("sectors-body").innerHTML = rows.map(function (r) {
          return "<tr><td>" + D.esc(r.sic) + "</td><td>" + D.esc(r.title) + "</td>" +
            '<td class="num">' + D.fmtNum(r.companies) + "</td>" +
            '<td class="num">' + D.fmtNum(r.trades) + "</td>" +
            '<td class="num">' + D.fmtMoney(r.value) + "</td>" +
            '<td style="min-width:160px"><div class="hbar-track"><div class="hbar-fill" style="width:' +
            Math.max(1, Math.round((r.trades / max) * 100)) + '%"></div></div></td></tr>';
        }).join("") || '<tr><td colspan="6"><div class="empty">No data.</div></td></tr>';
        el("sectors-note").textContent =
          "SIC codes are the issuer's Standard Industrial Classification as reported " +
          "by SEC on the filing. Titles come from SEC's official SIC code list.";
      });
  }

  /* ---------------------------------------------------------------- filings */
  var filState = { page: 1, perPage: 30 };

  function renderFilingsTable() {
    var filings = D.groupByFiling(store.trades);
    filings.sort(function (a, b) {
      return (b.filing_date || "").localeCompare(a.filing_date || "") ||
             b.accession.localeCompare(a.accession);
    });
    var p = D.paginate(filings, filState.page, filState.perPage);
    filState.page = p.page;
    el("filings-count").textContent = p.total.toLocaleString("en-US") +
      " filings (showing " + (p.total ? p.start + "–" + p.end : "0") + ")";
    el("filings-table").innerHTML =
      '<div class="table-wrap"><table class="data-table"><thead><tr>' +
      "<th>Filed</th><th>Company</th><th>Reporting person</th><th class=\"num\">Transactions</th>" +
      "<th>Contents</th><th>Source</th></tr></thead><tbody>" +
      (p.rows.map(function (f) {
        var parts = Object.keys(f.codes).map(function (c) {
          return '<span class="code-chip b-neutral" title="' + D.esc(f.codes[c] + " transaction(s)") + '">' +
            D.esc(c || "?") + "</span>";
        });
        return "<tr><td class=\"num\">" + D.fmtDate(f.filing_date) + "</td>" +
          "<td><span class=\"ticker\">" + D.esc(f.company.ticker || "—") + "</span><br>" +
          '<span class="muted">' + D.esc(f.company.name) + "</span></td>" +
          "<td>" + D.esc(f.owner.name) + "<br><span class=\"muted\">" +
          D.esc(D.roleLabel(f.owner)) + "</span></td>" +
          '<td class="num">' + D.fmtNum(f.trades) + "</td>" +
          "<td>" + parts.join(" ") + "</td>" +
          "<td>" + filingLink(f, "4") + "</td></tr>";
      }).join("") || '<tr><td colspan="6"><div class="empty">No filings.</div></td></tr>') +
      "</tbody></table></div>";
    el("filings-pager").innerHTML =
      '<button class="pg-prev" ' + (p.page <= 1 ? "disabled" : "") + ">‹ Previous</button>" +
      '<span class="page-info">Page ' + p.page + " of " + p.pages + "</span>" +
      '<button class="pg-next" ' + (p.page >= p.pages ? "disabled" : "") + ">Next ›</button>";
    $(".pg-prev", el("filings-pager")).addEventListener("click", function () {
      filState.page -= 1; renderFilingsTable();
    });
    $(".pg-next", el("filings-pager")).addEventListener("click", function () {
      filState.page += 1; renderFilingsTable();
    });
  }

  function renderFilings() {
    return loadJSON("trades.json").then(function (trades) {
      store.trades = trades;
      renderFilingsTable();
    });
  }

  /* ------------------------------------------------------------ methodology */
  function renderMethodology() {
    return Promise.all([
      loadJSON("manifest.json"), loadJSON("validation.json"),
      loadJSON("verification.json"), loadJSON("errors.json")
    ]).then(function (data) {
      var m = data[0], val = data[1], ver = data[2], errs = data[3];
      el("m-window").textContent = m.window.start + " to " + m.window.end + " (" + m.window.days + " days)";
      el("m-sources").innerHTML =
        '<div class="table-wrap"><table class="data-table"><thead><tr>' +
        "<th>Input</th><th>Endpoint</th><th>Used for</th></tr></thead><tbody>" +
        "<tr><td>Filing index</td><td class=\"mono\">" + D.esc(m.endpoint_enumeration) + "</td>" +
        "<td>Enumerating every Form 4 filed in the window</td></tr>" +
        "<tr><td>Raw filings</td><td class=\"mono\">" + D.esc(m.endpoint_filings) + "</td>" +
        "<td>Machine-readable ownership XML of each filing</td></tr>" +
        "<tr><td>SIC titles</td><td class=\"mono\">https://www.sec.gov/search-filings/standard-industrial-classification-sic-code-list</td>" +
        "<td>Industry titles for the sectors page</td></tr>" +
        "<tr><td>Code descriptions</td><td class=\"mono\">https://www.sec.gov/files/form4.pdf</td>" +
        "<td>Transaction-code meanings (Instruction 8)</td></tr></tbody></table></div>";

      el("m-manifest").innerHTML =
        "<ul><li>Generated: <b>" + D.esc(m.generated_at) + "</b></li>" +
        "<li>Filings indexed: <b>" + D.fmtNum(m.filings_indexed) + "</b></li>" +
        "<li>Filings parsed: <b>" + D.fmtNum(m.filings_parsed) + "</b></li>" +
        "<li>Transactions: <b>" + D.fmtNum(m.transactions) + "</b></li>" +
        "<li>Companies: <b>" + D.fmtNum(m.companies) + "</b></li>" +
        "<li>Parse failures: <b>" + D.fmtNum(m.errors) + "</b></li></ul>";

      el("m-validation").innerHTML = (val && val.checks ? val.checks : []).map(function (c) {
        return "<li>" + (c[1] ? "✅" : "❌") + " " + D.esc(c[0]) +
          (c[2] ? " <span class='muted'>(" + D.esc(c[2]) + ")</span>" : "") + "</li>";
      }).join("") || "<li>No validation report yet.</li>";

      el("m-verification").innerHTML = ver ?
        "<ul><li>Sampled <b>" + D.fmtNum(ver.sample_size) + "</b> records randomly (seed " + ver.seed + ")</li>" +
        "<li>Re-downloaded each filing from SEC and re-parsed it</li>" +
        "<li>Verified issuer CIK, owner CIK, period end and the specific transaction row</li>" +
        "<li>Result: <b class=\"" + (ver.failed ? "b-sell" : "b-buy") + "\">" +
        D.fmtNum(ver.passed) + " passed / " + D.fmtNum(ver.failed) + " failed</b></li></ul>" +
        (ver.failed ? "<div class=\"callout warn\"><div class=\"title\">Mismatches</div>" +
          ver.results.filter(function (r) { return !r.pass; }).map(function (r) {
            return "<div>" + D.esc(r.accession) + " — " + D.esc(r.error || JSON.stringify(r.checks)) + "</div>";
          }).join("") + "</div>" : "")
        : "<li>No verification report yet.</li>";

      el("m-errors").innerHTML = errs.length ?
        '<p class="muted">' + D.fmtNum(errs.length) + " filing(s) could not be parsed. Detailed errors are in " +
        '<span class="mono">docs/data/errors.json</span>:</p><ul>' +
        errs.slice(0, 25).map(function (e) {
          var url = e.cik ? "https://www.sec.gov/Archives/edgar/data/" +
            String(parseInt(e.cik, 10)) + "/" + e.accession.replace(/-/g, "") +
            "/" + e.accession + "-index.htm" : "";
          return "<li><span class=\"mono\">" + D.esc(e.accession) + "</span> — " +
            D.esc(e.error) + (url ? " (" + '<a href="' + D.esc(url) + '" target="_blank" rel="noopener noreferrer">filing ' + EXTERNAL + "</a>)" : "") + "</li>";
        }).join("") + "</ul>"
        : "<p>No parse failures. ✓</p>";
    });
  }

  /* ------------------------------------------------------------------ boot */
  var renderers = {
    index: renderHome, trades: renderTrades, types: renderTypes,
    roles: renderRoles, companies: renderCompanies, sectors: renderSectors,
    filings: renderFilings, methodology: renderMethodology
  };

  function showFatal(err) {
    var box = el("fatal");
    if (!box) return;
    box.style.display = "block";
    el("fatal-msg").textContent = err && err.message ? err.message : String(err);
  }

  document.addEventListener("DOMContentLoaded", function () {
    initHeader().then(function () {
      var fn = renderers[page] || renderers.index;
      return fn();
    }).catch(showFatal).then(function () {
      var loading = el("loading");
      if (loading) loading.remove();
    });
  });
}());
