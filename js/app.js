
"use strict";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

async function loadJSON(url) {
  const r = await fetch(url, { cache: "no-cache" });
  if (!r.ok) throw new Error(url + " -> " + r.status);
  return r.json();
}
async function loadJSONOpt(url) {
  try { return await loadJSON(url); } catch (e) { return null; }
}
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtNum(n) {
  if (n === null || n === undefined || n === "") return "—";
  return Number(n).toLocaleString("en-US", { maximumFractionDigits: 2 });
}
function fmtMoney(n, signed) {
  if (n === null || n === undefined || isNaN(n)) return "—";
  const neg = n < 0, a = Math.abs(n);
  let s;
  if (a >= 1e9) s = "$" + (a / 1e9).toFixed(2) + "B";
  else if (a >= 1e6) s = "$" + (a / 1e6).toFixed(1) + "M";
  else if (a >= 1e3) s = "$" + (a / 1e3).toFixed(0) + "K";
  else s = "$" + a.toFixed(0);
  if (neg) s = "−" + s;
  else if (signed && n > 0) s = "+" + s;
  return s;
}
function fmtDate(d) {
  if (!d) return "—";
  return d; // ISO already
}
function fmtPct(n) {
  if (n === null || n === undefined || n === "" || isNaN(n)) return "—";
  const s = (Number(n) * 100).toFixed(2) + "%";
  return Number(n) > 0 ? "+" + s : s;
}
function fmtPx(n) {
  if (n === null || n === undefined || n === "" || isNaN(n)) return "—";
  return "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
}
function statusPill(st) {
  if (st === "open") return '<span class="pill buy">open</span>';
  if (st === "awaiting_entry") return '<span class="pill pending">awaiting open</span>';
  return '<span class="pill dead">no price</span>';
}
function pctCell(n) {
  if (n === null || n === undefined || isNaN(n)) return '<td class="num">—</td>';
  const cls = n > 0 ? "pos" : n < 0 ? "neg" : "";
  return `<td class="num ${cls}">${fmtPct(n)}</td>`;
}
function sideClass(code) {
  if (code === "P") return "buy";
  if (code === "S") return "sell";
  return "neutral";
}
function codePill(code, text) {
  return `<span class="pill code ${sideClass(code)}" title="${esc(text || "")}">${esc(code || "?")}</span>`;
}
function companyCell(r) {
  const tk = r.tk ? `<span class="badge">${esc(r.tk)}</span>` : "";
  const sub = r.tk ? "" : '<div class="sub">no ticker filed</div>';
  return `<td><span class="ticker">${esc(r.co)}</span>${tk}${sub}</td>`;
}
function tradeRow(r) {
  const link = r.acc ? `<a href="${`https://www.sec.gov/Archives/edgar/data/${parseInt(r.acc.slice(0, 10))}/${r.acc.replace(/-/g, "")}-index.htm`}" target="_blank" rel="noopener" title="View filing on SEC EDGAR">↗</a>` : "";
  return `<tr>
    <td class="num" title="Filed">${fmtDate(r.fd)}</td>
    <td class="num" title="Transaction date">${fmtDate(r.td)}</td>
    ${companyCell(r)}
    <td>${esc(r.in)}<div class="sub">${esc([r.rel, r.title].filter(Boolean).join(" · "))}</div></td>
    <td>${codePill(r.code, r.ct)}</td>
    <td>${esc(r.sec)}${r.der ? `<div class="sub">derivative${r.under ? " → " + esc(r.under) : ""}</div>` : ""}</td>
    <td class="num">${fmtNum(r.sh)}</td>
    <td class="num">${r.px ? "$" + fmtNum(r.px) : "—"}</td>
    <td class="num"><b>${r.val ? fmtMoney(r.val) : "—"}</b></td>
    <td class="num">${fmtNum(r.af)}</td>
    <td style="text-align:center">${r.di === "I" ? "I" : "D"} ${link}</td>
  </tr>`;
}

function makeTable(el, cols, rows, opts) {
  // cols: [{key,label,cls,render(row)}...]
  opts = opts || {};
  const state = { sortKey: opts.sortKey || null, sortDir: -1, page: 1, per: opts.per || 25, filter: opts.filter || (() => true) };
  el.innerHTML = "";
  const wrap = document.createElement("div");
  wrap.className = "table-scroll";
  el.appendChild(wrap);
  const note = document.createElement("div");
  note.className = "count-note";
  el.appendChild(note);
  const pager = document.createElement("div");
  pager.className = "pager";
  el.appendChild(pager);

  function applyFilter(rows) { return rows.filter(state.filter); }
  function applySort(rows) {
    if (!state.sortKey) return rows;
    const k = state.sortKey, dir = state.sortDir;
    return rows.slice().sort((a, b) => {
      let x = a[k], y = b[k];
      if (x == null) x = dir === 1 ? Infinity : -Infinity;
      if (y == null) y = dir === 1 ? -Infinity : Infinity;
      if (typeof x === "string" || typeof y === "string")
        return String(x).localeCompare(String(y)) * dir;
      return (x - y) * dir;
    });
  }
  function render() {
    const frows = applySort(applyFilter(rows));
    const pages = Math.max(1, Math.ceil(frows.length / state.per));
    if (state.page > pages) state.page = pages;
    const slice = frows.slice((state.page - 1) * state.per, state.page * state.per);
    let html = "<table><thead><tr>";
    for (const c of cols)
      html += `<th class="${c.cls || ""} ${c.key ? "sortable" : ""}" data-key="${c.key || ""}">${c.label}${state.sortKey === c.key ? (state.sortDir === 1 ? " ▲" : " ▼") : ""}</th>`;
    html += "</tr></thead><tbody>";
    for (const r of slice) html += (opts.row || tradeRow)(r, cols);
    html += "</tbody></table>";
    wrap.innerHTML = slice.length ? html : '<div class="empty">No trades match the current filters.</div>';
    note.textContent = `${frows.length.toLocaleString()} of ${rows.length.toLocaleString()} trades shown`;
    pager.innerHTML = "";
    if (pages > 1) {
      const b = (label, pg, dis) => {
        const btn = document.createElement("button");
        btn.textContent = label; btn.disabled = !!dis;
        btn.onclick = () => { state.page = pg; render(); el.scrollIntoView({ block: "start" }); };
        return btn;
      };
      pager.appendChild(b("← Prev", state.page - 1, state.page === 1));
      const info = document.createElement("span");
      info.textContent = `page ${state.page} / ${pages}`;
      pager.appendChild(info);
      pager.appendChild(b("Next →", state.page + 1, state.page === pages));
    }
    $$(".sortable", wrap).forEach(th => th.onclick = () => {
      const k = th.dataset.key;
      if (!k) return;
      if (state.sortKey === k) state.sortDir *= -1;
      else { state.sortKey = k; state.sortDir = -1; }
      render();
    });
  }
  render();
  return { setFilter(fn) { state.filter = fn; state.page = 1; render(); }, setRows(rs) { rows = rs; state.page = 1; render(); } };
}

function barList(el, items, opts) {
  // items: [{label, value, sub, kind}] kind: buy|sell|acc
  opts = opts || {};
  const max = Math.max(...items.map(i => Math.abs(i.value)), 1);
  el.innerHTML = '<div class="bars">' + items.map(i => {
    const w = Math.max(2, Math.round(Math.abs(i.value) / max * 100));
    return `<div class="bar-row">
      <div class="blbl" title="${esc(i.label)}">${esc(i.label)}</div>
      <div class="bar-track"><div class="bar-fill ${i.kind || "acc"}" style="width:${w}%"></div></div>
      <div class="bval">${opts.fmt ? opts.fmt(i.value) : fmtNum(i.value)}${i.sub ? `<span class="sub"> ${esc(i.sub)}</span>` : ""}</div>
    </div>`;
  }).join("") + "</div>";
}

function setLastUpdated() {
  loadJSON("data/summary.json").then(s => {
    $$("[data-slot=last-updated]").forEach(e => e.textContent = "last updated " + s.generated);
  }).catch(() => {});
}

function edgarLink(acc) {
  if (!acc) return "";
  const cik = parseInt(String(acc).slice(0, 10), 10);
  const href = `https://www.sec.gov/Archives/edgar/data/${cik}/${String(acc).replace(/-/g, "")}-index.htm`;
  return `<a href="${href}" target="_blank" rel="noopener" title="View filing on SEC EDGAR">↗</a>`;
}
function renderPaperKpis(sel, psum) {
  const el = $(sel); if (!el) return;
  if (!psum || !psum.counts) {
    el.innerHTML = '<div class="card empty" style="grid-column:1/-1">Paper book will fill after the first collection of open-market buys (code P).</div>';
    return;
  }
  const cap = psum.capital || {}, roi = psum.roi || {}, c = psum.counts;
  const cards = [
    ["Signals", (c.signals || 0).toLocaleString(), "insider open-market buys", ""],
    ["Open positions", (c.open || 0).toLocaleString(), (c.awaiting_entry || 0) + " awaiting next open", ""],
    ["Capital deployed", fmtMoney(cap.deployed), "$10,000 × open positions", ""],
    ["Mark-to-market", fmtMoney(cap.value), "latest regular-session close", ""],
    ["Paper P&L", fmtMoney(cap.pnl, true), "ROI " + fmtPct(cap.roi), cap.pnl >= 0 ? "buy" : "sell"],
    ["Win rate", roi.n ? ((roi.win_rate || 0) * 100).toFixed(1) + "%" : "—", "positions with ROI > 0 · n=" + (roi.n || 0), roi.win_rate >= 0.5 ? "buy" : ""],
  ];
  el.innerHTML = cards.map(x =>
    `<div class="card stat"><div class="lbl">${x[0]}</div><div class="val ${x[3]}">${x[1]}</div><div class="sub">${x[2]}</div></div>`).join("");
}
function renderEquity(sel, eq) {
  const el = $(sel); if (!el) return;
  if (!eq || !eq.length) { el.innerHTML = '<div class="empty">Equity curve appears after the first fills.</div>'; return; }
  const pts = eq.slice(-90);
  const maxA = Math.max(...pts.map(p => Math.abs(p.pnl)), 1);
  el.innerHTML = '<div class="chart">' + pts.map(p => {
    const h = Math.max(2, Math.round(Math.abs(p.pnl) / maxA * 100));
    const kind = p.pnl >= 0 ? "buy" : "sell";
    return `<div class="col" title="${p.d}: ${p.n} pos, MTM ${fmtMoney(p.value)}, P&L ${fmtMoney(p.pnl, true)} (${fmtPct(p.roi)})">
      <div class="b ${kind}" style="height:${h}%"></div></div>`;
  }).join("") + "</div>" +
    `<div style="display:flex;justify-content:space-between;font-size:11px;color:var(--muted);margin-top:4px">
      <span>${pts[0].d}</span><span>${pts[pts.length - 1].d}</span></div>`;
}
function renderFindings(sel, psum) {
  const el = $(sel); if (!el) return;
  const fs = (psum && psum.findings) || [];
  if (!fs.length) { el.innerHTML = '<div class="empty">Findings appear once paper positions are marked to market.</div>'; return; }
  el.innerHTML = '<ul class="findings">' + fs.map(f =>
    `<li><b>${esc(f.title)}</b><span class="sub">${esc(f.text)}</span></li>`).join("") + "</ul>";
}
function paperRow(p) {
  const link = edgarLink(p.acc);
  return `<tr>
    <td>${statusPill(p.status)}</td>
    <td class="num">${fmtDate(p.fd)}<div class="sub">txn ${fmtDate(p.td)}</div></td>
    <td><span class="ticker">${esc(p.tk)}</span><div class="sub">${esc(p.co)}</div></td>
    <td>${esc(p.insider)}<div class="sub">${esc([p.rel, p.title].filter(Boolean).join(" · "))}</div></td>
    <td class="num">${fmtNum(p.insider_sh)}<div class="sub">${fmtPx(p.insider_px)}</div></td>
    <td class="num"><b>${fmtMoney(p.insider_val)}</b></td>
    <td class="num">${fmtDate(p.entry_d)}<div class="sub">${fmtPx(p.entry_px)}</div></td>
    ${pctCell(p.gap)}
    <td class="num">${fmtPx(p.last_px)}<div class="sub">${fmtDate(p.last_d)}</div></td>
    <td class="num">${fmtMoney(p.mtm)}</td>
    <td class="num ${p.pnl > 0 ? "pos" : p.pnl < 0 ? "neg" : ""}">${fmtMoney(p.pnl, true)}</td>
    ${pctCell(p.roi)}
    ${pctCell(p.r1)}${pctCell(p.r5)}${pctCell(p.r21)}
    <td style="text-align:center">${link}</td>
  </tr>`;
}
function renderPaperPreview(sel, rows, psum) {
  const el = $(sel); if (!el) return;
  const latest = (rows || []).slice(0, 12);
  if (!latest.length) { el.innerHTML = '<div class="empty">No paper trades yet.</div>'; return; }
  el.innerHTML = `<div class="table-scroll"><table><thead><tr>
    <th>Status</th><th class="num">Filed</th><th>Ticker</th><th>Insider</th>
    <th class="num">Insider $</th><th class="num">Our entry</th><th class="num">Gap</th>
    <th class="num">P&L</th><th class="num">ROI</th>
  </tr></thead><tbody>` + latest.map(p => `<tr>
    <td>${statusPill(p.status)}</td>
    <td class="num">${fmtDate(p.fd)}</td>
    <td><span class="ticker">${esc(p.tk)}</span></td>
    <td>${esc(p.insider)}</td>
    <td class="num">${fmtMoney(p.insider_val)}</td>
    <td class="num">${fmtPx(p.entry_px)}<div class="sub">${fmtDate(p.entry_d)}</div></td>
    ${pctCell(p.gap)}
    <td class="num ${p.pnl > 0 ? "pos" : p.pnl < 0 ? "neg" : ""}">${fmtMoney(p.pnl, true)}</td>
    ${pctCell(p.roi)}
  </tr>`).join("") + `</tbody></table></div>
    <div style="margin-top:10px"><a href="paper.html" class="btn ghost" style="width:100%;text-align:center">Open the full paper book →</a></div>`;
}

// ---------------------------------------------------------------- pages ----
function emptyBanner() {
  return `<div class="card" style="margin-bottom:16px;border-left:4px solid var(--warn)">
    <b>Awaiting first automated collection.</b>
    <div class="sub" style="margin-top:4px">The GitHub Actions pipeline pulls every Form 4 / Form 5 filing from
    SEC EDGAR nightly at 04:00 UTC and publishes it here automatically. No manual input is needed.</div></div>`;
}

async function initIndex() {
  const [sum, recent, psum, ppos, peq] = await Promise.all([
    loadJSON("data/summary.json"), loadJSON("data/recent.json"),
    loadJSONOpt("data/paper/summary.json"), loadJSONOpt("data/paper/positions.json"),
    loadJSONOpt("data/paper/equity.json"),
  ]);
  if (!sum.counts.trades) $("main").insertAdjacentHTML("afterbegin", emptyBanner());
  const nOpen = psum && psum.counts ? psum.counts.open : 0;
  $(".hero .range").textContent = nOpen
    ? `Paper book · ${nOpen.toLocaleString()} open $10k longs · last marked ${psum.asof || psum.generated || ""}`
    : (sum.counts.trades
      ? `Coverage ${sum.range.from} → ${sum.range.to} · ${sum.counts.trades.toLocaleString()} trades · paper book filling`
      : `Dataset initializing — first automated collection pending`);
  renderPaperKpis("#paperstats", psum);
  renderEquity("#papereq", peq || []);
  renderFindings("#paperfindings", psum);
  renderPaperPreview("#paperlatest", ppos || [], psum);
  const count = n => n.toLocaleString("en-US");
  const cards = [
    ["Insider trades", count(sum.counts.trades), "across " + count(sum.counts.filings) + " filings", ""],
    ["Companies", count(sum.counts.companies), "with filed trades", ""],
    ["Insiders", count(sum.counts.insiders), "directors, officers, 10%+ owners", ""],
    ["Open-market buys", fmtMoney(sum.value.buy), "code P · " + count(sum.counts.priced_purchases) + " trades", "buy"],
    ["Open-market sells", fmtMoney(sum.value.sell), "code S · " + count(sum.counts.priced_sales) + " trades", "sell"],
    ["Net insider flow", fmtMoney(sum.value.net, true), "buys − sells (P − S)", sum.value.net >= 0 ? "buy" : "sell"],
  ];
  $("#stats").innerHTML = cards.map(c =>
    `<div class="card stat"><div class="lbl">${c[0]}</div><div class="val ${c[3]}">${c[1]}</div><div class="sub">${c[2]}</div></div>`).join("");

  const latest = recent.slice(0, 15);
  $("#latest").innerHTML = `<div class="table-scroll"><table><thead><tr>
    <th>Filed</th><th>Company</th><th>Insider</th><th>Code</th><th class="num">Value</th>
  </tr></thead><tbody>` + latest.map(r =>
    `<tr><td class="num">${r.fd}<div class="sub">txn ${fmtDate(r.td)}</div></td>
     ${companyCell(r)}
     <td>${esc(r.in)}<div class="sub">${esc(r.title || r.rel)}</div></td>
     <td>${codePill(r.code, r.ct)}</td>
     <td class="num"><b>${r.val ? fmtMoney(r.val) : "—"}</b></td></tr>`).join("") +
    `</tbody></table></div><div style="margin-top:10px"><a href="trades.html" class="btn ghost" style="width:100%;text-align:center">Browse all ${sum.counts.trades.toLocaleString()} trades →</a></div>`;

  barList($("#topco"), sum.top_companies.slice(0, 10)
    .map(c => ({ label: c.tk || c.co, value: c.trades, sub: c.co, kind: "acc" })), {});
  const netItems = sum.companies.slice().sort((a, b) => Math.abs(b.net) - Math.abs(a.net)).slice(0, 10)
    .map(c => ({ label: c.tk || c.co, value: c.net, kind: c.net >= 0 ? "buy" : "sell" }));
  barList($("#netflow"), netItems, { fmt: v => fmtMoney(v, true) });

  // daily volume
  const daily = sum.daily.slice(-30);
  const maxT = Math.max(...daily.map(d => d.trades), 1);
  $("#dailychart").innerHTML = '<div class="chart">' + daily.map(d => {
    const h = Math.max(2, Math.round(d.trades / maxT * 100));
    return `<div class="col" title="${d.d}: ${d.trades} trades, net ${fmtMoney(d.net, true)}">
      <div class="b" style="height:${h}%"></div></div>`;
  }).join("") + "</div>";
  $("#dailyx").innerHTML = `<div style="display:flex;justify-content:space-between;font-size:11px;color:var(--muted)">
    <span>${daily.length ? daily[0].d : ""}</span><span>${daily.length ? daily[daily.length - 1].d : ""}</span></div>`;
  const lastDaily = daily[daily.length - 1];
  $("#dailynote").textContent = lastDaily
    ? `Most recent day: ${lastDaily.d} — ${lastDaily.trades} trades, net ${fmtMoney(lastDaily.net, true)}` : "";

  const buys = sum.net_buyers.slice(0, 5), sells = sum.net_sellers.slice(0, 5);
  $("#buyers").innerHTML = buys.length ? buys.map(c =>
    `<div class="linklist"><a href="trades.html?tk=${encodeURIComponent(c.tk || c.co)}">
      <span>${esc(c.tk || c.co)} <span class="sub">${esc(c.co)}</span></span>
      <span class="l-r pos">${fmtMoney(c.net, true)}</span></a></div>`).join("")
    : '<div class="empty">No open-market net buyers yet.</div>';
  $("#sellers").innerHTML = sells.length ? sells.map(c =>
    `<div class="linklist"><a href="trades.html?tk=${encodeURIComponent(c.tk || c.co)}">
      <span>${esc(c.tk || c.co)} <span class="sub">${esc(c.co)}</span></span>
      <span class="l-r neg">${fmtMoney(c.net, true)}</span></a></div>`).join("")
    : '<div class="empty">No open-market net sellers yet.</div>';
}

async function initPaper() {
  const [psum, ppos, peq] = await Promise.all([
    loadJSONOpt("data/paper/summary.json"),
    loadJSONOpt("data/paper/positions.json"),
    loadJSONOpt("data/paper/equity.json"),
  ]);
  renderPaperKpis("#paperstats", psum);
  renderEquity("#papereq", peq || []);
  renderFindings("#paperfindings", psum);
  if (psum && psum.rule) {
    const r = psum.rule;
    $("#paperrule").innerHTML = `<ul class="clean">
      <li><b>Signal</b> — ${esc(r.signal)}</li>
      <li><b>Entry</b> — ${esc(r.entry)}</li>
      <li><b>Size</b> — ${esc(r.size)}</li>
      <li><b>Mark</b> — ${esc(r.mark)}</li>
      <li><b>Lookahead</b> — ${esc(r.lookahead)}</li>
      <li><b>Prices</b> — ${esc(r.prices)}</li>
      <li><b>Costs</b> — ${esc(r.costs)}</li>
    </ul>`;
  }
  const byRole = (psum && psum.by_role) || [];
  const bySize = (psum && psum.by_size) || [];
  if ($("#byrole")) barList($("#byrole"), byRole.map(x => ({
    label: x.k, value: (x.mean || 0) * 100, kind: (x.mean || 0) >= 0 ? "buy" : "sell", sub: "n=" + x.n
  })), { fmt: v => (v >= 0 ? "+" : "") + v.toFixed(2) + "%" });
  if ($("#bysize")) barList($("#bysize"), bySize.map(x => ({
    label: x.k, value: (x.mean || 0) * 100, kind: (x.mean || 0) >= 0 ? "buy" : "sell", sub: "n=" + x.n
  })), { fmt: v => (v >= 0 ? "+" : "") + v.toFixed(2) + "%" });

  const hz = (psum && psum.horizons) || {};
  if ($("#horizons")) {
    const rows = [["Entry-day close","r0"],["+1 session","r1"],["+5 sessions","r5"],["+21 sessions","r21"],["+63 sessions","r63"],["Entry gap vs insider","gap"]];
    $("#horizons").innerHTML = `<table><thead><tr><th>Horizon</th><th class="num">n</th><th class="num">Mean</th><th class="num">Median</th><th class="num">Win rate</th></tr></thead><tbody>` +
      rows.map(([lab,k]) => {
        const s = hz[k] || {};
        return `<tr><td>${lab}</td><td class="num">${s.n || 0}</td>
          <td class="num ${s.mean>0?"pos":s.mean<0?"neg":""}">${fmtPct(s.mean)}</td>
          <td class="num ${s.median>0?"pos":s.median<0?"neg":""}">${fmtPct(s.median)}</td>
          <td class="num">${s.win_rate == null ? "—" : (s.win_rate*100).toFixed(1)+"%" }</td></tr>`;
      }).join("") + "</tbody></table>";
  }

  const rows = ppos || [];
  const COLS = [
    { key: "status", label: "Status" },
    { key: "fd", label: "Filed", cls: "num" },
    { key: "tk", label: "Ticker" },
    { key: "insider", label: "Insider" },
    { key: "insider_sh", label: "Insider sh / px", cls: "num" },
    { key: "insider_val", label: "Insider $", cls: "num" },
    { key: "entry_d", label: "Our entry", cls: "num" },
    { key: "gap", label: "Gap", cls: "num" },
    { key: "last_px", label: "Last px", cls: "num" },
    { key: "mtm", label: "MTM", cls: "num" },
    { key: "pnl", label: "P&L", cls: "num" },
    { key: "roi", label: "ROI", cls: "num" },
    { key: "r1", label: "+1d", cls: "num" },
    { key: "r5", label: "+5d", cls: "num" },
    { key: "r21", label: "+21d", cls: "num" },
    { key: "acc", label: "SEC" },
  ];
  const api = makeTable($("#paperTable"), COLS, rows, { per: 25, sortKey: "fd", row: paperRow });
  const apply = () => {
    const q = ($("#pq") && $("#pq").value.trim().toLowerCase()) || "";
    const st = ($("#pstatus") && $("#pstatus").value) || "";
    api.setFilter(r =>
      (!st || r.status === st) &&
      (!q || ((r.tk||"")+" "+(r.co||"")+" "+(r.insider||"")+" "+(r.title||"")).toLowerCase().includes(q)));
  };
  if ($("#pq")) $("#pq").addEventListener("input", apply);
  if ($("#pstatus")) $("#pstatus").addEventListener("change", apply);
}

async function initTrades() {
  const sum = await loadJSON("data/summary.json");
  if (!sum.counts.trades) $("main").insertAdjacentHTML("afterbegin", emptyBanner());
  const params = new URLSearchParams(location.search);
  const presetTk = params.get("tk") || "";
  const presetMonth = params.get("m") || "recent";

  const codeOpts = sum.by_code.map(c => `<option value="${esc(c.code)}">${esc(c.code)} — ${esc(c.text)}</option>`).join("");
  $("#codeSel").innerHTML = `<option value="">All codes</option>` + codeOpts;
  $("#sideSel").innerHTML = `<option value="">All sides</option>
    <option value="buy">Buys (P)</option><option value="sell">Sells (S)</option>
    <option value="exercise">Exercises</option><option value="grant">Grants</option>
    <option value="withholding">Tax withholding</option><option value="gift">Gifts</option>
    <option value="other">Other</option>`;
  $("#monthSel").innerHTML = `<option value="recent">Last 14 days</option>` +
    sum.months.map(m => `<option value="${esc(m)}">${m}</option>`).join("");
  if (presetMonth && (presetMonth === "recent" || sum.months.includes(presetMonth)))
    $("#monthSel").value = presetMonth;

  const tableEl = $("#tradesTable");
  const COLS = [
    { key: "fd", label: "Filed", cls: "num" },
    { key: "td", label: "Txn", cls: "num" },
    { key: "co", label: "Company" },
    { key: "in", label: "Insider" },
    { key: "code", label: "Code" },
    { key: "sec", label: "Security" },
    { key: "sh", label: "Shares", cls: "num" },
    { key: "px", label: "Price", cls: "num" },
    { key: "val", label: "Value", cls: "num" },
    { key: "af", label: "After", cls: "num" },
    { key: "di", label: "D/I", cls: "" },
  ];
  let api = null; // rebuilt on every data (re)load — see load()

  function currentFilters() {
    const q = $("#q").value.trim().toLowerCase();
    const code = $("#codeSel").value, side = $("#sideSel").value, form = $("#formSel").value;
    const der = $("#derChk").checked, tk = presetTk.toLowerCase();
    return (r) =>
      (!tk || (r.tk || "").toLowerCase() === tk || (r.co || "").toLowerCase().includes(tk)) &&
      (!code || r.code === code) && (!side || r.side === side) &&
      (!form || r.form === form) && (!der || r.der === 1) &&
      (!q || ((r.in || "") + " " + (r.co || "") + " " + (r.tk || "") + " " + (r.sec || "") + " " + (r.title || "")).toLowerCase().includes(q));
  }
  async function load(month) {
    tableEl.innerHTML = '<div class="empty"><span class="spinner"></span>Loading trades…</div>';
    const csvBtn = $("#csvBtn");
    if (csvBtn) {
      csvBtn.href = month === "recent" ? "data/trades.csv" : "data/csv/" + month + ".csv";
      csvBtn.textContent = month === "recent" ? "⬇ Download CSV (recent)" : "⬇ Download CSV (" + month + ")";
    }
    try {
      const rows = month === "recent"
        ? await loadJSON("data/recent.json")
        : await loadJSON("data/months/" + month + ".json");
      // Rebuild the table: the spinner above replaced tableEl's contents,
      // so the previous table DOM (if any) is gone.
      api = makeTable(tableEl, COLS, rows, { per: 25, sortKey: "fd" });
      api.setFilter(currentFilters());
      if (presetTk) $("#q").placeholder = "Search within " + presetTk + " (clear box to widen)";
    } catch (e) {
      api = null;
      tableEl.innerHTML = '<div class="empty">Could not load data: ' + esc(String(e)) + "</div>";
    }
  }
  ["q", "codeSel", "sideSel", "formSel", "derChk"].forEach(id => {
    const refresh = () => { if (api) api.setFilter(currentFilters()); };
    $("#" + id).addEventListener("input", refresh);
    $("#" + id).addEventListener("change", refresh);
  });
  $("#monthSel").addEventListener("change", () => load($("#monthSel").value));
  load(presetMonth);
}

async function initCompanies() {
  const sum = await loadJSON("data/summary.json");
  const el = $("#coTable");
  const api = makeTable(el, [
    { key: "tk", label: "Ticker" },
    { key: "co", label: "Company" },
    { key: "trades", label: "Trades", cls: "num" },
    { key: "insiders", label: "Insiders", cls: "num" },
    { key: "buy", label: "Buys $", cls: "num" },
    { key: "sell", label: "Sells $", cls: "num" },
    { key: "net", label: "Net $", cls: "num" },
    { key: "last", label: "Last filed", cls: "num" },
  ], sum.companies, { per: 50, sortKey: "trades", row: (r) => `<tr>
    <td><span class="ticker">${r.tk ? esc(r.tk) : "—"}</span></td>
    <td><a href="trades.html?tk=${encodeURIComponent(r.tk || r.co)}" title="View this company's trades">${esc(r.co)}</a></td>
    <td class="num">${fmtNum(r.trades)}</td>
    <td class="num">${fmtNum(r.insiders)}</td>
    <td class="num">${r.buy ? fmtMoney(r.buy) : "—"}</td>
    <td class="num">${r.sell ? fmtMoney(r.sell) : "—"}</td>
    <td class="num ${r.net > 0 ? "pos" : r.net < 0 ? "neg" : ""}">${fmtMoney(r.net, true)}</td>
    <td class="num">${fmtDate(r.last)}</td>
  </tr>` });
  $("#coSearch").addEventListener("input", () => {
    const q = $("#coSearch").value.trim().toLowerCase();
    api.setFilter((r) => !q || (r.co || "").toLowerCase().includes(q) || (r.tk || "").toLowerCase().includes(q));
  });
  const nb = sum.net_buyers.slice(0, 8), ns = sum.net_sellers.slice(0, 8);
  barList($("#nb"), nb.map(c => ({ label: c.tk || c.co, value: c.net, kind: "buy" })), { fmt: v => fmtMoney(v, true) });
  barList($("#ns"), ns.map(c => ({ label: c.tk || c.co, value: -c.net, kind: "sell" })), { fmt: v => fmtMoney(v) });
}

async function initInsiders() {
  const sum = await loadJSON("data/summary.json");
  const el = $("#inTable");
  const api = makeTable(el, [
    { key: "in", label: "Insider" },
    { key: "rel", label: "Role" },
    { key: "co", label: "Companies" },
    { key: "trades", label: "Trades", cls: "num" },
    { key: "buy", label: "Buys $", cls: "num" },
    { key: "sell", label: "Sells $", cls: "num" },
    { key: "net", label: "Net $", cls: "num" },
    { key: "last", label: "Last filed", cls: "num" },
  ], sum.insiders, { per: 50, sortKey: "trades", row: (r) => `<tr>
    <td><b>${esc(r.in)}</b><div class="sub">${esc(r.title || "")}</div></td>
    <td>${esc(r.rel)}</td>
    <td>${r.co.map(esc).join(", ")}</td>
    <td class="num">${fmtNum(r.trades)}</td>
    <td class="num">${r.buy ? fmtMoney(r.buy) : "—"}</td>
    <td class="num">${r.sell ? fmtMoney(r.sell) : "—"}</td>
    <td class="num ${r.net > 0 ? "pos" : r.net < 0 ? "neg" : ""}">${fmtMoney(r.net, true)}</td>
    <td class="num">${fmtDate(r.last)}</td>
  </tr>` });
  $("#inSearch").addEventListener("input", () => {
    const q = $("#inSearch").value.trim().toLowerCase();
    api.setFilter((r) => !q || (r.in || "").toLowerCase().includes(q) || (r.co || []).join(" ").toLowerCase().includes(q) || (r.title || "").toLowerCase().includes(q));
  });
}

async function initAnalysis() {
  const [sum, psum] = await Promise.all([
    loadJSON("data/summary.json"),
    loadJSONOpt("data/paper/summary.json"),
  ]);
  renderPaperKpis("#paperstats", psum);
  renderFindings("#paperfindings", psum);
  const byRole = (psum && psum.by_role) || [];
  const bySize = (psum && psum.by_size) || [];
  if ($("#byrole")) barList($("#byrole"), byRole.map(x => ({
    label: x.k, value: Math.abs(x.mean || 0) * 100, kind: (x.mean || 0) >= 0 ? "buy" : "sell", sub: "n=" + x.n
  })), { fmt: v => v.toFixed(2) + "%" });
  if ($("#bysize")) barList($("#bysize"), bySize.map(x => ({
    label: x.k, value: Math.abs(x.mean || 0) * 100, kind: (x.mean || 0) >= 0 ? "buy" : "sell", sub: "n=" + x.n
  })), { fmt: v => v.toFixed(2) + "%" });
  const best = (psum && psum.best) || [], worst = (psum && psum.worst) || [];
  if ($("#best")) $("#best").innerHTML = best.slice(0, 8).map(p =>
    `<tr><td>${esc(p.tk)}</td><td>${esc(p.insider)}</td>${pctCell(p.roi)}<td class="num">${fmtMoney(p.pnl, true)}</td></tr>`).join("") || '<tr><td colspan="4" class="empty">—</td></tr>';
  if ($("#worst")) $("#worst").innerHTML = worst.slice(0, 8).map(p =>
    `<tr><td>${esc(p.tk)}</td><td>${esc(p.insider)}</td>${pctCell(p.roi)}<td class="num">${fmtMoney(p.pnl, true)}</td></tr>`).join("") || '<tr><td colspan="4" class="empty">—</td></tr>';
  // code table
  $("#codeTable").innerHTML = `<table><thead><tr><th>Code</th><th>Meaning</th><th class="num">Trades</th><th class="num">Value</th></tr></thead><tbody>` +
    sum.by_code.map(c => `<tr><td>${codePill(c.code, c.text)}</td><td>${esc(c.text)}</td>
      <td class="num">${fmtNum(c.count)}</td><td class="num">${c.value ? fmtMoney(c.value) : "—"}</td></tr>`).join("") + "</tbody></table>";

  // donut: buys vs sells vs other value
  const buy = sum.value.buy, sell = sum.value.sell, other = Math.max(0, sum.value.total - buy - sell);
  const tot = Math.max(buy + sell + other, 1);
  const a1 = buy / tot * 360, a2 = a1 + sell / tot * 360;
  $("#donut").style.background = `conic-gradient(var(--buy) 0 ${a1}deg, var(--sell) ${a1}deg ${a2}deg, #94a3b8 ${a2}deg 360deg)`;
  $("#donuthole").innerHTML = `<b>${fmtMoney(sum.value.total)}</b>total value`;
  $("#legend").innerHTML = `
    <div><span class="dot" style="background:var(--buy)"></span>Open-market buys (P) — <b>${fmtMoney(buy)}</b></div>
    <div><span class="dot" style="background:var(--sell)"></span>Open-market sells (S) — <b>${fmtMoney(sell)}</b></div>
    <div><span class="dot" style="background:#94a3b8"></span>Other transactions — <b>${fmtMoney(other)}</b></div>`;

  // relationship breakdown
  const maxRel = Math.max(...sum.by_rel.map(r => r.count), 1);
  $("#relBars").innerHTML = sum.by_rel.map(r =>
    `<div class="bar-row"><div class="blbl">${esc(r.rel)}</div>
     <div class="bar-track"><div class="bar-fill" style="width:${Math.round(r.count / maxRel * 100)}%"></div></div>
     <div class="bval">${fmtNum(r.count)}</div></div>`).join("");

  // security types
  barList($("#secBars"), sum.by_security.slice(0, 12)
    .map(s => ({ label: s.sec.length > 34 ? s.sec.slice(0, 32) + "…" : s.sec, value: s.count, sub: s.value ? fmtMoney(s.value) : "" })), {});

  // daily net flow chart (last 60 days)
  const daily = sum.daily.slice(-60);
  const maxA = Math.max(...daily.map(d => Math.max(d.buy, d.sell, Math.abs(d.net))), 1);
  $("#netchart").innerHTML = '<div class="chart">' + daily.map(d => {
    const bh = Math.max(1, Math.round(d.buy / maxA * 100));
    const sh = Math.max(1, Math.round(d.sell / maxA * 100));
    return `<div class="col" title="${d.d}: buys ${fmtMoney(d.buy)}, sells ${fmtMoney(d.sell)}, net ${fmtMoney(d.net, true)}">
      <div class="b buy" style="height:${bh}%"></div><div class="b sell" style="height:${sh}%"></div></div>`;
  }).join("") + "</div>";
  $("#netx").innerHTML = `<div style="display:flex;justify-content:space-between;font-size:11px;color:var(--muted)">
    <span>${daily.length ? daily[0].d : ""}</span><span>${daily.length ? daily[daily.length - 1].d : ""}</span></div>`;

  // top net tables
  $("#ntb").innerHTML = sum.net_buyers.slice(0, 10).map(c =>
    `<tr><td>${esc(c.tk || c.co)}</td><td>${esc(c.co)}</td>
     <td class="num">${fmtMoney(c.buy)}</td><td class="num">${fmtMoney(c.sell)}</td>
     <td class="num pos">${fmtMoney(c.net, true)}</td></tr>`).join("") || '<tr><td colspan="5" class="empty">—</td></tr>';
  $("#nts").innerHTML = sum.net_sellers.slice(0, 10).map(c =>
    `<tr><td>${esc(c.tk || c.co)}</td><td>${esc(c.co)}</td>
     <td class="num">${fmtMoney(c.buy)}</td><td class="num">${fmtMoney(c.sell)}</td>
     <td class="num neg">${fmtMoney(c.net, true)}</td></tr>`).join("") || '<tr><td colspan="5" class="empty">—</td></tr>';
  $("#ntp").innerHTML = sum.top_purchasers.slice(0, 10).map(p =>
    `<tr><td><b>${esc(p.in)}</b></td><td>${esc((p.co || []).join(", "))}</td>
     <td class="num">${fmtMoney(p.buy)}</td></tr>`).join("") || '<tr><td colspan="3" class="empty">—</td></tr>';

  $("#formmix").innerHTML = `<ul class="clean">
    <li><b>Form 4</b> (statement of changes in beneficial ownership): <b>${sum.counts.form4.toLocaleString()}</b> trades</li>
    <li><b>Form 5</b> (annual statement, catch-all): <b>${sum.counts.form5.toLocaleString()}</b> trades</li>
    <li>Trades with a filed ticker symbol: <b>${sum.counts.with_ticker.toLocaleString()}</b> of ${sum.counts.trades.toLocaleString()}</li></ul>`;
}

async function initAbout() {
  let stats = null;
  try { stats = await loadJSON("data/stats.json"); } catch (e) { /* optional */ }
  if (stats && stats.last_updated) {
    const last = stats.runs && stats.runs.length ? stats.runs[stats.runs.length - 1] : null;
    $("#autostats").innerHTML = `<table>
      <tr><th>Last collection run</th><td>${esc(stats.last_updated)}</td></tr>
      ${last ? `<tr><th>Window collected</th><td>${esc((last.window || []).join(" → "))} (added ${esc(last.new_filings_trades)} trades)</td></tr>
      <tr><th>HTTP requests this run</th><td>${esc(last.requests)}</td></tr>
      <tr><th>Errors this run</th><td>${esc((last.errors || []).length)}</td></tr>` : ""}
    </table>`;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const p = document.body.dataset.page;
  $$(".nav a").forEach(a => a.classList.toggle("active", a.dataset.nav === p));
  const init = { index: initIndex, paper: initPaper, trades: initTrades, companies: initCompanies,
    insiders: initInsiders, analysis: initAnalysis, about: initAbout }[p] || initAbout;
  setLastUpdated();
  init().catch(e => {
    document.querySelector("main").insertAdjacentHTML("afterbegin",
      `<div class="card empty" style="border-color:var(--sell);color:var(--sell)">Failed to load data: ${esc(String(e))}</div>`);
    console.error(e);
  });
});
