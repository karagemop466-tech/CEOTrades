#!/usr/bin/env node
/* Test the browser-side data helpers against the generated SEC dataset.
   Run: node scripts/site_test.js */
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const D = require(path.join(ROOT, "assets", "data.js"));

function load(name) {
  return JSON.parse(fs.readFileSync(path.join(ROOT, "data", name), "utf8"));
}

const trades = load("trades.json");
const summary = load("summary.json");

let passed = 0;
function test(name, fn) {
  try {
    fn();
    passed++;
    console.log("PASS  " + name);
  } catch (err) {
    console.error("FAIL  " + name + " -- " + err.message);
    process.exitCode = 1;
  }
}

test("trades.json exists and is a non-empty list", () => {
  assert.ok(Array.isArray(trades) && trades.length > 0);
});

test("filterTrades: search by ticker", () => {
  const r = D.filterTrades(trades, { q: trades[0].company.ticker || "" });
  assert.ok(r.length > 0);
  assert.ok(r.every(x => (x.company.ticker === trades[0].company.ticker) ||
                          (x.company.ticker || "").toLowerCase().includes(
                            (trades[0].company.ticker || "").toLowerCase())));
});

test("filterTrades: code filter", () => {
  const code = trades.find(t => t.code)?.code;
  const r = D.filterTrades(trades, { code });
  assert.ok(r.length > 0 && r.every(t => t.code === code));
});

test("filterTrades: direction filter", () => {
  const r = D.filterTrades(trades, { direction: "A" });
  assert.ok(r.every(t => t.acquired_disposed === "A"));
});

test("filterTrades: date range", () => {
  const r = D.filterTrades(trades, { dateFrom: "2026-08-20", dateTo: "2026-08-21" });
  assert.ok(r.every(t => t.filing_date >= "2026-08-20" && t.filing_date <= "2026-08-21"));
});

test("sortTrades: value desc", () => {
  const sorted = D.sortTrades(trades, "value", "desc");
  for (let i = 1; i < sorted.length; i++) {
    assert.ok((sorted[i - 1].value ?? -1) >= (sorted[i].value ?? -1));
  }
});

test("paginate: bounds and pages", () => {
  const p = D.paginate(trades, 1, 25);
  assert.strictEqual(p.rows.length, Math.min(25, trades.length));
  assert.strictEqual(p.pages, Math.ceil(trades.length / 25));
  const last = D.paginate(trades, 999, 25);
  assert.ok(last.rows.length > 0);
  assert.ok(last.end <= trades.length);
});

test("groupByFiling: every filing one entry, counts add up", () => {
  const g = D.groupByFiling(trades);
  const total = g.reduce((n, f) => n + f.trades, 0);
  assert.strictEqual(total, trades.length);
  assert.strictEqual(g.length, new Set(trades.map(t => t.accession)).size);
});

test("dailySeries matches Python summary.json", () => {
  const ours = D.dailySeries(trades);
  const theirs = summary.daily || [];
  assert.strictEqual(ours.length, theirs.length);
  ours.forEach((d, i) => {
    assert.strictEqual(d.date, theirs[i].date);
    assert.strictEqual(d.trades, theirs[i].trades);
    assert.strictEqual(d.filings, theirs[i].filings);
    assert.ok(Math.abs(d.value - theirs[i].value) < 0.01,
      `value mismatch on ${d.date}`);
  });
});

test("roleLabel/roles handle multi-role owners", () => {
  const multi = trades.find(t => {
    const o = t.owner;
    return [o.officer, o.director, o.ten_percent_owner, o.other].filter(Boolean).length > 1;
  });
  if (multi) assert.ok(D.roles(multi.owner).length >= 2);
});

test("directionClass/label cover A and D", () => {
  const buy = trades.find(t => t.acquired_disposed === "A");
  const sell = trades.find(t => t.acquired_disposed === "D");
  if (buy) assert.strictEqual(D.directionClass(buy), "b-buy");
  if (sell) assert.strictEqual(D.directionClass(sell), "b-sell");
});

test("csv escapes quotes and commas", () => {
  const csv = D.csv([{ a: 'x,"y"' }], [{ label: "a", get: r => r.a }]);
  assert.ok(csv.includes('"x,""y"""'));
});

test("fmt helpers format numbers and money", () => {
  assert.strictEqual(D.fmtMoney(1234.5), "$1,234.50");
  assert.strictEqual(D.fmtNum(1234), "1,234");
  assert.strictEqual(D.fmtCompact(1.2e6), "$1.20M");
});

console.log(`\n${passed} browser-side tests passed (${trades.length} records, ${new Set(trades.map(t => t.accession)).size} filings)`);
process.exit(process.exitCode || 0);
