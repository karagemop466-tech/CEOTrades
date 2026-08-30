#!/usr/bin/env python3
"""
CEOTrades static page generator.

Writes the HTML shells. All figures are fetched client-side from ./data/*.json,
so pages never need regenerating when the data refreshes — only when the layout
changes. Run: python3 collector/build_pages.py
"""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

PAGES = [
    ("index.html", "index", "Dashboard"),
    ("paper.html", "paper", "Paper book"),
    ("trades.html", "trades", "Trades"),
    ("companies.html", "companies", "Companies"),
    ("insiders.html", "insiders", "Insiders"),
    ("activity.html", "activity", "Insider flows"),
    ("analysis.html", "analysis", "Findings"),
    ("irregularities.html", "irregularities", "Irregularities"),
    ("about.html", "about", "About"),
]


def nav(active: str) -> str:
    out = []
    for href, key, label in PAGES:
        cls = ' class="active"' if key == active else ""
        out.append(f'        <a href="{href}"{cls}>{label}</a>')
    return "\n".join(out)


def shell(active: str, title: str, desc: str, body: str, script: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · CEOTrades</title>
<meta name="description" content="{desc}">
<link rel="stylesheet" href="css/site.css">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📈</text></svg>">
</head>
<body>
<header class="top"><div class="wrap nav">
  <a class="brand" href="index.html">📈 CEO<span>Trades</span></a>
  <nav>
{nav(active)}
  </nav>
</div></header>
<main class="wrap">
{body}
</main>
<footer><div class="wrap">
  <div>Source: <a href="https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets">SEC Forms 3/4/5</a>.
    Prices: Yahoo Finance / Stooq. Educational research only — not investment advice.</div>
  <div><a href="https://github.com/karagemop466-tech/CEOTrades">Source on GitHub</a></div>
</div></footer>
<script src="js/app.js"></script>
<script>
{script}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------

INDEX_BODY = """
<section class="hero">
  <h1>Every insider trade, collected and forward-tested.</h1>
  <p>CEOTrades pulls every SEC Form 3, 4 and 5 filed by corporate insiders, organises it by
     company and person, and paper-trades each open-market purchase with a simulated
     $10,000 bought at the first market open <em>after</em> the filing became public —
     using real daily prices, never the insider's own fill.</p>
  <div class="pills" id="pills"></div>
</section>

<div class="grid g5" id="kpis"></div>
<div class="note" id="auditnote" style="display:none"></div>

<h2 class="sec">Paper book <small>simulated $10,000 per insider purchase</small></h2>
<div class="grid g4" id="paperkpis"></div>

<h2 class="sec">Explore</h2>
<div class="hub">
  <a href="paper.html"><div class="t">📊 Paper book</div><div class="d">Every simulated position: insider's price, our entry, gap, ROI and horizon returns.</div></a>
  <a href="backtest.html"><div class="t">🔄 Backtest</div><div class="d">Verified entry AND exit prices/dates for every tracked insider buy, with realised round-trip ROI.</div></a>
  <a href="trades.html"><div class="t">🧾 All trades</div><div class="d">Search and filter the full transaction tape by ticker, insider, code and date.</div></a>
  <a href="companies.html"><div class="t">🏢 Companies</div><div class="d">Insider activity aggregated per issuer, with buy/sell flow and net dollars.</div></a>
  <a href="insiders.html"><div class="t">👤 Insiders</div><div class="d">Directors, officers and 10% owners ranked by activity and net flow.</div></a>
  <a href="activity.html"><div class="t">🔁 Insider flows</div><div class="d">Track insiders who both buy and sell, with reported post-transaction holdings and review links.</div></a>
  <a href="analysis.html"><div class="t">🔍 Findings</div><div class="d">ROI by role, conviction size and holding period, plus filing-lag statistics.</div></a>
  <a href="irregularities.html"><div class="t">⚠️ Irregularities</div><div class="d">Automated review flags for coverage gaps, large transactions, clusters and data-quality issues.</div></a>
  <a href="about.html"><div class="t">📖 Methodology</div><div class="d">Exactly how signals, entries and returns are computed — and the caveats.</div></a>
</div>

<h2 class="sec">Recent filings <small id="tapesub"></small></h2>
<div class="card tight" id="tape"></div>
<div style="margin-top:12px"><a class="btn" href="trades.html">Browse all trades →</a></div>
"""

INDEX_JS = """
Promise.all([CT.load('data/summary.json'), CT.load('data/paper/summary.json').catch(function(){return null;}), CT.load('data/recent.json').catch(function(){return [];}), CT.load('data/audit.json').catch(function(){return null;})])
.then(function (r) {
  var s = r[0], p = r[1], recent = r[2] || [], audit = r[3];
  var c = s.counts, v = s.value;
  document.getElementById('pills').innerHTML =
    '<span class="pill">' + CT.fmtInt(c.trades) + ' transactions</span>' +
    '<span class="pill">' + CT.fmtInt(c.companies) + ' companies</span>' +
    '<span class="pill">' + CT.fmtInt(c.insiders) + ' insiders</span>' +
    '<span class="pill">' + CT.esc(s.range.from || '—') + ' → ' + CT.esc(s.range.to || '—') + '</span>';

  document.getElementById('kpis').innerHTML =
    CT.statCard('Transactions', CT.fmtInt(c.trades), CT.fmtInt(c.filings) + ' filings') +
    CT.statCard('Companies', CT.fmtInt(c.companies), 'issuers with filings') +
    CT.statCard('Insiders', CT.fmtInt(c.insiders), 'directors, officers, 10% owners') +
    CT.statCard('Insider buying', CT.fmtMoney(v.buy), CT.fmtInt(c.buys) + ' code-P purchases', 'buy') +
    CT.statCard('Insider selling', CT.fmtMoney(v.sell), CT.fmtInt(c.sells) + ' code-S sales', 'sell');

  if (audit && audit.completeness && !audit.completeness.complete) {
    var an = document.getElementById('auditnote');
    an.style.display = 'block';
    an.innerHTML = '<strong>Audit warning:</strong> Current local data is ' +
      CT.esc(audit.completeness.status || 'incomplete_or_unproven') + '. ' +
      '<a href="irregularities.html">Review coverage and flags →</a>';
  }

  var pk = document.getElementById('paperkpis');
  if (p && p.counts && p.counts.open) {
    pk.innerHTML =
      CT.statCard('Open positions', CT.fmtInt(p.counts.open), 'one per insider-buy filing') +
      CT.statCard('Capital deployed', CT.fmtMoney(p.capital.deployed), '$10,000 each') +
      CT.statCard('Current value', CT.fmtMoney(p.capital.value), 'marked to last close') +
      CT.statCard('Net P&L', CT.fmtMoney(p.capital.pnl), CT.fmtPct(p.capital.roi) + ' on deployed capital',
                  CT.pctCls(p.capital.roi));
  } else {
    pk.innerHTML = '<div class="card"><div class="empty">No paper positions yet — the collector has not run.</div></div>';
  }

  document.getElementById('tapesub').textContent = recent.length ? 'latest ' + CT.fmtInt(recent.length) + ' rows' : '';
  new CT.Table({
    mount: '#tape', rows: recent, pageSize: 25, sortKey: 'fd', sortDir: -1,
    empty: 'No recent filings in the window.',
    cols: [
      { key: 'fd', label: 'Filed', cls: 'nowrap' },
      { key: 'tk', label: 'Ticker', render: function (r) {
          return r.tk ? '<a class="ticker" href="companies.html?cik=' + CT.esc(r.icik || '') + '">' + CT.esc(r.tk) + '</a>' : '<span class="sub">—</span>'; } },
      { key: 'co', label: 'Company', render: function (r) { return '<div class="trunc" title="' + CT.esc(r.co) + '">' + CT.esc(CT.titleCase(r.co)) + '</div>'; } },
      { key: 'in', label: 'Insider', render: function (r) { return '<div class="trunc" title="' + CT.esc(r['in']) + '">' + CT.esc(CT.titleCase(r['in'])) + '</div><div class="sub">' + CT.esc(r.rel || '') + '</div>'; } },
      { key: 'code', label: 'Code', render: function (r) { return CT.sideBadge(r.code, r.side); } },
      { key: 'sh', label: 'Shares', num: true, render: function (r) { return CT.fmtInt(r.sh); } },
      { key: 'px', label: 'Price', num: true, render: function (r) { return CT.fmtPx(r.px); } },
      { key: 'val', label: 'Value', num: true, render: function (r) { return CT.fmtMoney(r.val); } },
      { key: 'acc', label: 'Filing', sort: false, render: function (r) { return r.acc ? '<a href="' + CT.edgar(r.acc, r.icik) + '" target="_blank" rel="noopener">SEC ↗</a>' : ''; } }
    ]
  });
}).catch(function (e) { CT.fail(document.getElementById('kpis'), e); });
"""

# ---------------------------------------------------------------------------

PAPER_BODY = """
<div class="page-head">
  <h1>Paper book</h1>
  <p>Every simulated position. When an insider reports an open-market purchase (Form 4/5,
     transaction code <code>P</code>), we buy <strong>$10,000</strong> of the stock at the
     regular-session <strong>open of the first trading day strictly after the filing date</strong>.
     Positions are never closed — this is a forward test. <em>Gap</em> is how much more (or less)
     we paid than the insider.</p>
</div>

<div class="grid g4" id="kpis"></div>
<div id="rule" class="note" style="display:none"></div>

<h2 class="sec">Positions</h2>
<div class="controls">
  <input type="search" id="q" placeholder="Search ticker, company or insider…">
  <select id="status"><option value="">All statuses</option><option value="open">Open</option><option value="awaiting_entry">Awaiting entry</option><option value="no_price">No price</option></select>
  <select id="role"><option value="">All roles</option><option>Director</option><option>Officer</option><option>10% Owner</option></select>
  <select id="win"><option value="">All results</option><option value="w">Winners only</option><option value="l">Losers only</option></select>
  <button class="btn" id="csv">Export view (CSV)</button>
  <a class="btn" href="data/paper/positions.csv.gz" download>Full book (.csv.gz)</a>
</div>
<div class="card tight" id="tbl"></div>
"""

PAPER_JS = """
Promise.all([CT.load('data/paper/summary.json'), CT.load('data/paper/positions.json')])
.then(function (r) {
  var s = r[0], rows = r[1] || [];
  document.getElementById('kpis').innerHTML =
    CT.statCard('Open positions', CT.fmtInt(s.counts.open), CT.fmtInt(s.counts.signals) + ' signals total') +
    CT.statCard('Deployed', CT.fmtMoney(s.capital.deployed), '$' + CT.fmtInt(s.stake) + ' per signal') +
    CT.statCard('Value now', CT.fmtMoney(s.capital.value), 'at last close') +
    CT.statCard('Net P&L', CT.fmtMoney(s.capital.pnl), CT.fmtPct(s.capital.roi), CT.pctCls(s.capital.roi));

  if (s.rule) {
    var n = document.getElementById('rule');
    n.style.display = 'block';
    n.innerHTML = '<strong>Rule:</strong> ' + CT.esc(s.rule.entry) + '. ' +
      '<strong>Exit:</strong> ' + CT.esc(s.rule.exit) + '. ' +
      '<strong>Costs:</strong> ' + CT.esc(s.rule.costs) + '.' +
      (s.verification ? '<br><strong>Verification:</strong> ' +
        CT.fmtInt(s.verification.open_positions_checked || 0) + ' open positions checked; ' +
        CT.fmtInt(s.verification.entry_rule_failures || 0) + ' entry-rule failures; ' +
        CT.fmtInt(s.verification.arithmetic_failures || 0) + ' arithmetic failures.' : '');
  }
  if (rows.length < s.counts.signals) {
    var d = document.createElement('div');
    d.className = 'note';
    d.innerHTML = 'Showing the ' + CT.fmtInt(rows.length) + ' most recent positions. ' +
      'Download the full book of ' + CT.fmtInt(s.counts.signals) + ' via the button above.';
    document.getElementById('rule').insertAdjacentElement('afterend', d);
  }

  var cols = [
    { key: 'fd', label: 'Filed', cls: 'nowrap' },
    { key: 'entry_d', label: 'Entry', cls: 'nowrap', render: function (r) { return r.entry_d ? CT.esc(r.entry_d) : '<span class="b warn">' + CT.esc((r.status||'').replace('_',' ')) + '</span>'; } },
    { key: 'tk', label: 'Ticker', render: function (r) { return '<a class="ticker" href="companies.html?cik=' + CT.esc(r.icik || '') + '">' + CT.esc(r.tk) + '</a>'; } },
    { key: 'co', label: 'Company', render: function (r) { return '<div class="trunc" title="' + CT.esc(r.co) + '">' + CT.esc(CT.titleCase(r.co)) + '</div>'; } },
    { key: 'insider', label: 'Insider', render: function (r) { return '<div class="trunc" title="' + CT.esc(r.insider) + '">' + CT.esc(CT.titleCase(r.insider)) + '</div><div class="sub">' + CT.esc(r.title || r.rel || '') + '</div>'; } },
    { key: 'insider_val', label: 'Insider spent', num: true, render: function (r) { return CT.fmtMoney(r.insider_val); }, title: 'Dollar value the insider reported' },
    { key: 'insider_px', label: 'Their px', num: true, render: function (r) { return CT.fmtPx(r.insider_px); } },
    { key: 'entry_px', label: 'Our entry', num: true, render: function (r) { return CT.fmtPx(r.entry_px); }, title: 'Open of the first session after the filing' },
    { key: 'entry_rule_status', label: 'Entry check', render: function (r) { return '<span class="b ' + (r.entry_rule_status === 'verified' ? 'buy' : r.entry_rule_status === 'invalid' ? 'sell' : 'warn') + '" title="' + CT.esc(r.entry_check || '') + '">' + CT.esc(r.entry_rule_status || '—') + '</span>'; } },
    { key: 'price_src', label: 'Price src', render: function (r) { return '<span class="sub">' + CT.esc(r.price_src || '—') + '</span>'; } },
    { key: 'gap', label: 'Gap', num: true, render: function (r) { return '<span class="' + CT.pctCls(-CT.num(r.gap)) + '">' + CT.fmtPct(r.gap) + '</span>'; }, title: 'Our entry vs the insider price' },
    { key: 'last_px', label: 'Last', num: true, render: function (r) { return CT.fmtPx(r.last_px); } },
    { key: 'pnl', label: 'P&L', num: true, render: function (r) { return '<span class="' + CT.pctCls(r.pnl) + '">' + CT.fmtUSD(r.pnl) + '</span>'; } },
    { key: 'roi', label: 'ROI', num: true, render: function (r) { return '<strong class="' + CT.pctCls(r.roi) + '">' + CT.fmtPct(r.roi) + '</strong>'; } },
    { key: 'acc', label: 'Filing', sort: false, render: function (r) { return r.acc ? '<a href="' + CT.edgar(r.acc, r.icik) + '" target="_blank" rel="noopener">SEC ↗</a>' : ''; } }
  ];
  var t = new CT.Table({ mount: '#tbl', rows: rows, cols: cols, pageSize: 50, sortKey: 'fd', sortDir: -1 });

  function apply() {
    var q = document.getElementById('q').value.trim().toLowerCase();
    var st = document.getElementById('status').value;
    var role = document.getElementById('role').value;
    var win = document.getElementById('win').value;
    t.filter(function (r) {
      if (st && r.status !== st) return false;
      if (role && String(r.rel || '').indexOf(role) < 0) return false;
      if (win) { var v = CT.num(r.roi); if (v === null) return false;
        if (win === 'w' && v <= 0) return false; if (win === 'l' && v >= 0) return false; }
      if (q) {
        var hay = (r.tk + ' ' + r.co + ' ' + r.insider).toLowerCase();
        if (hay.indexOf(q) < 0) return false;
      }
      return true;
    });
  }
  ['q','status','role','win'].forEach(function (id) {
    var el = document.getElementById(id);
    el.addEventListener(id === 'q' ? 'input' : 'change', id === 'q' ? CT.debounce(apply, 200) : apply);
  });
  document.getElementById('csv').addEventListener('click', function () {
    CT.download('ceotrades-paper-view.csv', CT.toCSV(t.view, cols));
  });
}).catch(function (e) { CT.fail(document.getElementById('tbl'), e); });
"""

# ---------------------------------------------------------------------------

TRADES_BODY = """
<div class="page-head">
  <h1>All trades</h1>
  <p>Recent Form 3/4/5 transactions, searchable and sortable. Complete history for every year
     is downloadable below as gzipped CSV — one file per year, straight from the SEC datasets.</p>
</div>

<div class="controls">
  <input type="search" id="q" placeholder="Search ticker, company or insider…">
  <select id="code"><option value="">All codes</option></select>
  <select id="side"><option value="">All types</option><option value="buy">Purchases</option><option value="sell">Sales</option><option value="grant">Grants</option><option value="exercise">Exercises</option><option value="gift">Gifts</option></select>
  <button class="btn" id="csv">Export view (CSV)</button>
</div>
<div class="card tight" id="tbl"></div>

<h2 class="sec">Full history downloads <small>gzipped CSV, one per filing year</small></h2>
<div class="card" id="dl"></div>
"""

TRADES_JS = """
Promise.all([CT.load('data/recent.json').catch(function(){return [];}), CT.load('data/summary.json')])
.then(function (r) {
  var rows = r[0] || [], s = r[1];
  var sel = document.getElementById('code');
  (s.by_code || []).forEach(function (c) {
    var o = document.createElement('option');
    o.value = c.code; o.textContent = c.code + ' — ' + CT.fmtInt(c.n);
    sel.appendChild(o);
  });
  var dl = (s.yearly || []).map(function (y) {
    return '<a class="btn" style="margin:0 8px 8px 0" href="data/csv/trades-' + CT.esc(y.y) +
      '.csv.gz" download>' + CT.esc(y.y) + ' · ' + CT.fmtInt(y.n) + '</a>';
  }).join('');
  document.getElementById('dl').innerHTML = dl ||
    '<div class="empty">No history published yet.</div>';

  var cols = [
    { key: 'fd', label: 'Filed', cls: 'nowrap' },
    { key: 'td', label: 'Traded', cls: 'nowrap' },
    { key: 'tk', label: 'Ticker', render: function (r) { return r.tk ? '<a class="ticker" href="companies.html?cik=' + CT.esc(r.icik || '') + '">' + CT.esc(r.tk) + '</a>' : '<span class="sub">—</span>'; } },
    { key: 'co', label: 'Company', render: function (r) { return '<div class="trunc" title="' + CT.esc(r.co) + '">' + CT.esc(CT.titleCase(r.co)) + '</div>'; } },
    { key: 'in', label: 'Insider', render: function (r) { return '<div class="trunc" title="' + CT.esc(r['in']) + '">' + CT.esc(CT.titleCase(r['in'])) + '</div><div class="sub">' + CT.esc(r.title || r.rel || '') + '</div>'; } },
    { key: 'code', label: 'Code', render: function (r) { return CT.sideBadge(r.code, r.side) + ' <span class="sub">' + CT.esc((r.ct || '').slice(0, 26)) + '</span>'; } },
    { key: 'sec', label: 'Security', render: function (r) { return '<div class="trunc" title="' + CT.esc(r.sec) + '">' + CT.esc(r.sec || '') + '</div>'; } },
    { key: 'sh', label: 'Shares', num: true, render: function (r) { return CT.fmtInt(r.sh); } },
    { key: 'px', label: 'Price', num: true, render: function (r) { return CT.fmtPx(r.px); } },
    { key: 'val', label: 'Value', num: true, render: function (r) { return CT.fmtMoney(r.val); } },
    { key: 'af', label: 'Held after', num: true, render: function (r) { return CT.fmtInt(r.af); } },
    { key: 'acc', label: 'Filing', sort: false, render: function (r) { return r.acc ? '<a href="' + CT.edgar(r.acc, r.icik) + '" target="_blank" rel="noopener">SEC ↗</a>' : ''; } }
  ];
  var t = new CT.Table({ mount: '#tbl', rows: rows, cols: cols, pageSize: 50, sortKey: 'fd', sortDir: -1,
    empty: 'No recent rows. Use the yearly downloads for full history.' });

  function apply() {
    var q = document.getElementById('q').value.trim().toLowerCase();
    var code = document.getElementById('code').value, side = document.getElementById('side').value;
    t.filter(function (r) {
      if (code && r.code !== code) return false;
      if (side && r.side !== side) return false;
      if (q && ((r.tk || '') + ' ' + (r.co || '') + ' ' + (r['in'] || '')).toLowerCase().indexOf(q) < 0) return false;
      return true;
    });
  }
  document.getElementById('q').addEventListener('input', CT.debounce(apply, 200));
  document.getElementById('code').addEventListener('change', apply);
  document.getElementById('side').addEventListener('change', apply);
  document.getElementById('csv').addEventListener('click', function () {
    CT.download('ceotrades-trades-view.csv', CT.toCSV(t.view, cols));
  });
}).catch(function (e) { CT.fail(document.getElementById('tbl'), e); });
"""

# ---------------------------------------------------------------------------

COMPANIES_BODY = """
<div class="page-head">
  <h1>Companies</h1>
  <p>Insider activity aggregated per issuer. Click a company to see its most recent
     transactions. Net flow is code-P purchase dollars minus code-S sale dollars.</p>
</div>
<div class="controls">
  <input type="search" id="q" placeholder="Search company or ticker…">
  <select id="sort">
    <option value="n">Most transactions</option>
    <option value="net_v">Biggest net buying</option>
    <option value="net_vd">Biggest net selling</option>
    <option value="buy_v">Most bought</option>
    <option value="sell_v">Most sold</option>
  </select>
  <button class="btn" id="csv">Export view (CSV)</button>
</div>
<div class="card tight" id="tbl"></div>
<div id="detail"></div>
"""

COMPANIES_JS = """
var COLS = [
  { key: 'tk', label: 'Ticker', render: function (r) { return r.tk ? '<span class="ticker">' + CT.esc(r.tk) + '</span>' : '<span class="sub">—</span>'; } },
  { key: 'co', label: 'Company', render: function (r) { return '<a href="#" data-cik="' + CT.esc(r.cik) + '">' + CT.esc(CT.titleCase(r.co)) + '</a>'; } },
  { key: 'n', label: 'Trades', num: true, render: function (r) { return CT.fmtInt(r.n); } },
  { key: 'ins', label: 'Insiders', num: true, render: function (r) { return CT.fmtInt(r.ins); } },
  { key: 'buy_n', label: 'Buys', num: true, render: function (r) { return CT.fmtInt(r.buy_n); } },
  { key: 'sell_n', label: 'Sells', num: true, render: function (r) { return CT.fmtInt(r.sell_n); } },
  { key: 'buy_v', label: 'Bought', num: true, render: function (r) { return '<span class="pos">' + CT.fmtMoney(r.buy_v) + '</span>'; } },
  { key: 'sell_v', label: 'Sold', num: true, render: function (r) { return '<span class="neg">' + CT.fmtMoney(r.sell_v) + '</span>'; } },
  { key: 'net_v', label: 'Net flow', num: true, render: function (r) { return '<strong class="' + CT.pctCls(r.net_v) + '">' + CT.fmtMoney(r.net_v) + '</strong>'; } },
  { key: 'last', label: 'Last filing', cls: 'nowrap' }
];

CT.load('data/companies.json').then(function (rows) {
  rows = rows || [];
  var t = new CT.Table({ mount: '#tbl', rows: rows, cols: COLS, pageSize: 50, sortKey: 'n', sortDir: -1 });

  function apply() {
    var q = document.getElementById('q').value.trim().toLowerCase();
    t.filter(function (r) {
      return !q || ((r.tk || '') + ' ' + (r.co || '')).toLowerCase().indexOf(q) >= 0;
    });
  }
  document.getElementById('q').addEventListener('input', CT.debounce(apply, 200));
  document.getElementById('sort').addEventListener('change', function () {
    var v = this.value;
    if (v === 'net_vd') t.sort('net_v', 1); else t.sort(v, -1);
  });
  document.getElementById('csv').addEventListener('click', function () {
    CT.download('ceotrades-companies.csv', CT.toCSV(t.view, COLS));
  });

  document.getElementById('tbl').addEventListener('click', function (e) {
    var a = e.target.closest('a[data-cik]');
    if (!a) return;
    e.preventDefault();
    showCompany(a.getAttribute('data-cik'), rows);
  });

  var want = CT.param('cik');
  if (want) showCompany(want, rows);
});

function showCompany(cik, rows) {
  var meta = null;
  for (var i = 0; i < rows.length; i++) if (String(rows[i].cik) === String(cik)) meta = rows[i];
  var bucket = (String(cik).replace(/\\D/g, '') || '0');
  bucket = parseInt(bucket, 10) % 64;
  var box = document.getElementById('detail');
  box.innerHTML = '<h2 class="sec" id="d">' + CT.esc(meta ? CT.titleCase(meta.co) : 'Company ' + cik) +
    (meta && meta.tk ? ' <small>' + CT.esc(meta.tk) + '</small>' : '') + '</h2>' +
    '<div class="card"><div class="empty">Loading transactions…</div></div>';
  document.getElementById('d').scrollIntoView({ behavior: 'smooth' });

  CT.load('data/co/' + bucket + '.json').then(function (all) {
    var list = (all && all[String(cik)]) || [];
    box.innerHTML = '<h2 class="sec" id="d">' + CT.esc(meta ? CT.titleCase(meta.co) : 'Company ' + cik) +
      (meta && meta.tk ? ' <small>' + CT.esc(meta.tk) + '</small>' : '') +
      ' <small>' + CT.fmtInt(list.length) + ' most recent transactions</small></h2>' +
      '<div class="card tight" id="ctbl"></div>';
    new CT.Table({
      mount: '#ctbl', rows: list, pageSize: 25, sortKey: 'fd', sortDir: -1,
      empty: 'No transactions stored for this company.',
      cols: [
        { key: 'fd', label: 'Filed', cls: 'nowrap' },
        { key: 'td', label: 'Traded', cls: 'nowrap' },
        { key: 'in', label: 'Insider', render: function (r) { return CT.esc(CT.titleCase(r['in'])) + '<div class="sub">' + CT.esc(r.title || r.rel || '') + '</div>'; } },
        { key: 'code', label: 'Code', render: function (r) { return CT.sideBadge(r.code, r.side); } },
        { key: 'sec', label: 'Security', render: function (r) { return '<div class="trunc">' + CT.esc(r.sec || '') + '</div>'; } },
        { key: 'sh', label: 'Shares', num: true, render: function (r) { return CT.fmtInt(r.sh); } },
        { key: 'px', label: 'Price', num: true, render: function (r) { return CT.fmtPx(r.px); } },
        { key: 'val', label: 'Value', num: true, render: function (r) { return CT.fmtMoney(r.val); } },
        { key: 'acc', label: 'Filing', sort: false, render: function (r) { return r.acc ? '<a href="' + CT.edgar(r.acc, r.icik) + '" target="_blank" rel="noopener">SEC ↗</a>' : ''; } }
      ]
    });
  }).catch(function (e) { CT.fail(box, e); });
}
"""

# ---------------------------------------------------------------------------

INSIDERS_BODY = """
<div class="page-head">
  <h1>Insiders</h1>
  <p>Every reporting person — directors, officers and 10%+ owners — ranked by filing activity,
     with the dollar value they have bought and sold on the open market.</p>
</div>
<div class="controls">
  <input type="search" id="q" placeholder="Search insider name…">
  <select id="role"><option value="">All roles</option><option>Director</option><option>Officer</option><option>10% Owner</option></select>
  <button class="btn" id="csv">Export view (CSV)</button>
</div>
<div class="card tight" id="tbl"></div>

<h2 class="sec">Reported holdings across issuers <small id="psub"></small></h2>
<p class="note" id="pnote"></p>
<div class="controls">
  <input type="search" id="pq" placeholder="Search insider name…">
  <button class="btn" id="pcsv">Export view (CSV)</button>
  <a class="btn" href="data/insider_portfolios_issuers.csv.gz" download>Per-issuer breakdown (.csv.gz)</a>
</div>
<div class="card tight" id="ptbl"></div>
<div id="pdetail"></div>
"""

INSIDERS_JS = """
var COLS = [
  { key: 'in', label: 'Insider', render: function (r) { return '<strong>' + CT.esc(CT.titleCase(r['in'])) + '</strong>' + (r.title ? '<div class="sub">' + CT.esc(r.title) + '</div>' : ''); } },
  { key: 'rel', label: 'Role', render: function (r) { return '<span class="b acc">' + CT.esc(r.rel) + '</span>'; } },
  { key: 'cos', label: 'Companies', num: true, render: function (r) { return CT.fmtInt(r.cos); } },
  { key: 'n', label: 'Trades', num: true, render: function (r) { return CT.fmtInt(r.n); } },
  { key: 'buy_n', label: 'Buys', num: true, render: function (r) { return CT.fmtInt(r.buy_n); } },
  { key: 'sell_n', label: 'Sells', num: true, render: function (r) { return CT.fmtInt(r.sell_n); } },
  { key: 'buy_v', label: 'Bought', num: true, render: function (r) { return '<span class="pos">' + CT.fmtMoney(r.buy_v) + '</span>'; } },
  { key: 'sell_v', label: 'Sold', num: true, render: function (r) { return '<span class="neg">' + CT.fmtMoney(r.sell_v) + '</span>'; } },
  { key: 'net_v', label: 'Net flow', num: true, render: function (r) { return '<strong class="' + CT.pctCls(r.net_v) + '">' + CT.fmtMoney(r.net_v) + '</strong>'; } },
  { key: 'last', label: 'Last filing', cls: 'nowrap' }
];
CT.load('data/insiders.json').then(function (rows) {
  var t = new CT.Table({ mount: '#tbl', rows: rows || [], cols: COLS, pageSize: 50, sortKey: 'n', sortDir: -1 });
  function apply() {
    var q = document.getElementById('q').value.trim().toLowerCase();
    var role = document.getElementById('role').value;
    t.filter(function (r) {
      if (role && String(r.rel || '').indexOf(role) < 0) return false;
      return !q || String(r['in'] || '').toLowerCase().indexOf(q) >= 0;
    });
  }
  document.getElementById('q').addEventListener('input', CT.debounce(apply, 200));
  document.getElementById('role').addEventListener('change', apply);
  document.getElementById('csv').addEventListener('click', function () {
    CT.download('ceotrades-insiders.csv', CT.toCSV(t.view, COLS));
  });
}).catch(function (e) { CT.fail(document.getElementById('tbl'), e); });

/* ---- per-insider reported portfolio across issuers ---- */
var PCOLS = [
  { key: 'insider', label: 'Insider', render: function (r) { return '<strong>' + CT.esc(CT.titleCase(r.insider)) + '</strong><div class="sub">' + CT.esc(r.title || r.rel || '') + '</div>'; } },
  { key: 'rel', label: 'Role', render: function (r) { return '<span class="b acc">' + CT.esc(r.rel) + '</span>'; } },
  { key: 'issuer_n', label: 'Issuers held', num: true, render: function (r) { return CT.fmtInt(r.issuer_n); } },
  { key: 'reported_shares', label: 'Reported shares', num: true, render: function (r) { return CT.fmtInt(r.reported_shares); } },
  { key: 'priced_value', label: 'Marked value', num: true, render: function (r) { return CT.fmtMoney(r.priced_value); } },
  { key: 'priced_issuer_n', label: 'Priced issuers', num: true, render: function (r) { return CT.fmtInt(r.priced_issuer_n); } },
  { key: 'net_v', label: 'Net flow', num: true, render: function (r) { return '<strong class="' + CT.pctCls(r.net_v) + '">' + CT.fmtMoney(r.net_v) + '</strong>'; } },
  { key: 'overlap', label: 'Buy+sell', render: function (r) { return r.overlap ? '<span class="b sell">both</span>' : ''; } },
  { key: 'last', label: 'Last filing', cls: 'nowrap' }
];
CT.load('data/insider_portfolios.json').then(function (doc) {
  var rows = doc.rows || [];
  var sum = doc.summary || {};
  document.getElementById('pnote').textContent = sum.scope || '';
  document.getElementById('psub').textContent = CT.fmtInt(sum.insiders) + ' insiders · ' +
    CT.fmtInt(sum.with_multiple_issuers) + ' hold more than one issuer · reported value marked at latest closes: ' + CT.fmtMoney(sum.reported_value_priced);
  var t = new CT.Table({ mount: '#ptbl', rows: rows, cols: PCOLS, pageSize: 50, sortKey: 'priced_value', sortDir: -1 });
  var cur = null;
  function show(r) {
    cur = r;
    var el = document.getElementById('pdetail');
    if (!r) { el.innerHTML = ''; return; }
    var h = '<div class="card"><h3>' + CT.esc(CT.titleCase(r.insider)) + ' — reported positions by issuer</h3>' +
      '<div class="sub">Latest SEC as-filed post-transaction common-share counts, marked at each issuer\\'s latest close when available. ' +
      'First filing ' + CT.esc(r.first || '—') + ' · last ' + CT.esc(r.last || '—') + '.</div>' +
      '<table><thead><tr><th>Company</th><th>Ticker</th><th>Trades</th><th>Reported shares</th><th>Marked value</th><th>Latest holding filed</th><th>SEC</th></tr></thead><tbody>';
    (r.issuers || []).forEach(function (e) {
      h += '<tr><td>' + CT.esc(e.co) + '</td><td>' + CT.esc(e.tk) + '</td>' +
        '<td>' + CT.fmtInt(e.n) + ' (' + CT.fmtInt(e.buy_n) + 'b/' + CT.fmtInt(e.sell_n) + 's)</td>' +
        '<td>' + CT.fmtInt(e.reported_shares) + '</td><td>' + CT.fmtMoney(e.holding_value) +
        (e.valuation_status && e.valuation_status !== 'priced' ? ' <span class="sub">' + CT.esc(e.valuation_status) + '</span>' : '') + '</td>' +
        '<td>' + CT.esc(e.latest_holding_fd || e.latest_filing_fd || '') + '</td>' +
        '<td>' + (e.edgar_url ? '<a href="' + e.edgar_url + '" target="_blank" rel="noopener">SEC ↗</a>' : '') + '</td></tr>';
    });
    h += '</tbody></table></div>';
    el.innerHTML = h;
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
  t.mount.addEventListener('click', function (ev) {
    var tr = ev.target.closest('tr[data-i]');
    if (tr) show(t.view[parseInt(tr.getAttribute('data-i'), 10)]);
  });
  function papply() {
    var q = document.getElementById('pq').value.trim().toLowerCase();
    t.filter(function (r) { return !q || String(r.insider || '').toLowerCase().indexOf(q) >= 0; });
  }
  document.getElementById('pq').addEventListener('input', CT.debounce(papply, 200));
  document.getElementById('pcsv').addEventListener('click', function () {
    CT.download('ceotrades-insider-portfolios.csv', CT.toCSV(t.view, PCOLS));
  });
}).catch(function (e) { /* portfolios artifact absent in older builds */ });
"""

# ---------------------------------------------------------------------------

ACTIVITY_BODY = """
<div class="page-head">
  <h1>Insider flows and reported holdings</h1>
  <p>Tracks each insider/company pair in the published target year: code-P buys,
     code-S sells, whether the same insider did both, and the latest as-filed
     post-transaction common-share holding we can quote from SEC fields. Holding
     values are shown only when a real market close is available.</p>
</div>

<div class="grid g4" id="kpis"></div>
<div class="note" id="scope"></div>

<h2 class="sec">Insider/company pairs <small id="sub"></small></h2>
<div class="controls">
  <input type="search" id="q" placeholder="Search insider, company or ticker…">
  <select id="overlap"><option value="">All pairs</option><option value="both">Bought and sold</option><option value="buy">Bought only / has buys</option><option value="sell">Sold only / has sells</option></select>
  <select id="holding"><option value="">All holdings</option><option value="shares">Has reported shares</option><option value="priced">Has priced holding value</option></select>
  <button class="btn" id="csv">Export view (CSV)</button>
  <a class="btn" href="data/insider_activity.csv.gz" download>Full activity (.csv.gz)</a>
</div>
<div class="card tight" id="tbl"></div>
"""

ACTIVITY_JS = """
Promise.all([CT.load('data/insider_activity.json'), CT.load('data/audit.json').catch(function(){return null;})])
.then(function (r) {
  var payload = r[0] || {}, audit = r[1];
  var s = payload.summary || {}, rows = payload.rows || [];
  document.getElementById('kpis').innerHTML =
    CT.statCard('Insider/company pairs', CT.fmtInt(s.insider_company_pairs || 0), 'target ' + (payload.target_year || '—')) +
    CT.statCard('Buy + sell overlap', CT.fmtInt(s.buy_sell_pairs || 0), 'same insider, same issuer') +
    CT.statCard('Reported share holdings', CT.fmtInt(s.with_reported_common_shares || 0), 'latest SEC held-after field') +
    CT.statCard('Priced holding value', CT.fmtMoney(s.reported_holding_value_priced || 0), CT.fmtInt(s.with_priced_holdings || 0) + ' priced pairs');
  var note = CT.esc(s.scope || 'Computed from stored SEC fields only.');
  if (audit && audit.completeness && !audit.completeness.complete) {
    note += '<br><strong>Coverage warning:</strong> ' + CT.esc(audit.completeness.status || 'incomplete_or_unproven') +
      ' — holdings and buy/sell overlap are limited by the incomplete local corpus.';
  }
  document.getElementById('scope').innerHTML = note;
  document.getElementById('sub').textContent = payload.truncated ?
    'showing ' + CT.fmtInt(rows.length) + ' of ' + CT.fmtInt(payload.row_count) + ' pairs' :
    CT.fmtInt(rows.length) + ' pairs';

  var cols = [
    { key: 'buy_sell_overlap', label: 'Buy+Sell', render: function (x) { return x.buy_sell_overlap ? '<span class="b warn">both</span>' : '<span class="b neutral">—</span>'; } },
    { key: 'tk', label: 'Ticker', render: function (x) { return x.tk ? '<span class="ticker">' + CT.esc(x.tk) + '</span>' : '<span class="sub">—</span>'; } },
    { key: 'co', label: 'Company', render: function (x) { return '<div class="trunc" title="' + CT.esc(x.co) + '">' + CT.esc(CT.titleCase(x.co || '')) + '</div>'; } },
    { key: 'insider', label: 'Insider', render: function (x) { return '<div class="trunc" title="' + CT.esc(x.insider) + '">' + CT.esc(CT.titleCase(x.insider || '')) + '</div><div class="sub">' + CT.esc(x.title || x.rel || '') + '</div>'; } },
    { key: 'buy_n', label: 'Buys', num: true, render: function (x) { return CT.fmtInt(x.buy_n); } },
    { key: 'sell_n', label: 'Sells', num: true, render: function (x) { return CT.fmtInt(x.sell_n); } },
    { key: 'buy_v', label: 'Bought', num: true, render: function (x) { return '<span class="pos">' + CT.fmtMoney(x.buy_v) + '</span>'; } },
    { key: 'sell_v', label: 'Sold', num: true, render: function (x) { return '<span class="neg">' + CT.fmtMoney(x.sell_v) + '</span>'; } },
    { key: 'net_v', label: 'Net', num: true, render: function (x) { return '<strong class="' + CT.pctCls(x.net_v) + '">' + CT.fmtMoney(x.net_v) + '</strong>'; } },
    { key: 'reported_common_shares', label: 'Reported shares', num: true, render: function (x) { return CT.fmtInt(x.reported_common_shares); }, title: 'Latest SEC shares-owned-following-transaction common-equity amount, summed across direct/indirect buckets' },
    { key: 'holding_value', label: 'Holding value', num: true, render: function (x) { return CT.fmtMoney(x.holding_value); } },
    { key: 'price_src', label: 'Price src', render: function (x) { return '<span class="sub">' + CT.esc(x.price_src || x.valuation_status || '—') + '</span>'; } },
    { key: 'last', label: 'Last filed', cls: 'nowrap' },
    { key: 'review_links', label: 'SEC review', sort: false, render: function (x) {
        return (x.review_links || []).slice(0, 3).map(function (f) {
          var url = f.edgar_url || CT.edgar(f.acc, f.icik || x.icik);
          return '<a href="' + url + '" target="_blank" rel="noopener">' + CT.esc(f.acc) + ' ↗</a>';
        }).join('<br>');
      } }
  ];
  var t = new CT.Table({ mount: '#tbl', rows: rows, cols: cols, pageSize: 50, sortKey: 'net_v', sortDir: -1,
    empty: 'No insider/company activity rows in the published target-year corpus.' });

  function apply() {
    var q = document.getElementById('q').value.trim().toLowerCase();
    var ov = document.getElementById('overlap').value;
    var hold = document.getElementById('holding').value;
    t.filter(function (x) {
      if (ov === 'both' && !x.buy_sell_overlap) return false;
      if (ov === 'buy' && !(CT.num(x.buy_n) > 0)) return false;
      if (ov === 'sell' && !(CT.num(x.sell_n) > 0)) return false;
      if (hold === 'shares' && CT.num(x.reported_common_shares) === null) return false;
      if (hold === 'priced' && CT.num(x.holding_value) === null) return false;
      if (!q) return true;
      var hay = [x.tk, x.co, x.insider, x.rel, x.title].join(' ').toLowerCase();
      return hay.indexOf(q) >= 0;
    });
  }
  document.getElementById('q').addEventListener('input', CT.debounce(apply, 200));
  document.getElementById('overlap').addEventListener('change', apply);
  document.getElementById('holding').addEventListener('change', apply);
  document.getElementById('csv').addEventListener('click', function () {
    CT.download('ceotrades-insider-activity-view.csv', CT.toCSV(t.view, cols));
  });
}).catch(function (e) { CT.fail(document.getElementById('tbl'), e); });
"""

# ---------------------------------------------------------------------------

ANALYSIS_BODY = """
<div class="page-head">
  <h1>Findings</h1>
  <p>What the forward test says so far. Every figure is computed from the paper book —
     real entry prices at the first open after each filing — not from the insiders' own fills.</p>
</div>
<div class="card"><h3>Headline findings</h3><div id="findings"></div></div>
<div class="card" style="margin-top:16px"><h3>Paper-book verification</h3><div id="verify"></div></div>

<h2 class="sec">Return by holding period <small>median return measured from our entry price</small></h2>
<div class="card tight"><div id="hz"></div></div>

<h2 class="sec">Return by insider role</h2>
<div class="card tight"><div id="role"></div></div>

<h2 class="sec">Return by purchase size <small>how much the insider spent</small></h2>
<div class="card tight"><div id="size"></div></div>

<h2 class="sec">Return by filing year</h2>
<div class="card tight"><div id="year"></div></div>

<h2 class="sec">Best and worst positions</h2>
<div class="grid g2">
  <div class="card tight"><h3>Top 25 winners</h3><div id="best"></div></div>
  <div class="card tight"><h3>Bottom 25 losers</h3><div id="worst"></div></div>
</div>

<h2 class="sec">Transaction code mix <small>the whole dataset, not just paper trades</small></h2>
<div class="card tight"><div id="codes"></div></div>
"""

ANALYSIS_JS = """
var LBL = { r1: '1 session', r5: '1 week (5)', r21: '1 month (21)', r63: '1 quarter (63)', r252: '1 year (252)' };
function statCols(keyLabel, key) {
  return [
    { key: key, label: keyLabel },
    { key: 'n', label: 'Positions', num: true, render: function (r) { return CT.fmtInt(r.n); } },
    { key: 'median', label: 'Median ROI', num: true, render: function (r) { return '<strong class="' + CT.pctCls(r.median) + '">' + CT.fmtPct(r.median) + '</strong>'; } },
    { key: 'mean', label: 'Mean ROI', num: true, render: function (r) { return '<span class="' + CT.pctCls(r.mean) + '">' + CT.fmtPct(r.mean) + '</span>'; } },
    { key: 'win', label: 'Win rate', num: true, render: function (r) { return r.win === null ? '—' : (r.win * 100).toFixed(1) + '%'; } },
    { key: 'p25', label: 'p25', num: true, render: function (r) { return CT.fmtPct(r.p25); } },
    { key: 'p75', label: 'p75', num: true, render: function (r) { return CT.fmtPct(r.p75); } }
  ];
}
function posCols() {
  return [
    { key: 'tk', label: 'Ticker', render: function (r) { return '<span class="ticker">' + CT.esc(r.tk) + '</span>'; } },
    { key: 'insider', label: 'Insider', render: function (r) { return '<div class="trunc">' + CT.esc(CT.titleCase(r.insider)) + '</div>'; } },
    { key: 'fd', label: 'Filed', cls: 'nowrap' },
    { key: 'entry_px', label: 'Entry', num: true, render: function (r) { return CT.fmtPx(r.entry_px); } },
    { key: 'last_px', label: 'Last', num: true, render: function (r) { return CT.fmtPx(r.last_px); } },
    { key: 'roi', label: 'ROI', num: true, render: function (r) { return '<strong class="' + CT.pctCls(r.roi) + '">' + CT.fmtPct(r.roi) + '</strong>'; } }
  ];
}

Promise.all([CT.load('data/paper/summary.json'), CT.load('data/summary.json')])
.then(function (r) {
  var p = r[0], s = r[1];
  document.getElementById('findings').innerHTML = (p.findings || []).map(function (f, i) {
    return '<div class="finding"><div class="n">' + (i + 1) + '</div><div>' + CT.esc(f) + '</div></div>';
  }).join('') || '<div class="empty">No findings yet.</div>';
  var vf = p.verification || {};
  var srcs = (vf.price_sources || []).map(function (x) { return CT.esc(x.source) + ': ' + CT.fmtInt(x.n); }).join(' · ');
  document.getElementById('verify').innerHTML =
    '<p><strong>Entry rule failures:</strong> ' + CT.fmtInt(vf.entry_rule_failures || 0) +
    ' · <strong>Arithmetic failures:</strong> ' + CT.fmtInt(vf.arithmetic_failures || 0) +
    ' · <strong>Open positions checked:</strong> ' + CT.fmtInt(vf.open_positions_checked || 0) + '</p>' +
    '<p class="sub">' + CT.esc(vf.line_by_line_review || '') + '</p>' +
    '<p class="sub">Price sources: ' + (srcs || '—') + '</p>';

  var hz = Object.keys(LBL).filter(function (k) { return p.horizons && p.horizons[k]; })
    .map(function (k) { var o = Object.assign({}, p.horizons[k]); o.h = LBL[k]; return o; });
  new CT.Table({ mount: '#hz', rows: hz, cols: statCols('Holding period', 'h'), pageSize: 10, sortKey: null });
  new CT.Table({ mount: '#role', rows: p.by_role || [], cols: statCols('Role', 'role'), pageSize: 15, sortKey: 'n' });
  new CT.Table({ mount: '#size', rows: p.by_size || [], cols: statCols('Insider spend', 'size'), pageSize: 15, sortKey: 'n' });
  new CT.Table({ mount: '#year', rows: p.by_year || [], cols: statCols('Filing year', 'y'), pageSize: 30, sortKey: 'y', sortDir: 1 });
  new CT.Table({ mount: '#best', rows: p.best || [], cols: posCols(), pageSize: 25, sortKey: 'roi', sortDir: -1 });
  new CT.Table({ mount: '#worst', rows: p.worst || [], cols: posCols(), pageSize: 25, sortKey: 'roi', sortDir: 1 });

  var total = (s.by_code || []).reduce(function (a, c) { return a + c.n; }, 0) || 1;
  new CT.Table({
    mount: '#codes', rows: s.by_code || [], pageSize: 25, sortKey: 'n', sortDir: -1,
    cols: [
      { key: 'code', label: 'Code', render: function (r) { return '<span class="b neutral">' + CT.esc(r.code) + '</span>'; } },
      { key: 'n', label: 'Transactions', num: true, render: function (r) { return CT.fmtInt(r.n); } },
      { key: 'v', label: 'Reported value', num: true, render: function (r) { return CT.fmtMoney(r.v); } },
      { key: 'pct', label: 'Share', num: true, sort: false, render: function (r) {
          var w = (r.n / total * 100);
          return '<div class="bar" title="' + w.toFixed(2) + '%"><i style="width:' +
            Math.max(2, w).toFixed(1) + '%;background:var(--acc)"></i></div>'; } }
    ]
  });
}).catch(function (e) { CT.fail(document.getElementById('findings'), e); });
"""

# ---------------------------------------------------------------------------

IRREGULARITIES_BODY = """
<div class="page-head">
  <h1>Irregularities</h1>
  <p>Automated review flags generated from the SEC-derived local store. These are not legal
     conclusions. They identify coverage gaps, data-quality issues and transaction patterns that
     should be reviewed against the linked SEC accessions before publication or investment use.</p>
</div>

<div class="grid g4" id="auditkpis"></div>
<div class="note" id="auditnote" style="display:none"></div>

<h2 class="sec">Flagged items <small id="flagsub"></small></h2>
<div class="controls">
  <input type="search" id="q" placeholder="Search ticker, company, insider or rule…">
  <select id="sev"><option value="">All severities</option><option>High</option><option>Medium</option><option>Low</option><option>Informational</option></select>
  <button class="btn" id="csv">Export view (CSV)</button>
</div>
<div class="card tight" id="tbl"></div>

<h2 class="sec">Details</h2>
<div id="details"></div>
"""

IRREGULARITIES_JS = """
Promise.all([CT.load('data/irregularities.json').catch(function(){return [];}), CT.load('data/audit.json').catch(function(){return null;})])
.then(function (r) {
  var rows = r[0] || [], audit = r[1];
  var k = document.getElementById('auditkpis');
  if (audit && audit.counts) {
    var c = audit.counts, comp = audit.completeness || {}, integ = audit.integrity || {};
    k.innerHTML =
      CT.statCard('Audit status', comp.complete ? 'Complete candidate' : 'Incomplete / unproven', 'target ' + (audit.target_year || '—'), comp.complete ? 'buy' : 'sell') +
      CT.statCard('Rows audited', CT.fmtInt(c.rows), CT.fmtInt(c.filings) + ' SEC accessions') +
      CT.statCard('Row issues', CT.fmtInt(integ.row_issues || 0), 'mechanical checks') +
      CT.statCard('Review flags', CT.fmtInt(rows.length), 'automated, not legal conclusions');
    if (comp.blockers && comp.blockers.length) {
      var n = document.getElementById('auditnote');
      n.style.display = 'block';
      n.innerHTML = '<strong>Coverage note:</strong> ' + comp.blockers.map(CT.esc).join(' ');
    }
  } else {
    k.innerHTML = CT.statCard('Review flags', CT.fmtInt(rows.length), 'audit artifact unavailable');
  }

  document.getElementById('flagsub').textContent = rows.length ? CT.fmtInt(rows.length) + ' generated flags' : '';
  var cols = [
    { key: 'id', label: 'ID', render: function (x) { return '<strong>' + CT.esc(x.id) + '</strong>'; } },
    { key: 'severity', label: 'Severity', render: function (x) { return '<span class="b ' + (x.severity === 'High' ? 'sell' : x.severity === 'Medium' ? 'warn' : 'neutral') + '">' + CT.esc(x.severity) + '</span>'; } },
    { key: 'tk', label: 'Ticker', render: function (x) { return x.tk ? '<span class="ticker">' + CT.esc(x.tk) + '</span>' : '<span class="sub">—</span>'; } },
    { key: 'co', label: 'Company' },
    { key: 'category', label: 'Category' },
    { key: 'fd', label: 'Filed' },
    { key: 'total_value', label: 'Value', num: true, render: function (x) { return CT.fmtMoney(x.total_value); } },
    { key: 'accessions', label: 'SEC', sort: false, render: function (x) {
        var fs = x.filings || [];
        if (!fs.length && x.accessions) fs = x.accessions.map(function(a){ return {acc:a, icik:x.icik}; });
        return fs.slice(0, 4).map(function (f) { return '<a href="' + CT.edgar(f.acc, f.icik || x.icik) + '" target="_blank" rel="noopener">' + CT.esc(f.acc) + ' ↗</a>'; }).join('<br>');
      } }
  ];
  var t = new CT.Table({ mount: '#tbl', rows: rows, cols: cols, pageSize: 25, sortKey: 'id', sortDir: 1,
    empty: 'No automated review flags generated.' });

  function renderDetails(list) {
    document.getElementById('details').innerHTML = list.map(function (x) {
      var ev = (x.evidence || []).map(function (e) { return '<li>' + CT.esc(e) + '</li>'; }).join('');
      var filings = (x.filings || []).slice(0, 8).map(function (f) {
        return '<a class="pill" href="' + CT.edgar(f.acc, f.icik || x.icik) + '" target="_blank" rel="noopener">SEC ' + CT.esc(f.acc) + '</a>';
      }).join(' ');
      return '<div class="card" style="margin-top:14px">' +
        '<h3 style="margin-top:0">' + CT.esc(x.id + ' · ' + x.category) + '</h3>' +
        '<p><span class="b ' + (x.severity === 'High' ? 'sell' : x.severity === 'Medium' ? 'warn' : 'neutral') + '">' + CT.esc(x.severity) + '</span> ' +
        (x.tk ? '<span class="ticker">' + CT.esc(x.tk) + '</span> ' : '') + CT.esc(x.co || '') + '</p>' +
        '<p><strong>Summary:</strong> ' + CT.esc(x.summary || '') + '</p>' +
        '<p class="sub">' + CT.esc(x.details || '') + '</p>' +
        '<p><strong>Rule:</strong> <span class="sub">' + CT.esc(x.rule || '') + '</span></p>' +
        '<div>' + filings + '</div>' +
        '<div class="note"><strong>Evidence from stored SEC fields:</strong><ul>' + ev + '</ul></div>' +
      '</div>';
    }).join('') || '<div class="card"><div class="empty">No details.</div></div>';
  }
  renderDetails(rows.slice(0, 50));

  function apply() {
    var q = document.getElementById('q').value.trim().toLowerCase();
    var sev = document.getElementById('sev').value;
    t.filter(function (x) {
      if (sev && x.severity !== sev) return false;
      if (!q) return true;
      var hay = [x.id, x.tk, x.co, x.category, x.summary, x.rule, (x.insiders || []).join(' ')].join(' ').toLowerCase();
      return hay.indexOf(q) >= 0;
    });
    renderDetails(t.view.slice(0, 50));
  }
  document.getElementById('q').addEventListener('input', CT.debounce(apply, 200));
  document.getElementById('sev').addEventListener('change', apply);
  document.getElementById('csv').addEventListener('click', function () {
    CT.download('ceotrades-irregularities.csv', CT.toCSV(t.view, cols));
  });
}).catch(function (e) { CT.fail(document.getElementById('tbl'), e); });
"""

# ---------------------------------------------------------------------------

ABOUT_BODY = """
<div class="page-head">
  <h1>Methodology</h1>
  <p>How CEOTrades collects, organises and forward-tests insider trades — and what the
     numbers do and do not mean.</p>
</div>

<div class="card">
  <h3>Where the data comes from</h3>
  <p>Under Section 16 of the Securities Exchange Act of 1934, every director, executive officer
     and beneficial owner of more than 10% of a registered class of equity must report their
     holdings and transactions to the SEC on <strong>Form 3</strong> (initial), <strong>Form 4</strong>
     (changes, due within two business days) and <strong>Form 5</strong> (annual catch-up).</p>
  <p>Historical data is loaded from the SEC's official
     <a href="https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets">Insider
     Transactions Data Sets</a> — quarterly archives covering January 2006 to the present,
     extracted directly from the ownership XML as filed. New filings are picked up from the
     EDGAR daily index. Both paths write the same canonical schema, and rows are de-duplicated
     by accession number so nightly and quarterly sources never double-count.</p>
</div>

<div class="card" style="margin-top:16px">
  <h3>The paper-trading rule</h3>
  <p>The simulation is deliberately simple and strictly free of hindsight:</p>
  <dl class="dict">
    <dt>Signal</dt><dd>A Form 4/5 <em>non-derivative</em> transaction with code <code>P</code>
      (open-market or private purchase) of common stock or an ADR, with both a share count and a
      price reported. Multiple lots in the same filing for the same ticker are combined into one
      signal at their share-weighted average price, so a single filing never becomes several trades.</dd>
    <dt>Position size</dt><dd>A fixed <strong>$10,000</strong> notional, fractional shares allowed.</dd>
    <dt>Entry</dt><dd>The regular-session <strong>open of the first trading day strictly after the
      filing date</strong>. This is the earliest price a member of the public could realistically have
      acted on. The insider's own fill price is <em>never</em> used as our entry.</dd>
    <dt>Exit</dt><dd>None. Positions stay open — this is a forward test, marked to the latest close.</dd>
    <dt>Horizons</dt><dd>Returns are also measured 1, 5, 21, 63 and 252 trading sessions after entry.
      When a horizon has not elapsed yet it is reported as blank, never estimated.</dd>
    <dt>Gap</dt><dd>Our entry price divided by the insider's average price, minus one. A positive gap
      means the public follower paid more than the insider did.</dd>
    <dt>Costs</dt><dd>No commissions, spreads, slippage or taxes are modelled. Real-world results
      would be lower.</dd>
    <dt>Prices</dt><dd>Daily bars from Yahoo Finance, with Stooq as a fallback. Series are
      split-adjusted; insider-reported prices are as-filed and are not adjusted.</dd>
  </dl>
</div>

<div class="card" style="margin-top:16px">
  <h3>Honest caveats</h3>
  <ul>
    <li><strong>Not investment advice.</strong> This is a research record, not a recommendation.</li>
    <li><strong>Survivorship and delisting.</strong> Tickers that no longer trade may have no price
      history available; those signals are reported as <em>no price</em> rather than dropped silently,
      so you can see exactly how much of the sample is missing.</li>
    <li><strong>Ticker reuse.</strong> Symbols get recycled between companies over two decades. Price
      series are matched on the ticker as filed, so very old positions in reused symbols can be wrong.</li>
    <li><strong>Split adjustment mismatch.</strong> Insider prices are as-filed while market bars are
      split-adjusted, so the <em>gap</em> column can look extreme around historical splits.</li>
    <li><strong>Open positions only.</strong> Because nothing is ever sold, aggregate ROI is a
      buy-and-hold figure that mixes positions of very different ages.</li>
    <li><strong>As-filed data.</strong> The SEC publishes filings without correcting them. Fat-fingered
      share counts and prices exist in the source and are preserved here rather than quietly edited.</li>
  </ul>
</div>

<div class="card" style="margin-top:16px">
  <h3>Insider flow and reported holdings</h3>
  <p>The Insider flows page groups rows by reporting-owner CIK and issuer CIK to show whether
     the same insider both bought and sold the same issuer in the published target year.</p>
  <p>Reported holding size comes from the SEC <code>sharesOwnedFollowingTransaction</code>
     / <code>SHRS_OWND_FOLWNG_TRANS</code> field on the latest non-derivative common-equity row
     for each direct/indirect ownership bucket. It is not a reconstructed brokerage account,
     and it does not include unfiled holdings or assets outside the issuer.</p>
</div>

<div class="card" style="margin-top:16px">
  <h3>Automation</h3>
  <p>A GitHub Actions workflow runs nightly: it fetches target-year filings from official SEC
     bulk archives plus the EDGAR daily index, refreshes prices when real market data is reachable,
     re-runs the simulation, rebuilds every JSON artifact and commits the result. There is no
     manual data entry step.</p>
  <p>Hard-coded trade lists and synthetic price paths are intentionally disabled. If a SEC or
     market-data source is unavailable, the affected rows or paper positions are marked incomplete
     or <code>no_price</code>; they are never filled by interpolation or estimates.</p>
  <div id="gen" class="sub"></div>
</div>

<div class="card" style="margin-top:16px">
  <h3>Data dictionary</h3>
  <dl class="dict">
    <dt>Filed / fd</dt><dd>Date the filing became public on EDGAR. Drives the paper entry.</dd>
    <dt>Traded / td</dt><dd>Date the insider actually transacted, as reported.</dd>
    <dt>Code</dt><dd>SEC transaction code. <code>P</code> purchase, <code>S</code> sale,
      <code>A</code> grant, <code>M</code> option exercise, <code>F</code> tax withholding,
      <code>G</code> gift, <code>D</code> disposition to issuer.</dd>
    <dt>Shares / Price / Value</dt><dd>As reported on the form; value is shares × price.</dd>
    <dt>Held after</dt><dd>Shares beneficially owned following the transaction.</dd>
    <dt>Net flow</dt><dd>Code-P purchase dollars minus code-S sale dollars.</dd>
  </dl>
</div>
"""

ABOUT_JS = """
CT.load('data/summary.json').then(function (s) {
  document.getElementById('gen').textContent =
    'Dataset last built ' + s.generated + ' · ' + CT.fmtInt(s.counts.trades) +
    ' transactions from ' + (s.range.from || '—') + ' to ' + (s.range.to || '—') + '.';
}).catch(function () {});
"""

# ---------------------------------------------------------------------------

BACKTEST_BODY = """
<div class="page-head">
  <h1>Backtest — verified entries and exits</h1>
  <p>For every tracked open-market insider purchase (Form 4/5 code <code>P</code>), we simulate the
     same <strong>$10,000</strong> rule over the <em>historical</em> record: buy at the
     regular-session <strong>open of the first trading day strictly after the filing</strong>,
     and exit at the <strong>close 252 sessions later</strong> (~one trading year). Entry and
     exit prices/dates are real observed bars — nothing is estimated. Positions younger than
     252 sessions stay open; delisted names exit at their last observed close and are flagged.</p>
</div>

<div class="grid g4" id="kpis"></div>
<div id="rule" class="note" style="display:none"></div>

<h2 class="sec">Round trips</h2>
<div class="controls">
  <input type="search" id="q" placeholder="Search ticker, company or insider…">
  <select id="status"><option value="">All exits</option><option value="exited_252">252-session exit</option><option value="exit_last_observed">Last observed (delisted)</option><option value="open">Still open</option><option value="no_price">No price</option></select>
  <select id="win"><option value="">All results</option><option value="w">Winners</option><option value="l">Losers</option></select>
  <button class="btn" id="csv">Export view (CSV)</button>
  <a class="btn" href="data/backtest/positions.csv" download>Full backtest (CSV)</a>
</div>
<div class="card tight" id="tbl"></div>
<div class="note" id="missing" style="display:none"></div>
"""

BACKTEST_JS = """
Promise.all([
  CT.load('data/backtest/summary.json').catch(function(){return null;}),
  CT.load('data/backtest/positions.json').catch(function(){return [];})
]).then(function (r) {
  var s = r[0], rows = r[1] || [];
  if (!s) {
    document.getElementById('missing').style.display = 'block';
    document.getElementById('missing').innerHTML =
      'The historical backtest is generated by the <code>backfill+backtest</code> workflow over the ' +
      'widened SEC archive. The nightly run adds it once the 2006-present corpus is priced; forward ' +
      'positions are always in the <a href="paper.html">Paper book</a>.';
    document.getElementById('tbl').innerHTML = '<div class="empty">No backtest book in this build yet.</div>';
    return;
  }
  var c = s.counts || {};
  document.getElementById('kpis').innerHTML =
    CT.statCard('Buy signals', CT.fmtInt(c.signals), 'one paper trade per SEC accession+ticker') +
    CT.statCard('Verified entries', CT.fmtInt(c.verified_entry), 'real next-session open') +
    CT.statCard('Exited', CT.fmtInt(c.exited), CT.fmtInt(c.exited_252) + ' at +252 sessions' + (c.exit_last_observed_delisted ? ', ' + CT.fmtInt(c.exit_last_observed_delisted) + ' delisted' : '')) +
    CT.statCard('Mean realised ROI', CT.fmtPct((s.realised_exit_roi||{}).mean), 'win rate ' + CT.fmtPct((s.realised_exit_roi||{}).win), CT.pctCls((s.realised_exit_roi||{}).mean));

  var n = document.getElementById('rule');
  n.style.display = 'block';
  n.innerHTML = '<strong>Entry:</strong> ' + CT.esc((s.rule||{}).entry || '') + '.<br>' +
    '<strong>Exit:</strong> ' + CT.esc((s.rule||{}).exit || '') + '.<br>' +
    '<strong>Prices:</strong> ' + CT.esc((s.rule||{}).prices || '') + '. ' +
    'Coverage: ' + CT.fmtInt(c.no_price_or_pending) + ' signals have no verified entry yet (no price / pending) — counted, never dropped.';

  var cols = [
    { key: 'fd', label: 'Filed', cls: 'nowrap' },
    { key: 'tk', label: 'Ticker', render: function (r) { return r.tk ? '<a class="ticker" href="companies.html?cik=' + CT.esc(r.icik || '') + '">' + CT.esc(r.tk) + '</a>' : '<span class="sub">—</span>'; } },
    { key: 'co', label: 'Company', render: function (r) { return '<div class="trunc" title="' + CT.esc(r.co||'') + '">' + CT.esc(CT.titleCase(r.co||'')) + '</div>'; } },
    { key: 'insider', label: 'Insider', render: function (r) { return '<div class="trunc" title="' + CT.esc(r.insider||'') + '">' + CT.esc(CT.titleCase(r.insider||'')) + '</div>'; } },
    { key: 'entry_d', label: 'Entry', cls: 'nowrap', render: function (r) { return r.entry_d ? CT.esc(r.entry_d) + ' <span class="sub">@' + CT.fmtPx(r.entry_px) + '</span>' : '<span class="b warn">no price</span>'; } },
    { key: 'exit_d', label: 'Exit', cls: 'nowrap', render: function (r) { return r.exit_d ? CT.esc(r.exit_d) + ' <span class="sub">@' + CT.fmtPx(r.exit_px) + '</span>' : (r.exit_status === 'open' ? '<span class="sub">open</span>' : '—'); } },
    { key: 'exit_status', label: 'Exit type', render: function (r) {
        var m = { exited_252: ['buy','+252 close'], exit_last_observed: ['warn','last obs.'], open: ['','open'], no_price: ['warn','no price'] };
        var x = m[r.exit_status || r.entry_rule_status] || ['', r.exit_status || ''];
        return '<span class="b ' + x[0] + '" title="' + CT.esc(r.review_note || '') + '">' + x[1] + '</span>';
      } },
    { key: 'exit_roi', label: 'Realised ROI', num: true, render: function (r) {
        if (r.roi_review) return '<span class="b warn" title="' + CT.esc(r.roi_review_reason || r.review_note || '') + '">review</span>';
        return r.exit_roi === null || r.exit_roi === undefined ? '<span class="sub">—</span>' : '<strong class="' + CT.pctCls(r.exit_roi) + '">' + CT.fmtPct(r.exit_roi) + '</strong>';
      } },
    { key: 'exit_pnl', label: 'Realised P&L', num: true, render: function (r) { return r.exit_pnl === null || r.exit_pnl === undefined ? '<span class="sub">—</span>' : '<span class="' + CT.pctCls(r.exit_pnl) + '">' + CT.fmtUSD(r.exit_pnl) + '</span>'; } },
    { key: 'roi_open', label: 'Open ROI', num: true, render: function (r) { return r.roi_open === null || r.roi_open === undefined ? '—' : '<span class="' + CT.pctCls(r.roi_open) + '">' + CT.fmtPct(r.roi_open) + '</span>'; } },
    { key: 'price_src', label: 'Price src', render: function (r) { return '<span class="sub">' + CT.esc(r.price_src || '—') + '</span>'; } },
    { key: 'acc', label: 'Filing', sort: false, render: function (r) { return r.acc ? '<a href="' + CT.edgar(r.acc, r.icik) + '" target="_blank" rel="noopener">SEC ↗</a>' : ''; } }
  ];
  var t = new CT.Table({ mount: '#tbl', rows: rows, cols: cols, pageSize: 50, sortKey: 'fd', sortDir: -1 });

  function apply() {
    var q = document.getElementById('q').value.trim().toLowerCase();
    var st = document.getElementById('status').value;
    var win = document.getElementById('win').value;
    t.filter(function (r) {
      var rs = r.exit_status || (r.entry_rule_status === 'verified' ? 'open' : 'no_price');
      if (st && rs !== st) return false;
      if (win) { var v = CT.num(r.exit_roi); if (v === null) return false;
        if (win === 'w' && v <= 0) return false; if (win === 'l' && v >= 0) return false; }
      if (q) { var hay = (r.tk + ' ' + r.co + ' ' + r.insider).toLowerCase(); if (hay.indexOf(q) < 0) return false; }
      return true;
    });
  }
  ['q','status','win'].forEach(function (id) {
    var el = document.getElementById(id);
    el.addEventListener(id === 'q' ? 'input' : 'change', id === 'q' ? CT.debounce(apply, 200) : apply);
  });
  document.getElementById('csv').addEventListener('click', function () {
    CT.download('ceotrades-backtest-view.csv', CT.toCSV(t.view, cols));
  });
}).catch(function (e) { CT.fail(document.getElementById('tbl'), e); });
"""

# ---------------------------------------------------------------------------

SPECS = [
    ("index.html", "index", "Dashboard",
     "Every SEC insider trade, collected and forward-tested with simulated $10,000 positions at real market prices.",
     INDEX_BODY, INDEX_JS),
    ("paper.html", "paper", "Paper book",
     "Every simulated $10,000 insider-follow position with real entry prices, gap and ROI.",
     PAPER_BODY, PAPER_JS),
    ("backtest.html", "backtest", "Backtest",
     "Verified entry and exit prices/dates for every tracked insider buy, with realised round-trip ROI.",
     BACKTEST_BODY, BACKTEST_JS),
    ("trades.html", "trades", "Trades",
     "Searchable tape of SEC Form 3/4/5 insider transactions with full history downloads.",
     TRADES_BODY, TRADES_JS),
    ("companies.html", "companies", "Companies",
     "Insider buying and selling aggregated by public company.",
     COMPANIES_BODY, COMPANIES_JS),
    ("insiders.html", "insiders", "Insiders",
     "Directors, officers and 10% owners ranked by insider trading activity.",
     INSIDERS_BODY, INSIDERS_JS),
    ("activity.html", "activity", "Insider flows",
     "Buyer/seller overlap and reported post-transaction holding estimates by insider and issuer.",
     ACTIVITY_BODY, ACTIVITY_JS),
    ("analysis.html", "analysis", "Findings",
     "Forward-test results: ROI by role, purchase size, holding period and year.",
     ANALYSIS_BODY, ANALYSIS_JS),
    ("irregularities.html", "irregularities", "Irregularities",
     "Automated review flags for SEC insider-trade data quality and transaction patterns.",
     IRREGULARITIES_BODY, IRREGULARITIES_JS),
    ("about.html", "about", "About",
     "Methodology, paper-trading rules, caveats and data dictionary for CEOTrades.",
     ABOUT_BODY, ABOUT_JS),
]


def main() -> int:
    for fn, key, title, desc, body, js in SPECS:
        html = shell(key, title, desc, body.strip(), js.strip())
        with open(os.path.join(ROOT, fn), "w", encoding="utf-8") as f:
            f.write(html)
        print(f"wrote {fn} ({len(html):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
