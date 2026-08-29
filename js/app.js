/* CEOTrades — shared front-end helpers.
   Pure vanilla JS, no dependencies. All data comes from ./data/*.json. */
(function (global) {
  'use strict';

  /* ---------- formatting ---------- */
  function num(v) {
    if (v === null || v === undefined || v === '') return null;
    var n = typeof v === 'number' ? v : parseFloat(String(v).replace(/,/g, ''));
    return isFinite(n) ? n : null;
  }
  function fmtInt(v) {
    var n = num(v);
    return n === null ? '—' : n.toLocaleString('en-US', { maximumFractionDigits: 0 });
  }
  function fmtMoney(v, dp) {
    var n = num(v);
    if (n === null) return '—';
    var neg = n < 0; n = Math.abs(n);
    var s;
    if (n >= 1e9) s = '$' + (n / 1e9).toFixed(2) + 'B';
    else if (n >= 1e6) s = '$' + (n / 1e6).toFixed(2) + 'M';
    else if (n >= 1e3 && dp !== 2) s = '$' + (n / 1e3).toFixed(1) + 'K';
    else s = '$' + n.toLocaleString('en-US', { minimumFractionDigits: dp === undefined ? 0 : dp, maximumFractionDigits: dp === undefined ? 0 : dp });
    return (neg ? '−' : '') + s;
  }
  function fmtUSD(v) {
    var n = num(v);
    return n === null ? '—' : (n < 0 ? '−' : '') + '$' +
      Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function fmtPx(v) {
    var n = num(v);
    return n === null ? '—' : '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: n < 1 ? 4 : 2 });
  }
  function fmtPct(v, dp) {
    var n = num(v);
    if (n === null) return '—';
    return (n >= 0 ? '+' : '') + (n * 100).toFixed(dp === undefined ? 2 : dp) + '%';
  }
  function pctCls(v) {
    var n = num(v);
    if (n === null) return '';
    return n > 0 ? 'pos' : (n < 0 ? 'neg' : '');
  }
  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function titleCase(s) {
    s = String(s || '').trim();
    if (!s) return '';
    if (/[a-z]/.test(s)) return s;               // already mixed case
    return s.toLowerCase().replace(/\b([a-z])/g, function (m, c) { return c.toUpperCase(); });
  }
  function edgar(acc, issuerCik) {
    var a = String(acc || '').replace(/[^0-9-]/g, '');
    if (!a) return '';
    var plain = a.replace(/-/g, '');
    var cik = String(issuerCik || '').replace(/\D/g, '').replace(/^0+/, '');
    // Ownership-form accession prefixes often identify the reporting owner or
    // filing agent, not the issuer. Prefer the issuer CIK from parsed SEC data.
    if (!cik) cik = plain.slice(0, 10).replace(/^0+/, '');
    return 'https://www.sec.gov/Archives/edgar/data/' + cik + '/' + plain + '/' + a + '-index.htm';
  }
  function sideBadge(code, side) {
    var c = String(code || '').toUpperCase();
    var cls = side === 'buy' ? 'buy' : side === 'sell' ? 'sell' : 'neutral';
    return '<span class="b ' + cls + '">' + esc(c || '?') + '</span>';
  }

  /* ---------- data ---------- */
  var cache = {};
  function load(path) {
    if (cache[path]) return cache[path];
    cache[path] = fetch(path, { cache: 'no-cache' }).then(function (r) {
      if (!r.ok) throw new Error(path + ' → HTTP ' + r.status);
      return r.json();
    });
    return cache[path];
  }
  function fail(el, err) {
    if (!el) return;
    el.innerHTML = '<div class="empty">Could not load data.<br><span class="sub">' +
      esc(err && err.message ? err.message : err) + '</span></div>';
  }

  /* ---------- sortable / paged table ---------- */
  function Table(opts) {
    this.mount = typeof opts.mount === 'string' ? document.querySelector(opts.mount) : opts.mount;
    this.cols = opts.cols;
    this.rows = opts.rows || [];
    this.view = this.rows.slice();
    this.page = 0;
    this.size = opts.pageSize || 50;
    this.sortKey = opts.sortKey || null;
    this.sortDir = opts.sortDir || -1;
    this.empty = opts.empty || 'No matching rows.';
    if (this.sortKey) this.sort(this.sortKey, this.sortDir, true);
    this.render();
  }
  Table.prototype.setRows = function (rows) {
    this.rows = rows; this.view = rows.slice(); this.page = 0;
    if (this.sortKey) this.sort(this.sortKey, this.sortDir, true);
    this.render();
  };
  Table.prototype.filter = function (fn) {
    this.view = fn ? this.rows.filter(fn) : this.rows.slice();
    this.page = 0;
    if (this.sortKey) this.sort(this.sortKey, this.sortDir, true);
    this.render();
  };
  Table.prototype.sort = function (key, dir, quiet) {
    var col = null, i;
    for (i = 0; i < this.cols.length; i++) if (this.cols[i].key === key) col = this.cols[i];
    this.sortKey = key; this.sortDir = dir;
    var numeric = col && col.num;
    this.view.sort(function (a, b) {
      var x = a[key], y = b[key];
      if (numeric) {
        x = num(x); y = num(y);
        if (x === null && y === null) return 0;
        if (x === null) return 1;          // missing values always sink
        if (y === null) return -1;
        return (x - y) * dir;
      }
      x = String(x === null || x === undefined ? '' : x).toLowerCase();
      y = String(y === null || y === undefined ? '' : y).toLowerCase();
      if (x === y) return 0;
      if (!x) return 1;
      if (!y) return -1;
      return (x < y ? -1 : 1) * dir;
    });
    if (!quiet) { this.page = 0; this.render(); }
  };
  Table.prototype.render = function () {
    var self = this, c, i;
    if (!this.mount) return;
    var total = this.view.length;
    var pages = Math.max(1, Math.ceil(total / this.size));
    if (this.page >= pages) this.page = pages - 1;
    var slice = this.view.slice(this.page * this.size, (this.page + 1) * this.size);

    var h = '<div class="scroll"><table><thead><tr>';
    for (i = 0; i < this.cols.length; i++) {
      c = this.cols[i];
      var cls = (c.num ? 'num ' : '') + (c.sort === false ? '' : 'sortable') +
        (this.sortKey === c.key ? (this.sortDir === 1 ? ' sort-asc' : ' sort-desc') : '');
      h += '<th class="' + cls + '" data-k="' + esc(c.key) + '"' +
        (c.title ? ' title="' + esc(c.title) + '"' : '') + '>' + esc(c.label) + '</th>';
    }
    h += '</tr></thead><tbody>';
    if (!slice.length) {
      h += '<tr><td colspan="' + this.cols.length + '"><div class="empty">' +
        esc(this.empty) + '</div></td></tr>';
    }
    for (i = 0; i < slice.length; i++) {
      h += '<tr data-i="' + (this.page * this.size + i) + '">';
      for (var j = 0; j < this.cols.length; j++) {
        c = this.cols[j];
        var v = c.render ? c.render(slice[i], this.page * this.size + i) : esc(slice[i][c.key]);
        h += '<td class="' + (c.num ? 'num ' : '') + (c.cls || '') + '">' + v + '</td>';
      }
      h += '</tr>';
    }
    h += '</tbody></table></div>';
    h += '<div class="pager"><button class="btn" data-p="prev"' +
      (this.page === 0 ? ' disabled' : '') + '>← Prev</button>' +
      '<span class="count">' + (total ? (this.page * this.size + 1).toLocaleString() + '–' +
        Math.min(total, (this.page + 1) * this.size).toLocaleString() + ' of ' +
        total.toLocaleString() : '0') + '</span>' +
      '<button class="btn" data-p="next"' +
      (this.page >= pages - 1 ? ' disabled' : '') + '>Next →</button></div>';
    this.mount.innerHTML = h;

    this.mount.querySelectorAll('th.sortable').forEach(function (th) {
      th.addEventListener('click', function () {
        var k = th.getAttribute('data-k');
        self.sort(k, self.sortKey === k ? -self.sortDir : -1);
      });
    });
    this.mount.querySelectorAll('button[data-p]').forEach(function (b) {
      b.addEventListener('click', function () {
        self.page += (b.getAttribute('data-p') === 'next' ? 1 : -1);
        self.render();
        var top = self.mount.getBoundingClientRect().top + window.scrollY - 70;
        window.scrollTo({ top: top, behavior: 'smooth' });
      });
    });
  };

  /* ---------- CSV export ---------- */
  function toCSV(rows, cols) {
    var out = [cols.map(function (c) { return c.key; }).join(',')];
    rows.forEach(function (r) {
      out.push(cols.map(function (c) {
        var v = r[c.key];
        if (v === null || v === undefined) return '';
        v = String(v);
        return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
      }).join(','));
    });
    return out.join('\n');
  }
  function download(name, text) {
    var b = new Blob([text], { type: 'text/csv;charset=utf-8' });
    var u = URL.createObjectURL(b);
    var a = document.createElement('a');
    a.href = u; a.download = name; document.body.appendChild(a); a.click();
    document.body.removeChild(a); setTimeout(function () { URL.revokeObjectURL(u); }, 1000);
  }

  function debounce(fn, ms) {
    var t; return function () {
      var a = arguments, s = this;
      clearTimeout(t); t = setTimeout(function () { fn.apply(s, a); }, ms || 180);
    };
  }
  function param(name) {
    return new URLSearchParams(location.search).get(name);
  }
  function statCard(lbl, val, sub, cls) {
    return '<div class="card stat"><div class="lbl">' + esc(lbl) + '</div>' +
      '<div class="val ' + (cls || '') + '">' + val + '</div>' +
      (sub ? '<div class="sub">' + sub + '</div>' : '') + '</div>';
  }

  global.CT = {
    num: num, fmtInt: fmtInt, fmtMoney: fmtMoney, fmtUSD: fmtUSD, fmtPx: fmtPx,
    fmtPct: fmtPct, pctCls: pctCls, esc: esc, titleCase: titleCase, edgar: edgar,
    sideBadge: sideBadge, load: load, fail: fail, Table: Table, toCSV: toCSV,
    download: download, debounce: debounce, param: param, statCard: statCard
  };
})(window);
