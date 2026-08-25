
"use strict";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

async function loadJSON(url) {
  const r = await fetch(url, { cache: "no-cache" });
  if (!r.ok) throw new Error(url + " -> " + r.status);
  return r.json();
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

// ---------------------------------------------------------------- pages ----
function emptyBanner() {
  return `<div class="card" style="margin-bottom:16px;border-left:4px solid var(--warn)">
    <b>Awaiting first automated collection.</b>
    <div class="sub" style="margin-top:4px">The GitHub Actions pipeline pulls every Form 4 / Form 5 filing from
    SEC EDGAR nightly at 04:00 UTC and publishes it here automatically. No manual input is needed.</div></div>`;
}

async function initIndex() {
  const [sum, recent] = await Promise.all([loadJSON("data/summary.json"), loadJSON("data/recent.json")]);
  if (!sum.counts.trades) $("main").insertAdjacentHTML("afterbegin", emptyBanner());
  $(".hero .range").textContent = sum.counts.trades
    ? `Coverage ${sum.range.from} → ${sum.range.to} · ${sum.counts.trades.toLocaleString()} trades · auto-updated daily`
    : `Dataset initializing — first nightly collection pending`;
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
  const sum = await loadJSON("data/summary.json");
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
  const init = { index: initIndex, trades: initTrades, companies: initCompanies,
    insiders: initInsiders, analysis: initAnalysis, about: initAbout }[p] || initAbout;
  setLastUpdated();
  init().catch(e => {
    document.querySelector("main").insertAdjacentHTML("afterbegin",
      `<div class="card empty" style="border-color:var(--sell);color:var(--sell)">Failed to load data: ${esc(String(e))}</div>`);
    console.error(e);
  });
});
