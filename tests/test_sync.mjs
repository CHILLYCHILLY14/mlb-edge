/*
 * Shared-ledger round trip for this board.
 *
 * The sync layer's promise is that a bet can leave this board, be flattened into
 * the shared sheet's columns, come back, and still be the exact object this
 * board's own code expects - same stake, same settlement, same everything it
 * reads. That promise is what these tests hold it to, offline, with no network
 * and no browser: the real ledger module, the real betsync.js and the real
 * adapter, loaded into a sandbox.
 *
 *   node tests/test_sync.mjs
 */
import assert from "node:assert/strict";
import vm from "node:vm";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const web = path.join(root, "docs");

let passed = 0;
const cases = [];
const test = (name, fn) => cases.push([name, fn]);

/* Objects made inside the sandbox carry its realm's prototype, which
   deepStrictEqual refuses to match. Compare what they say, not where they were
   made. */
function same(a, b, message) {
  const key = o => JSON.stringify(o, Object.keys(o || {}).sort());
  assert.equal(key(a), key(b), message);
}

/* A browser thin enough to load the ledger module, betsync and one adapter. */
function boardInPage() {
  const store = new Map();
  const localStorage = {
    getItem: k => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: k => store.delete(k)
  };
  const noop = () => {};
  const win = {
    localStorage, console, Promise, JSON, Date, Math, Object, Array,
    String, Number, isFinite, isNaN, Error,
    setInterval: noop, clearInterval: noop, setTimeout, clearTimeout,
    addEventListener: noop,
    navigator: { userAgent: "node" },
    document: { visibilityState: "visible", addEventListener: noop, readyState: "complete" },
    Storage: { prototype: { setItem: localStorage.setItem } }
  };
  win.window = win; win.self = win; win.globalThis = win;
  const ctx = vm.createContext(win);
  const run = f => vm.runInContext(fs.readFileSync(f, "utf8"), ctx, { filename: f });

  run(path.join(web, "ledger.js"));
  run(path.join(web, "staking.js"));
  run(path.join(web, "betsync.js"));

  let adapter = null;
  const register = ctx.window.BetSync.register;
  ctx.window.BetSync.register = a => { adapter = a; return register(a); };
  ctx.window.BetSync.start = () => {};
  run(path.join(web, "sync-adapter.js"));
  assert.ok(adapter, "sync-adapter.js registered nothing");

  return { ctx, adapter, L: ctx.window.MLBLedger, BS: ctx.window.BetSync,
           put: rows => localStorage.setItem(ctx.window.MLBLedger.STORAGE_KEY, JSON.stringify({ entries: rows })),
           get: () => ctx.window.MLBLedger.load() };
}

const entry = (over = {}) => ({
  gamePk: 777, date: "2026-09-13", start: "2026-09-13T17:35Z",
  away: "New York Mets", home: "New York Yankees",
  market: "ML", selection: "New York Yankees", label: "Yankees ML",
  line: null, price: -186, book: "DraftKings", stake: 12.5,
  tier: "GOOD", edge: 0.031, p_model: 0.641,
  added_at: "2026-09-13T15:00:00.000Z", result: null, pl: null, final: null,
  ...over
});

/* --------------------------------------------------------------------------- */

test("a bet flattens into the sheet's columns without losing itself", () => {
  const b = boardInPage();
  b.put([entry()]);
  const rows = b.adapter.readLocal();
  assert.equal(rows.length, 1);
  assert.equal(rows[0].app, "mlb-edge");
  assert.ok(rows[0].id.startsWith("mlb-edge:"), "ids carry the board, so two boards never collide");
  assert.equal(rows[0].status, "Pending");
  assert.ok(rows[0].native, "the board's own record travels along");
});

test("and comes back as the same object the board started with", () => {
  const b = boardInPage();
  const original = entry();
  b.put([original]);
  b.adapter.writeLocal(b.adapter.readLocal());
  same(b.get()[0], original, "a round trip must change nothing");
});

test("a result settled on another device lands on this board's entry", () => {
  const b = boardInPage();
  b.put([entry()]);
  const row = b.adapter.readLocal()[0];
  b.adapter.writeLocal([{ ...row, status: "Win", pnl: 7.5 }]);
  const after = b.get()[0];
  assert.equal(after.result, "win", "the board writes its own words for a result");
  assert.equal(after.pl, 7.5);
  assert.equal(after.stake, 12.5, "settling must not disturb the stake");
});

test("a stake edited elsewhere lands too, without disturbing the rest", () => {
  const b = boardInPage();
  const original = entry();
  b.put([original]);
  const row = b.adapter.readLocal()[0];
  b.adapter.writeLocal([{ ...row, stake: 41.25 }]);
  const after = b.get()[0];
  assert.equal(Number(after.stake), 41.25);
  assert.equal(after.gamePk, 777);
  assert.equal(after.label, "Yankees ML");
  assert.equal(after.tier, "GOOD");
});

test("a bet removed everywhere else is removed here", () => {
  const b = boardInPage();
  b.put([entry()]);
  b.adapter.writeLocal([]);
  assert.equal(b.get().length, 0);
});

test("the shared bankroll reaches the staking controls", () => {
  const b = boardInPage();
  b.adapter.applyBankroll({ current: 312.4 });
  assert.equal(b.ctx.window.MLBStaking.load({}).bankroll, 312.4);
});

test("app.js leaves the adapter a door in", () => {
  const source = fs.readFileSync(path.join(web, "app.js"), "utf8");
  assert.ok(/window\.MLBEdgeApp\s*=/.test(source),
    "app.js runs in a closure; without this export a synced bet never repaints");
  for (const fn of ["getLedger", "setLedger", "setStaking", "redraw"])
    assert.ok(source.includes(fn + ":"), "MLBEdgeApp." + fn + " is missing");
});

for (const [name, fn] of cases) {
  try { fn(); passed++; }
  catch (err) { console.error("FAIL  " + name + "\n      " + err.message); process.exitCode = 1; }
}
console.log(passed + "/" + cases.length + " passed");
