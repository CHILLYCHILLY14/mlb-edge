/* Prove the Monte Carlo gate still rejects bad feeds and systematic drift.
 * Uses a real published matchup, with isolated copies in a temporary directory.
 * Never writes to the production feed or changes a prediction.
 */
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdtempSync, readdirSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

const require = createRequire(import.meta.url);
const SIM = require("../docs/sim.js");
const root = path.resolve(import.meta.dirname, "..");
const dir = path.join(root, "docs/data");
let fixture;
for (const file of readdirSync(dir).filter(f => /^slate-.*\.json$/.test(f)).sort()) {
  const slate = JSON.parse(readFileSync(path.join(dir, file), "utf8"));
  const game = slate.games.find(g => g.sim_inputs && !g.sim_inputs.away.no_starter && !g.sim_inputs.home.no_starter);
  if (game) { fixture = { ...slate, games: [game] }; break; }
}
assert.ok(fixture, "A published matchup with starters is required");
const temp = mkdtempSync(path.join(tmpdir(), "mlb-sim-gate-"));
function run(slate) {
  writeFileSync(path.join(temp, "slate-fixture.json"), JSON.stringify(slate));
  const result = spawnSync(process.execPath, [path.join(root, "tests/test_sim.mjs"), temp],
    { encoding: "utf8", timeout: 60000 });
  assert.ifError(result.error);
  return result;
}
function rejects(name, mutate, expected) {
  const slate = structuredClone(fixture);
  mutate(slate);
  const result = run(slate);
  assert.equal(result.status, 1, `${name}: ${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, expected, `${name}: wrong failure`);
  console.log(`PASS ${name}`);
}
try {
  const control = run(fixture);
  assert.equal(control.status, 0, control.stdout + control.stderr);
  rejects("missing replay inputs are rejected", s => {
    const missing = structuredClone(s.games[0]);
    delete missing.sim_inputs;
    s.games.push(missing);
  }, /FAIL  every published game carries/);
  rejects("invalid trial counts are rejected", s => { s.games[0].n_sims = 0; },
    /FAIL  valid simulation probability and sample count/);
  rejects("incorrect calibration is rejected", s => {
    const g = s.games[0], scale = g.sim_inputs.prob_scale ?? s.calibration?.prob_scale ?? 1;
    g.sim.p_raw_home ??= SIM.applyProbScale(g.sim.p_sim_home, 1 / scale);
    g.sim.p_sim_home += 0.05;
  }, /FAIL  published calibration agrees/);
  for (const [shift, count, expected, name] of [
    [0.08, 1, /FAIL  home probability agrees/, "large model drift is rejected"],
    [0.009, 20, /FAIL  home has no systematic probability bias/, "small systematic drift is rejected"],
  ]) {
    rejects(name, s => {
      const g = s.games[0], scale = g.sim_inputs.prob_scale ?? s.calibration?.prob_scale ?? 1;
      const js = SIM.runGame(g.sim_inputs, { rlLine: g.odds?.rl_line ?? -1.5, probScale: scale }, 20000, g.sim_inputs.seed);
      g.n_sims = 20000;
      g.sim = { ...js, p_raw_home: js.p_raw_home + shift,
        p_sim_home: SIM.applyProbScale(js.p_raw_home + shift, scale) };
      s.games = Array.from({ length: count }, (_, i) => ({ ...structuredClone(g), gamePk: i + 1 }));
    }, expected);
  }
} finally {
  rmSync(temp, { recursive: true, force: true });
}
