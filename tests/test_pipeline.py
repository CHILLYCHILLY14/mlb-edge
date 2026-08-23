"""
End-to-end test: run the whole pipeline against synthetic feeds and assert the
outputs are internally consistent and physically sensible.

    python -m tests.test_pipeline
"""
from __future__ import annotations
import json, os, shutil, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from tests import fake_api
from pipeline.sources import mlb_api, espn, weather
from pipeline import config as C

FAILS: list[str] = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILS.append(f"{name} {detail}")


def patch():
    mlb_api.get_json = fake_api.responder
    espn.get_json = fake_api.responder
    weather.get_json = fake_api.responder


# ------------------------------------------------------------ unit checks ---
def test_simulator_physics():
    print("\n[simulator]")
    from pipeline.model.rates import LEAGUE_FALLBACK, apply_multipliers
    from pipeline.model.simulate import SidePack, simulate_game, derive
    lg = LEAGUE_FALLBACK
    P = np.tile(lg, (9, 1))
    mk = lambda M: SidePack(M, M, M, 22.5, 4.5, 9, 30, 1.0)
    sim = simulate_game(mk(P), mk(P), 20000, seed=3)
    d = derive(sim, 8.5)
    check("runs per team in 3.8-5.0", 3.8 <= d["mean_away"] <= 5.0, f"{d['mean_away']:.2f}")
    check("neutral matchup is a coin flip", abs(d["p_home"] - 0.5) < 0.02, f"{d['p_home']:.4f}")
    check("home bats less often than away", d["mean_home"] < d["mean_away"],
          f"{d['mean_home']:.2f} vs {d['mean_away']:.2f}")
    check("no ties survive", int((sim["away"] == sim["home"]).sum()) == 0)
    check("F5 is ~55% of the full game",
          0.50 <= d["mean_f5_total"] / d["mean_total"] <= 0.60,
          f"{d['mean_f5_total']/d['mean_total']:.3f}")
    check("one-run games near 29%",
          0.24 <= float((np.abs(sim["home"] - sim["away"]) == 1).mean()) <= 0.34,
          f"{float((np.abs(sim['home']-sim['away'])==1).mean()):.3f}")
    check("run line beats coin flip in the right direction", d["p_home_rl"] < d["p_home"])
    d_plus = derive(sim, 8.5, +1.5)
    check("laying the run and taking it are opposite sides",
          abs((d["p_home_rl"] + d_plus["p_away_rl"]) - 1.0) > 0.20
          and d_plus["p_home_rl"] > d["p_home_rl"],
          f"-1.5 {d['p_home_rl']:.3f} vs +1.5 {d_plus['p_home_rl']:.3f}")

    # ESPN publishes the spread from the favourite's side; make sure we flip it
    from pipeline.sources.espn import _runline_from
    ln, _, _ = _runline_from({"details": "MIL -1.5", "spread": -1.5,
                              "homeTeamOdds": {}, "awayTeamOdds": {}}, "WSH", "MIL")
    check("road favourite means the home run line is +1.5", ln == 1.5, str(ln))
    ln2, _, _ = _runline_from({"details": "WSH -1.5", "spread": -1.5,
                               "homeTeamOdds": {}, "awayTeamOdds": {}}, "WSH", "MIL")
    check("home favourite means the home run line is -1.5", ln2 == -1.5, str(ln2))

    # and that grading agrees with pricing on both signs
    from pipeline.grade import _settle
    row = {"market": "RL", "selection": "WSH", "home": "WSH", "away": "MIL",
           "line": 1.5, "stake": 0.0, "close_price": -110}
    r1 = _settle(row, {"away": 5, "home": 4})["result"]
    r2 = _settle(row, {"away": 7, "home": 4})["result"]
    check("home +1.5 wins a one-run loss", r1 == "win", r1)
    check("home +1.5 loses a three-run loss", r2 == "loss", r2)
    check("over+under = 1", abs(d["p_total_over"] + d["p_total_under"] - 1) < 1e-9)
    check("F5 probabilities sum to 1",
          abs(d["p_f5_home"] + d["p_f5_away"] + d["p_f5_tie"] - 1) < 1e-9)

    # a stacked lineup must beat a punchless one
    good = np.tile(apply_multipliers(lg, 1.6, 1.25), (9, 1))
    bad = np.tile(apply_multipliers(lg, 0.5, 0.80), (9, 1))
    s2 = simulate_game(mk(good), mk(bad), 8000, seed=5)
    check("better offence scores more", s2["away"].mean() > s2["home"].mean() + 1.0,
          f"{s2['away'].mean():.2f} vs {s2['home'].mean():.2f}")

    # Coors vs Oracle must move the total
    from pipeline.model.rates import park_weather_mults
    hr_c, hit_c = park_weather_mults({"run": 112, "hr": 111}, {"run_mult": 1.0})
    hr_o, hit_o = park_weather_mults({"run": 95, "hr": 88}, {"run_mult": 1.0})
    coors = np.tile(apply_multipliers(lg, hr_c, hit_c), (9, 1))
    oracle = np.tile(apply_multipliers(lg, hr_o, hit_o), (9, 1))
    tc = simulate_game(mk(coors), mk(coors), 8000, seed=9)
    to = simulate_game(mk(oracle), mk(oracle), 8000, seed=9)
    gap = (tc["away"] + tc["home"]).mean() - (to["away"] + to["home"]).mean()
    check("Coors totals run 1.0-3.0 above Oracle", 1.0 <= gap <= 3.0, f"{gap:.2f}")


def test_market_math():
    print("\n[market]")
    from pipeline.model import market as M
    check("decimal from -150", abs(M.american_to_decimal(-150) - 1.6667) < 1e-3)
    check("prob from +150", abs(M.american_to_prob(150) - 0.4) < 1e-6)
    check("round trip price", abs(M.prob_to_american(M.american_to_prob(-175)) + 175) < 0.01)
    a, b = M.no_vig(209, -259)
    check("de-vig sums to 1", abs(a + b - 1) < 1e-9)
    check("power de-vig shades the dog down", a < M.american_to_prob(209), f"{a:.4f}")
    for e in (0.01, 0.05, 0.25, 1.0, 5.0):
        check(f"edge {e} compresses under ceiling",
              M.compress(e, C.EDGE_CEILING) < C.EDGE_CEILING + 1e-9)
    check("tiny edges pass through nearly untouched",
          abs(M.compress(0.004, C.EDGE_CEILING) - 0.004) < 0.0005)
    worst = max(M.kelly_stake(C.EDGE_CEILING, p, C.BANKROLL)[0]
                for p in (-400, -175, -110, 100, 160, 400))
    check("no stake can exceed the cap", worst <= C.BANKROLL * C.MAX_STAKE_PCT + 1e-9,
          f"max ${worst:.2f} vs cap ${C.BANKROLL*C.MAX_STAKE_PCT:.2f}")
    check("negative edge never stakes", M.kelly_stake(-0.02, -110, 250)[0] == 0.0)


def test_weather_model():
    print("\n[weather]")
    from pipeline.sources import weather as W
    from pipeline.sources import parks as P
    W.get_json = fake_api.responder
    dome = P.lookup("Tropicana Field")
    rec = W.forecast(dome, "2026-08-21T23:10:00Z")
    check("dome neutralises weather", rec["roof_closed"] and rec["run_mult"] == 1.0)
    open_park = P.lookup("Wrigley Field")
    rec2 = W.forecast(open_park, "2026-08-21T23:10:00Z")
    check("open park weather stays within the cap",
          abs(rec2["run_mult"] - 1.0) <= C.WEATHER_CAP * C.WEATHER_WEIGHT + 1e-9,
          f"{rec2['run_mult']:.4f}")
    check("wind resolves onto the CF axis", "wind_component" in rec2)


# ----------------------------------------------------------- integration ----
def test_full_build():
    print("\n[end-to-end build]")
    patch()
    tmp = tempfile.mkdtemp(prefix="mlbedge-")
    data = os.path.join(tmp, "data")
    os.makedirs(data, exist_ok=True)
    C.DATA_DIR = data
    import pipeline.grade as G
    G.SHADOW = os.path.join(data, "shadow.json")
    G.final_scores = mlb_api.final_scores

    from pipeline import build as B
    B.C.DATA_DIR = data
    rc = B.main(["--date", "2026-08-21", "--days", "3", "--out", tmp, "--no-grade"])
    check("build returns 0", rc == 0)

    slate = json.load(open(os.path.join(tmp, "slate-2026-08-21.json")))
    games = slate["games"]
    check("15 games built", len(games) == 15, str(len(games)))

    tots, homes, edges, stakes = [], [], [], []
    for g in games:
        s = g["sim"]
        tots.append(s["mean_total"])
        homes.append(s["p_home"])
        check_probs = abs(s["p_home"] + s["p_away"] - 1) < 1e-9
        if not check_probs:
            FAILS.append("probs")
        for b in g["bets"]:
            edges.append(b["edge"])
            stakes.append(b["stake"])
    check("every game total inside the plausible 5.5-12.5 band",
          all(5.5 <= t <= 12.5 for t in tots), f"{min(tots):.2f}-{max(tots):.2f}")
    check("home win probs stay inside 20-80%",
          all(0.20 <= p <= 0.80 for p in homes), f"{min(homes):.3f}-{max(homes):.3f}")
    # The bug this guards: line shopping is positive on both sides of a game
    # whenever books disagree, so counting it as model edge made every market
    # look like a play and set the divergence flag off constantly.
    two_way = 0
    for g in games:
        for mkt in ("ML", "RL", "TOTAL"):
            sides = [b for b in g["bets"] if b["market"] == mkt and b["p_market"] is not None]
            if len(sides) != 2:
                continue
            two_way += 1
            if sum(1 for b in sides if b["edge"] > 1e-9) > 1:
                FAILS.append(f"both sides of {mkt} show an edge in {g['away']}@{g['home']}")
    check("at most one side of a two-way market can have an edge",
          not any("both sides of" in f for f in FAILS), f"{two_way} markets checked")
    check("two-way markets were actually present", two_way > 0, str(two_way))
    check("the shopping gain is reported separately from the model edge",
          all("edge_price_pct" in b and "edge_real_pct" in b
              for g in games for b in g["bets"]))
    check("realized edge is the model edge plus the shopping gain",
          all(abs(b["edge_pct"] + b["edge_price_pct"] - b["edge_real_pct"]) < 0.02
              for g in games for b in g["bets"]))
    check("no stake exceeds the realized edge's Kelly",
          all(b["stake"] == 0 or b["edge_real_pct"] > 0 for g in games for b in g["bets"]),
          "a bet was staked with negative expected value at its own price")

    check("no edge exceeds the ceiling",
          max(edges) <= C.EDGE_CEILING + 1e-9, f"{max(edges):.4f}")
    check("no stake exceeds 5% of bankroll",
          max(stakes) <= C.BANKROLL * C.MAX_STAKE_PCT + 1e-9, f"${max(stakes):.2f}")
    check("every game carries a rationale",
          all(len(g["rationale"]) > 60 for g in games))
    check("every game prices ML, RL and TOTAL",
          all({b["market"] for b in g["bets"]} >= {"ML", "RL", "TOTAL"} for g in games))
    check("F5 fair line published for every game",
          all(g["f5_fair"]["away"] and g["f5_fair"]["home"] for g in games))
    check("lineups flagged confirmed or projected",
          all(isinstance(g["lineups_confirmed"], bool) for g in games))
    for g in games:
        ml = {b["selection"]: b["p_final"] for b in g["bets"] if b["market"] == "ML"}
        if ml.get(g["away"]) is not None:
            if abs(ml[g["away"]] - g["sim"]["p_away_final"]) > 1e-6:
                FAILS.append(f"published prob disagrees with the ML bet in {g['away']}@{g['home']}")
    check("the published win probability is the one we actually bet",
          not any("published prob" in f for f in FAILS))
    check("raw simulation kept alongside the blended number",
          all("p_sim_home" in g["sim"] and "p_home_final" in g["sim"] for g in games))
    check("run distribution histogram present",
          all(sum(g["hist"]) == C.N_SIMS for g in games))

    pf = slate["portfolio"]
    check("no more than the configured best bets survive",
          pf["n_best"] <= C.MAX_BEST_BETS_PER_SLATE, str(pf["n_best"]))
    check("no more than the configured plays survive",
          pf["n_plays"] <= C.MAX_PLAYS_PER_SLATE, str(pf["n_plays"]))
    check("slate exposure inside the cap",
          pf["staked"] <= C.BANKROLL * C.MAX_SLATE_EXPOSURE_PCT + 0.51,
          f"${pf['staked']:.2f} vs ${C.BANKROLL*C.MAX_SLATE_EXPOSURE_PCT:.2f}")
    for g in games:
        sides = [b for b in g["bets"] if b["market"] in ("ML", "RL", "F5 ML") and b["stake"] > 0]
        if len(sides) > 1:
            FAILS.append(f"correlated side bets in {g['away']}@{g['home']}")
    check("never two correlated side bets on one game",
          not any(f.startswith("correlated side") for f in FAILS))

    for g in games:
        for b in g["bets"]:
            if b["market"] in ("RL", "TOTAL", "F5 TOTAL") and b["line"] is None:
                FAILS.append(f"{b['market']} bet with no line to grade against")
    check("every line-based bet carries its line",
          not any("no line to grade" in f for f in FAILS))
    check("moneylines carry no line", all(b["line"] is None for g in games
          for b in g["bets"] if b["market"] in ("ML", "F5 ML")))

    print("\n[lookahead]")
    day2 = json.load(open(os.path.join(tmp, "slate-2026-08-22.json")))
    day3 = json.load(open(os.path.join(tmp, "slate-2026-08-23.json")))
    check("future dates are built", len(day2["games"]) and len(day3["games"]))
    check("each slate knows how far out it is",
          day2["days_out"] == 1 and day3["days_out"] == 2)
    check("every game carries a readiness state",
          all(g["readiness"] in B.READINESS for d in (slate, day2, day3) for g in d["games"]))
    far = [b for g in day3["games"] for b in g["bets"] if b["stake"] > 0]
    check("nothing is staked beyond the sizing window", not far, f"{len(far)} staked")
    check("far-out games still publish a read",
          all(g["verdict"]["action"] in ("BET", "LEAN", "WATCH", "PASS", "WAIT")
              for g in day3["games"]))
    check("every game carries a plain-language verdict",
          all(len(g["verdict"]["text"]) > 20 for g in slate["games"]))

    print("\n[new markets and inputs]")
    g0 = games[0]
    d0 = g0["sim"]
    check("first-inning market sums to one",
          abs(d0["p_nrfi"] + d0["p_yrfi"] - 1) < 1e-9)
    check("first-inning runs are plausible", 0.4 <= d0["mean_i1"] <= 1.8, f"{d0['mean_i1']:.2f}")
    check("team totals are published for both sides",
          all(t in g["derived"]["team_totals"] for g in games for t in (g["away"], g["home"])))
    check("team total lines avoid a push",
          all(g["derived"]["team_totals"][g["away"]]["line"] % 1 == 0.5 for g in games))
    check("shutout odds published", all("shutout" in g["derived"] for g in games))
    check("NRFI fair price published",
          all(g["derived"]["nrfi"]["yes"] for g in games))
    check("team means add up to the game total",
          all(abs(g["derived"]["team_totals"][g["away"]]["mean"]
                  + g["derived"]["team_totals"][g["home"]]["mean"]
                  - g["sim"]["mean_total"]) < 0.02 for g in games))

    priced = [g for g in games if g["odds"].get("n_books")]
    check("odds carry a multi-book consensus", len(priced) > 0, str(len(priced)))
    if priced:
        o = priced[0]["odds"]
        check("consensus probabilities are de-vigged",
              abs(o["cons_away"] + o["cons_home"] - 1) < 1e-6)
        check("several books are read, not one", o["n_books"] >= 2, str(o["n_books"]))
        check("the edge is measured against consensus, not the best price",
              all(abs(b["p_market"] - o["cons_away" if b["selection"] == priced[0]["away"]
                                        else "cons_home"]) < 2e-4
                  for b in priced[0]["bets"] if b["market"] == "ML" and b["p_market"] is not None))
        # Shopping strips vig: the two best prices together imply less than any
        # single book does. That is exactly why the edge has to be graded
        # against the consensus and not against the numbers you actually bet -
        # otherwise every game shows an edge for free.
        from pipeline.model.market import american_to_prob as _p
        best_over = _p(o["ml_away"]) + _p(o["ml_home"])
        check("shopping strips vig rather than creating an edge",
              best_over <= 1.06, f"best-price overround {best_over:.4f}")
        check("the consensus keeps a real market margin out of the comparison",
              abs(o["cons_away"] + o["cons_home"] - 1.0) < 1e-6)
    check("bullpen availability is reported",
          all("unavailable" in g["away_pen"] and "tired" in g["away_pen"] for g in games))
    check("defensive efficiency is measured",
          all(0.55 <= g["defense"]["league"] <= 0.80 for g in games))

    ratings = json.load(open(os.path.join(tmp, "ratings.json")))["teams"]
    check("30 teams rated", len(ratings) == 30, str(len(ratings)))
    check("ratings sorted by true talent",
          all(ratings[i]["true_wpct"] >= ratings[i + 1]["true_wpct"] for i in range(len(ratings) - 1)))
    ws = [r["true_wpct"] for r in ratings]
    mean_w = sum(ws) / len(ws)
    mean_rs = sum(r["rs_per_g"] for r in ratings) / len(ratings)
    mean_ra = sum(r["ra_per_g"] for r in ratings) / len(ratings)
    check("the rated league centres on .500", abs(mean_w - 0.5) < 0.02, f"{mean_w:.4f}")
    check("league runs scored equals league runs allowed",
          abs(mean_rs - mean_ra) < 0.02, f"{mean_rs:.2f} vs {mean_ra:.2f}")
    check("league run environment is realistic",
          3.9 <= mean_rs <= 5.2, f"{mean_rs:.2f}")
    check("ratings are scaled to the league's real runs per game",
          all("scale" in r for r in ratings))
    check("true win pct is plausible",
          all(0.33 <= r["true_wpct"] <= 0.70 for r in ratings),
          f"{min(r['true_wpct'] for r in ratings):.3f}-{max(r['true_wpct'] for r in ratings):.3f}")

    # ---- grading: mark the slate final and settle it
    print("\n[shadow book]")
    fake_api.FINAL_DATES.add("2026-08-21")
    n = G.grade_all()
    check("calls graded", n > 0, str(n))
    perf = G.summarise()
    check("every tier is tracked",
          set(perf["by_tier"]) == {"BEST BET", "GOOD", "LEAN", "PASS"})
    check("PASSes are graded too", perf["by_tier"]["PASS"]["n"] > 0,
          str(perf["by_tier"]["PASS"]["n"]))
    tot_calls = sum(v["n"] for v in perf["by_tier"].values())
    check("all calls accounted for", tot_calls == perf["all_calls"]["n"])
    check("P/L only accrues on staked bets",
          all(r["pl"] == 0 for r in perf["ledger"] if not r["stake"]))
    wl = perf["all_calls"]
    check("wins + losses + pushes = n", wl["w"] + wl["l"] + wl["p"] == wl["n"])
    check("bankroll moves with P/L",
          abs(perf["bankroll_now"] - (C.BANKROLL + perf["overall"]["pl"])) < 0.01)
    check("ledger rows carry a final score",
          all(r["final_away"] is not None for r in perf["ledger"]))

    res = G.results_map()
    check("results feed is published for the browser to grade against",
          len(res) > 0, str(len(res)))
    sample = next(iter(res.values()))
    check("results carry both final and first-five scores",
          all(k in sample for k in ("away_score", "home_score", "f5_away", "f5_home")))
    check("results cover every graded game",
          {str(r["gamePk"]) for r in perf["ledger"]} <= set(res))

    print("\n[slate-wide divergence test]")
    from pipeline.model import portfolio as PF

    def slate_with(gap):
        """A slate where the model sits `gap` away from the market everywhere."""
        gs = []
        for i in range(12):
            p = 0.50 + gap
            gs.append({"gamePk": i, "away": "AAA", "home": "BBB", "bets": [
                {"market": "ML", "selection": "BBB", "label": "BBB ML", "tier": "GOOD",
                 "edge": 0.02, "stake": 0.0, "to_win": 0.0, "decimal": 2.0,
                 "p_final": p, "p_market": 0.50},
                {"market": "ML", "selection": "AAA", "label": "AAA ML", "tier": "PASS",
                 "edge": -0.02, "stake": 0.0, "to_win": 0.0, "decimal": 2.0,
                 "p_final": 1 - p, "p_market": 0.50}]})
        return {"games": gs}

    calm = slate_with(0.01)
    PF.apply(calm)
    check("a model that agrees with the market is not flagged",
          calm["portfolio"]["divergence_flag"] is False,
          f"median gap {calm['portfolio'].get('median_gap')}")
    check("the measured gap is reported either way",
          abs(calm["portfolio"]["median_gap"] - 0.01) < 1e-6)

    wild = slate_with(0.12)
    PF.apply(wild)
    check("a model systematically apart from the market is flagged",
          wild["portfolio"]["divergence_flag"] is True,
          f"median gap {wild['portfolio'].get('median_gap')}")

    # a few loud disagreements should not condemn an otherwise sane slate
    mixed = slate_with(0.01)
    for g in mixed["games"][:3]:
        g["bets"][0]["p_final"] = 0.80
        g["bets"][1]["p_final"] = 0.20
    PF.apply(mixed)
    check("three outliers do not condemn a sane slate",
          mixed["portfolio"]["divergence_flag"] is False,
          f"median gap {mixed['portfolio'].get('median_gap')}")

    thin = {"games": slate_with(0.30)["games"][:3]}
    PF.apply(thin)
    check("too few priced games to judge means no verdict",
          thin["portfolio"]["divergence_flag"] is False
          and thin["portfolio"].get("median_gap") is None)

    print("\n[prediction ledger]")
    import pipeline.predict as PR
    PR.STORE = os.path.join(data, "predictions.json")
    fake_api.FINAL_DATES.add("2026-08-22")
    n = PR.grade()
    check("game predictions are graded", n > 0, str(n))
    ps = PR.summary()
    ov = ps["overall"]
    check("every game is scored, not just the bets",
          ov["n"] >= 15, f"{ov['n']} graded")
    check("straight-up accuracy reported", 0.0 <= ov["accuracy"] <= 1.0)
    check("Brier score reported", ov["brier"] is not None)
    check("model and market are scored on the same games",
          ps["vs_market"]["n"] > 0 and ps["vs_market"]["market_brier"] is not None)
    check("run and total error tracked",
          ov["mae_total"] is not None and ov["mae_runs"] is not None)
    check("predictions exist for games with no bet",
          ov["n"] >= len({r["gamePk"] for r in perf["ledger"]}))
    cal = PR.calibration()
    check("calibration holds off until there is enough history",
          (cal["n"] >= C.CALIBRATION_MIN_GAMES) == cal["applied"],
          f"n={cal['n']} applied={cal['applied']}")
    check("calibration corrections stay inside their bounds",
          abs(cal["total_adj"]) <= C.CALIB_TOTAL_MAX
          and C.CALIB_PROB_MIN <= cal["prob_scale"] <= C.CALIB_PROB_MAX)

    print("\n[bad upstream data]")
    from pipeline.model.rates import blend_windows, split_vector, LEAGUE_FALLBACK
    lg = LEAGUE_FALLBACK
    good = {"bb": 60, "k": 110, "s": 90, "d": 30, "t": 2, "hr": 34}
    junk = {"bb": 300, "k": 300, "s": 300, "d": 300, "t": 300, "hr": 300}
    v = blend_windows(good, 500, junk, 50, lg, 200, 0.3, 40)
    check("an impossible rolling window is ignored",
          abs(float(v[5]) - float(blend_windows(good, 500, None, 0, lg, 200, 0.3, 40)[5])) < 1e-9)
    v2 = split_vector(good, 500, junk, 50, lg, 200, 180)
    check("an impossible split is ignored", 0 <= float(v2[5]) <= 0.25, f"{float(v2[5]):.3f}")

    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_simulator_physics()
    test_market_math()
    test_weather_model()
    test_full_build()
    print("\n" + ("=" * 60))
    if FAILS:
        print(f"{len(FAILS)} FAILURE(S):")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("all checks passed")
