/*
 * The two simulators must agree.
 *
 *     node tests/test_sim.mjs [path/to/docs/data]
 *
 * pipeline/model/simulate.py runs at build time; docs/sim.js runs in the page
 * so you can change an input and see the score move. They are two hand-written
 * implementations of the same game, which is exactly the arrangement that
 * drifts apart quietly. This replays every published slate through the
 * JavaScript engine and checks it lands on Python's numbers inside Monte Carlo
 * error - and that the controls actually move the score in the right direction.
 */
import { createRequire } from "node:module";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";

const require = createRequire(import.meta.url);
const SIM = require("../docs/sim.js");

// Fixed probabilities isolate calibration math from Monte Carlo noise.
if (Math.abs(SIM.applyProbScale(.7, 1.2) - 0.7343405055709702) > 1e-10)
  throw new Error("log-odds calibration differs from Python");
if (SIM.applyProbScale(.7, 1) !== .7 || SIM.applyProbScale(.5, 1.2) !== .5)
  throw new Error("identity and even-match calibration must be stable");

const dir = process.argv[2] || "docs/data";
let fails = 0;
const check = (name, cond, detail = "") => {
  if (cond) console.log(`  PASS  ${name}`);
  else { console.log(`  FAIL  ${name}  ${detail}`); fails++; }
};

if (!existsSync(dir)) {
  console.log(`skipped — no feed at ${dir} (run: python -m tools.make_sample docs/data)`);
  process.exit(0);
}
const slates = readdirSync(dir).filter(f => f.startsWith("slate-") && f.endsWith(".json")).sort();
if (!slates.length) {
  console.log(`skipped — no slates in ${dir}`);
  process.exit(0);
}

const N = 20000;
console.log(`\n[replaying ${slates.length} slate(s) through the browser engine]`);

let compared = 0, published = 0, legacy = 0;
const dWin = [], dTotal = [], dF5 = [];
const probabilityChecks = { home: [], runLine: [], nrfi: [] };
// The engines use different RNGs. Account for BOTH Monte Carlo samples before
// calibration: stretching log odds also stretches sampling noise. Five standard
// errors keep a single random tail among hundreds of archived games from
// blocking every future refresh. A separate mean-bias check still catches
// smaller, systematic drift. No production model or betting limits change.
const Z_LIMIT = 5;
const ROUNDING = 0.0002; // archived probabilities/rate vectors are rounded
function compareProbability(rows, label, gamePk, js, py, nPy) {
  if (![js, py].every(p => Number.isFinite(p) && p >= 0 && p <= 1)
      || !Number.isInteger(nPy) || nPy <= 0) {
    check(`valid simulation probability and sample count: ${label}`, false);
    return;
  }
  const pooled = (js * N + py * nPy) / (N + nPy);
  const variance = pooled * (1 - pooled) * (1 / N + 1 / nPy);
  rows.push({ label, gamePk, delta: js - py, variance,
    limit: Z_LIMIT * Math.sqrt(variance) + ROUNDING });
}
for (const f of slates) {
  const slate = JSON.parse(readFileSync(path.join(dir, f), "utf8"));
  for (const g of slate.games || []) {
    // The initial August 21 archive predates simulator-input publication.
    // Preserve that history; all later feeds must carry replayable inputs.
    if (!g.sim_inputs && f === "slate-2026-08-21.json" && slate.date === "2026-08-21") {
      legacy++;
      continue;
    }
    published++;
    if (!g.sim_inputs) continue;
    // Python derives the run line against the market's own number, which is
    // +1.5 whenever the road team is favored. Feed the JavaScript side the same
    // line or the two are answering different questions.
    const rlLine = (g.odds && g.odds.rl_line != null) ? g.odds.rl_line : -1.5;
    const scale = g.sim_inputs.prob_scale ?? slate.calibration?.prob_scale ?? 1;
    if (!Number.isFinite(scale) || scale <= 0) {
      check(`valid calibration: ${f} ${g.gamePk}`, false);
      continue;
    }
    const js = SIM.runGame(g.sim_inputs, { rlLine, probScale: scale }, N, g.sim_inputs.seed);
    const py = g.sim;
    const label = `${f} ${g.away} @ ${g.home} (${g.gamePk})`;
    compared++;
    // Older archives predate p_raw_home. Invert their recorded calibration;
    // never compare an uncalibrated probability to a calibrated one.
    const rawHome = py.p_raw_home ?? SIM.applyProbScale(py.p_sim_home, 1 / scale);
    if (py.p_raw_home != null) {
      const calibrationError = Math.abs(SIM.applyProbScale(rawHome, scale) - py.p_sim_home);
      if (!(calibrationError <= ROUNDING))
        check(`published calibration agrees: ${label}`, false, String(calibrationError));
    }
    compareProbability(probabilityChecks.home, label, g.gamePk, js.p_raw_home, rawHome, g.n_sims);
    compareProbability(probabilityChecks.runLine, label, g.gamePk, js.p_home_rl, py.p_home_rl, g.n_sims);
    compareProbability(probabilityChecks.nrfi, label, g.gamePk, js.p_nrfi, py.p_nrfi, g.n_sims);
    dWin.push(Math.abs(js.p_home - py.p_sim_home));
    dTotal.push(Math.abs(js.mean_total - py.mean_total));
    dF5.push(Math.abs(js.mean_f5_total - py.mean_f5_total));
  }
}
const worst = a => Math.max(...a);
const avg = a => a.reduce((x, y) => x + y, 0) / a.length;

check("every published game carries its simulator inputs", compared > 0 && compared === published,
  `${compared} of ${published}`);
if (legacy) console.log(`  ${legacy} legacy games predate replay inputs (August 21 archive)`);
for (const [market, rows] of Object.entries(probabilityChecks)) {
  const outliers = rows.filter(r => Math.abs(r.delta) > r.limit);
  check(`${market} probability agrees within Monte Carlo uncertainty`,
    rows.length === compared && !outliers.length,
    outliers.map(r => `${r.label}: difference ${r.delta.toFixed(5)}, limit ${r.limit.toFixed(5)}`).join("; "));
  // Repeated dates/lookahead snapshots share a game's seed. Count each game
  // once so the aggregate check does not pretend those are independent trials.
  const unique = [...new Map(rows.map(r => [r.gamePk, r])).values()];
  if (unique.length >= 20) {
    const bias = unique.reduce((sum, r) => sum + r.delta, 0) / unique.length;
    const limit = Z_LIMIT * Math.sqrt(unique.reduce((sum, r) => sum + r.variance, 0))
      / unique.length + ROUNDING;
    check(`${market} has no systematic probability bias`, Math.abs(bias) <= limit,
      `bias ${bias.toFixed(5)}, limit ${limit.toFixed(5)} across ${unique.length} games`);
  }
}
console.log(`  Calibrated home probability: worst difference ${worst(dWin).toFixed(4)}, average ${avg(dWin).toFixed(4)} over ${compared} games`);
check("projected total agrees", worst(dTotal) < 0.20,
  `worst ${worst(dTotal).toFixed(3)} runs`);
check("first five agrees", worst(dF5) < 0.20, `worst ${worst(dF5).toFixed(3)} runs`);

console.log("\n[the controls move the game the way they should]");
const slate = JSON.parse(readFileSync(path.join(dir, slates[slates.length - 1]), "utf8"));
const game = slates.flatMap(f => JSON.parse(readFileSync(path.join(dir, f), "utf8")).games || [])
  .find(g => g.sim_inputs && !g.sim_inputs.away.no_starter && !g.sim_inputs.home.no_starter);
if (!game) throw new Error("Starter-control tests require a published game with both starters");
const base = SIM.runGame(game.sim_inputs, {}, N, 11);
const calibrated = SIM.runGame({...game.sim_inputs, prob_scale:1.2}, {}, N, 11);
check("confidence calibration changes probability, not simulated scoring",
  Math.abs(calibrated.p_home - SIM.applyProbScale(base.p_raw_home, 1.2)) < 1e-12
  && calibrated.mean_total === base.mean_total);
check("raw and calibrated away probabilities complement home",
  Math.abs(calibrated.p_home + calibrated.p_away - 1) < 1e-12
  && Math.abs(calibrated.p_raw_home + calibrated.p_raw_away - 1) < 1e-12);

const hotter = SIM.runGame(game.sim_inputs,
  { away: SIM.weatherMults(1.10), home: SIM.weatherMults(1.10) }, N, 11);
check("a hotter, wind-out night raises the total",
  hotter.mean_total > base.mean_total + 0.15,
  `${base.mean_total.toFixed(2)} -> ${hotter.mean_total.toFixed(2)}`);

const colder = SIM.runGame(game.sim_inputs,
  { away: SIM.weatherMults(0.90), home: SIM.weatherMults(0.90) }, N, 11);
check("a cold night with the wind in lowers it",
  colder.mean_total < base.mean_total - 0.15,
  `${base.mean_total.toFixed(2)} -> ${colder.mean_total.toFixed(2)}`);

const acePitching = SIM.runGame(game.sim_inputs, { away: { spQuality: 0.80 } }, N, 11);
check("a better home starter suppresses the away side",
  acePitching.mean_away < base.mean_away - 0.15,
  `${base.mean_away.toFixed(2)} -> ${acePitching.mean_away.toFixed(2)}`);
check("...and lifts the home team's win probability",
  acePitching.p_home > base.p_home,
  `${base.p_home.toFixed(3)} -> ${acePitching.p_home.toFixed(3)}`);

const gutted = SIM.runGame(game.sim_inputs,
  { home: { benched: [0, 1, 2, 3] } }, N, 11);
check("benching the top of the order costs runs",
  gutted.mean_home !== base.mean_home,
  `${base.mean_home.toFixed(2)} -> ${gutted.mean_home.toFixed(2)}`);

const shortHook = SIM.runGame(game.sim_inputs, { away: { bfMean: 12 } }, N, 11);
check("pulling the starter early changes the away total",
  Math.abs(shortHook.mean_away - base.mean_away) > 0.02,
  `${base.mean_away.toFixed(2)} -> ${shortHook.mean_away.toFixed(2)}`);

console.log("\n[determinism]");
const a = SIM.runGame(game.sim_inputs, {}, 4000, 99);
const b = SIM.runGame(game.sim_inputs, {}, 4000, 99);
check("the same seed gives the same answer", a.p_home === b.p_home && a.mean_total === b.mean_total);
const c = SIM.runGame(game.sim_inputs, {}, 4000, 100);
check("a different seed gives a different answer", c.p_home !== a.p_home);

console.log("\n" + "=".repeat(60));
if (fails) { console.log(`${fails} FAILURE(S)`); process.exit(1); }
console.log("all simulator checks passed");
