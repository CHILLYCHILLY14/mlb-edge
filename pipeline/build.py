#!/usr/bin/env python3
"""
MLB Edge - build the slate.

Pulls the schedule, probable starters, batting orders, season rate stats,
standings, sportsbook prices and game-time weather; simulates every game
20,000 times at the plate-appearance level; prices the moneyline, run line,
total and first five; and writes a JSON feed the dashboard reads.

    python -m pipeline.build                # today
    python -m pipeline.build --date 2026-08-21
    python -m pipeline.build --days 3       # today plus the next two
"""
from __future__ import annotations
import argparse, json, os, sys, time
from datetime import datetime, timedelta, timezone

import numpy as np

from . import config as C
from .model import rates as R
from .model import teams as T
from .model.market import fmt_american, prob_to_american
from .model import portfolio
from .model.price import price_game, f5_fair
from .model.simulate import SidePack, simulate_game, derive
from .sources import espn, parks, weather
from .sources.mlb_api import (TEAM_ABBR, people_stats, schedule, standings,
                              team_batters, team_pitchers)

ET = timezone(timedelta(hours=-4))       # display only; all logic runs in UTC


# ------------------------------------------------------------------ utils ---
def today_et() -> str:
    return datetime.now(timezone.utc).astimezone(ET).strftime("%Y-%m-%d")


def load_json(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, separators=(",", ":"), default=str)
    os.replace(tmp, path)


# ------------------------------------------------------- roster gathering ---
class League:
    """Fetch-once cache of every roster we need, plus the league baseline."""

    def __init__(self):
        self.bat: dict[int, list] = {}
        self.pit: dict[int, list] = {}
        self.baseline = R.LEAGUE_FALLBACK.copy()

    def load(self, team_ids: list[int]) -> None:
        for tid in team_ids:
            if tid not in self.bat:
                self.bat[tid] = team_batters(tid)
            if tid not in self.pit:
                self.pit[tid] = team_pitchers(tid)
        allb = [b for v in self.bat.values() for b in v]
        if allb:
            self.baseline = R.league_baseline(allb)

    def pitcher_vec(self, p: dict | None, is_sp: bool) -> np.ndarray:
        if not p:
            return self.baseline.copy()
        prior = C.PRIOR_TBF_SP if is_sp else C.PRIOR_TBF_RP
        return R.shrink(p["counts"], p.get("tbf", 0.0), self.baseline, prior)


# ------------------------------------------------------------- narrative ----
def rationale(g, d, best, away_sp, home_sp, wx, park, lineup_conf,
              p_away_f=None, p_home_f=None):
    """Plain-English reason the number is what it is - drivers, not adjectives."""
    bits = []
    a, h = g["away"], g["home"]
    ph = p_home_f if p_home_f is not None else d["p_home"]
    tail = ""
    if p_home_f is not None and abs(p_home_f - d["p_home"]) >= 0.01:
        tail = (f" (the raw simulation had {h} at {d['p_home']*100:.1f}% before the "
                f"market blend)")
    bits.append(f"Sim projects {d['mean_away']:.2f}-{d['mean_home']:.2f} "
                f"({d['mean_total']:.2f} total); {h} wins {ph*100:.1f}%{tail}.")
    if away_sp and home_sp:
        bits.append(f"{away_sp['name']} ({away_sp.get('era',0):.2f} ERA, "
                    f"{away_sp.get('k9',0):.1f} K/9) vs {home_sp['name']} "
                    f"({home_sp.get('era',0):.2f} ERA, {home_sp.get('k9',0):.1f} K/9).")
    elif away_sp or home_sp:
        named = away_sp or home_sp
        bits.append(f"Only one starter posted ({named['name']}); the other side is "
                    f"modelled as a bullpen game.")
    else:
        bits.append("Neither starter posted - both staffs modelled as bullpen games.")
    if park.get("run", 100) != 100 or park.get("hr", 100) != 100:
        bits.append(f"{park['name']} plays {park['run']} runs / {park['hr']} HR.")
    if wx.get("roof_closed"):
        bits.append("Roof shut, weather neutralised.")
    elif abs(wx.get("applied_pct", 0)) >= 1.0:
        bits.append(f"Weather {wx['applied_pct']:+.1f}% run environment ({wx['note']}).")
    bits.append("Confirmed batting orders." if lineup_conf
                else "Projected batting orders - lineups not posted yet.")
    if best and best["tier"] != "PASS":
        gapt = (f", market {best['price_txt']} implies {best['p_market']*100:.1f}%"
                if best.get("p_market") is not None else "")
        bits.append(f"Best number is {best['label']}: model {best['p_final']*100:.1f}%"
                    f"{gapt} - {best['edge_pct']:+.2f}% edge, fair {best['fair_price']}.")
    else:
        bits.append("No qualifying edge - market is priced where the model is.")
    return " ".join(bits)


# ------------------------------------------------------------ power ratings -
def _accumulate(counts_list) -> dict:
    tot = {k: 0.0 for k in ("bb", "k", "s", "d", "t", "hr")}
    for c in counts_list:
        for k in tot:
            tot[k] += c.get(k, 0.0)
    return tot


def _reference_opponent(lg: "League"):
    """
    A single reference team, built exactly the way every rated team is built:
    the composite of all 30 projected batting orders, all 30 top-five rotations,
    and all 30 leverage-weighted bullpens.

    This matters. Rating a team's nine best hitters against a league average
    that includes every bench bat inflates offence, and rating its top five
    starters against that same weak average deflates run prevention - which is
    how you end up telling a 54-74 team it is a 110-win roster. Comparing every
    team against an identically-constructed opponent removes the bias, and the
    league then centres on .500 by construction.
    """
    league = lg.baseline
    slot_c = [[] for _ in range(9)]     # per batting-order slot, not one blob
    slot_pa = [0.0] * 9
    bat_c, bat_pa = [], 0.0
    sp_c, sp_tbf = [], 0.0
    pen_c, pen_tbf = [], 0.0
    for tid, batters in lg.bat.items():
        lineup, _ = T.project_lineup(batters, [])
        for i, b in enumerate(lineup[:9]):
            slot_c[i].append(b["counts"])
            slot_pa[i] += b.get("pa", 0.0)
            bat_c.append(b["counts"])
            bat_pa += b.get("pa", 0.0)
        pitchers = lg.pit.get(tid, [])
        sps = sorted([p for p in pitchers if p.get("is_sp")],
                     key=lambda p: -p.get("tbf", 0))[:5]
        for p in sps:
            sp_c.append(p["counts"])
            sp_tbf += p.get("tbf", 0.0)
        pen = T.bullpen_composite(pitchers)
        pen_c.append(pen["counts"])
        pen_tbf += pen.get("tbf", 0.0)

    ref_bat = R.shrink(_accumulate(bat_c), bat_pa, league, 0.0) if bat_pa else league
    ref_sp = R.shrink(_accumulate(sp_c), sp_tbf, league, 0.0) if sp_tbf else league
    ref_pen = R.shrink(_accumulate(pen_c), pen_tbf, league, 0.0) if pen_tbf else league
    # One reference hitter per lineup slot rather than nine clones of the league
    # mean. Run scoring is convex in on-base ability, so a uniform lineup scores
    # measurably less than a real one with the same average - which would show
    # up as every team out-hitting the reference by a tenth of a run.
    ref_lineup = []
    for i in range(9):
        pa = max(slot_pa[i], 1.0)
        ref_lineup.append({"id": None, "name": f"Reference {i+1}", "pos": "DH",
                           "bats": "R", "pa": pa, "sb": pa * 0.012,
                           "counts": _accumulate(slot_c[i]) if slot_c[i] else {},
                           "ops": 0.0})
    all_bat = [b for v in lg.bat.values() for b in v]
    ref_adv = R.baserunning_index(all_bat) if all_bat else 1.0
    return ref_bat, ref_sp, ref_pen, ref_lineup, ref_adv


def power_ratings(lg: League, sd: dict) -> list[dict]:
    """
    Neutral-park simulation of every roster against one identically-built
    reference team. A true-talent rating, not a standings snapshot: what would
    this roster do against average opposition in an average park.
    """
    out = []
    league = lg.baseline
    ref_bat, ref_sp, ref_pen, ref_lineup, ref_adv = _reference_opponent(lg)

    for tid, batters in lg.bat.items():
        pitchers = lg.pit.get(tid, [])
        lineup, _ = T.project_lineup(batters, [])
        pen = T.bullpen_composite(pitchers)
        sps = sorted([p for p in pitchers if p.get("is_sp")],
                     key=lambda p: -p.get("tbf", 0))[:5]
        if not sps:
            continue
        sp_vec = R.shrink(_accumulate([p["counts"] for p in sps]),
                          sum(p["tbf"] for p in sps), league, C.PRIOR_TBF_SP)
        pen_vec = lg.pitcher_vec(pen, False)

        # the team hits against the reference staff
        off = T.side_matrices(lineup, ref_sp, T.tto_vector(ref_sp), ref_pen,
                              league, "R", 1.0, 1.0, 0.0)
        team_off = SidePack(*off, bf_mean=C.SP_BF_DEFAULT, bf_sd=C.SP_BF_SD,
                            bf_min=C.SP_BF_MIN, bf_max=C.SP_BF_MAX,
                            adv=R.baserunning_index(batters))
        # the reference lineup hits against this team's staff
        deff = T.side_matrices(ref_lineup, sp_vec, T.tto_vector(sp_vec), pen_vec,
                               league, "R", 1.0, 1.0, 0.0)
        opp = SidePack(*deff, bf_mean=C.SP_BF_DEFAULT, bf_sd=C.SP_BF_SD,
                       bf_min=C.SP_BF_MIN, bf_max=C.SP_BF_MAX, adv=ref_adv)

        # Play the pair both ways. The home team skips the bottom of the ninth
        # when it is ahead, which shaves about two tenths of a run off whichever
        # side is at home; rating every team from one seat would bake that
        # structural quirk into the ratings as if it were talent.
        s_away = simulate_game(team_off, opp, C.N_SIMS_RATINGS, seed=C.RANDOM_SEED + tid)
        s_home = simulate_game(opp, team_off, C.N_SIMS_RATINGS, seed=C.RANDOM_SEED + tid + 7919)
        rs = (float(s_away["away"].mean()) + float(s_home["home"].mean())) / 2
        ra = (float(s_away["home"].mean()) + float(s_home["away"].mean())) / 2
        wpct = (float((s_away["away"] > s_away["home"]).mean())
                + float((s_home["home"] > s_home["away"]).mean())) / 2
        st = sd.get(tid, {})
        out.append({
            "team": TEAM_ABBR.get(tid, "?"), "team_id": tid,
            "name": st.get("name", TEAM_ABBR.get(tid, "?")),
            "rs_per_g": round(rs, 2), "ra_per_g": round(ra, 2),
            "net": round(rs - ra, 2), "true_wpct": round(wpct, 4),
            "proj_162": round(wpct * 162, 1),
            "w": st.get("w"), "l": st.get("l"), "diff": st.get("diff"),
            "l10": st.get("l10"), "streak": st.get("streak"),
            "actual_wpct": round(st["w"] / max(st["w"] + st["l"], 1), 4) if st else None,
            "bullpen_era": round(pen.get("era", 0.0), 2),
            "rotation_era": round(sum(p.get("era", 0) * p.get("tbf", 0) for p in sps)
                                  / max(sum(p.get("tbf", 0) for p in sps), 1), 2),
        })
    # Calibrate the two run columns.
    #
    # Two things are true of a real league and not of the raw output. Every run
    # scored is a run allowed, so the league's RS and RA must be the same
    # number; and the reference staff here is a composite of every team's top
    # five starters plus its leverage-weighted bullpen, which is better than the
    # average arm a lineup actually faces, so the raw scale sits below real MLB
    # scoring. An affine calibration fixes both: recentre so the columns
    # balance, then scale so the league lands on the runs per game it has
    # actually produced this season. Each team keeps its distance from league
    # average, which is the entire content of a rating - ordering, spread and
    # true win percentage are untouched.
    if out:
        m_rs = sum(r["rs_per_g"] for r in out) / len(out)
        m_ra = sum(r["ra_per_g"] for r in out) / len(out)
        mid = (m_rs + m_ra) / 2 or 1.0
        tot_r = sum(v.get("rs", 0) for v in sd.values())
        tot_g = sum(max(v.get("w", 0) + v.get("l", 0), 0) for v in sd.values())
        actual_rpg = (tot_r / tot_g) if tot_g else 0.0
        scale = min(max((actual_rpg / mid) if actual_rpg else 1.0, 0.75), 1.40)
        for r in out:
            r["rs_per_g"] = round((r["rs_per_g"] - m_rs + mid) * scale, 2)
            r["ra_per_g"] = round((r["ra_per_g"] - m_ra + mid) * scale, 2)
            r["net"] = round(r["rs_per_g"] - r["ra_per_g"], 2)
            r["scale"] = round(scale, 3)
            r["league_rpg"] = round(actual_rpg, 2)

    out.sort(key=lambda r: -r["true_wpct"])
    for i, r in enumerate(out, 1):
        r["rank"] = i
        r["luck"] = (round((r["actual_wpct"] - r["true_wpct"]) * 162, 1)
                     if r.get("actual_wpct") is not None else None)
    return out


# ----------------------------------------------------------------- one day --
def build_date(date_str: str, lg: League, sd: dict, manual: dict) -> dict:
    print(f"[{date_str}] fetching schedule…")
    games = schedule(date_str)
    if not games:
        print("  no games")
        return {"date": date_str, "games": [], "generated_at": _now()}

    team_ids = sorted({g["away_id"] for g in games} | {g["home_id"] for g in games})
    print(f"  {len(games)} games, loading {len(team_ids)} rosters…")
    lg.load(team_ids)

    print("  fetching odds…")
    odds_map = espn.odds_for_date(date_str)

    out_games = []
    for gi, g in enumerate(games):
        park = parks.lookup(g["venue"], g.get("lat"), g.get("lon"))
        wx = weather.forecast(park, g["gameDate"])
        o = odds_map.get((g["away"], g["home"]), {})
        g["odds_age_h"] = _age_hours(o.get("fetched_at"))
        g["weather"] = wx

        ab = lg.bat.get(g["away_id"], [])
        hb = lg.bat.get(g["home_id"], [])
        ap = lg.pit.get(g["away_id"], [])
        hp = lg.pit.get(g["home_id"], [])

        # confirmed lineup players who are not on the active roster payload
        missing = [i for i in (g["away_lineup"] + g["home_lineup"])
                   if i not in {b["id"] for b in ab + hb}]
        extra = people_stats(missing, "hitting") if missing else {}

        a_lineup, a_conf = T.project_lineup(ab, g["away_lineup"], extra)
        h_lineup, h_conf = T.project_lineup(hb, g["home_lineup"], extra)

        a_sp = T.starter_profile(ap, g["away_sp"])
        h_sp = T.starter_profile(hp, g["home_sp"])
        need = [x["id"] for x in (g["away_sp"], g["home_sp"])
                if x and not (a_sp if x is g["away_sp"] else h_sp)]
        if need:
            px = people_stats(need, "pitching")
            a_sp = a_sp or (px.get(g["away_sp"]["id"]) if g["away_sp"] else None)
            h_sp = h_sp or (px.get(g["home_sp"]["id"]) if g["home_sp"] else None)

        a_pen = T.bullpen_composite(ap, exclude_id=(a_sp or {}).get("id"))
        h_pen = T.bullpen_composite(hp, exclude_id=(h_sp or {}).get("id"))

        a_sp_vec = lg.pitcher_vec(a_sp, True)
        h_sp_vec = lg.pitcher_vec(h_sp, True)
        a_pen_vec = lg.pitcher_vec(a_pen, False)
        h_pen_vec = lg.pitcher_vec(h_pen, False)

        hr_m, hit_m = R.park_weather_mults(park, wx)

        # away hits against the HOME staff, and vice versa
        A = T.side_matrices(a_lineup, h_sp_vec, T.tto_vector(h_sp_vec), h_pen_vec,
                            lg.baseline, (g["home_sp"] or {}).get("hand", "R"),
                            hr_m, hit_m, -C.HOME_FIELD_ADV)
        H = T.side_matrices(h_lineup, a_sp_vec, T.tto_vector(a_sp_vec), a_pen_vec,
                            lg.baseline, (g["away_sp"] or {}).get("hand", "R"),
                            hr_m, hit_m, +C.HOME_FIELD_ADV)

        a_pack = SidePack(*A, bf_mean=_bf(h_sp), bf_sd=C.SP_BF_SD,
                          bf_min=C.SP_BF_MIN, bf_max=C.SP_BF_MAX,
                          adv=R.baserunning_index(ab), no_starter=h_sp is None)
        h_pack = SidePack(*H, bf_mean=_bf(a_sp), bf_sd=C.SP_BF_SD,
                          bf_min=C.SP_BF_MIN, bf_max=C.SP_BF_MAX,
                          adv=R.baserunning_index(hb), no_starter=a_sp is None)

        sim = simulate_game(a_pack, h_pack, C.N_SIMS, seed=C.RANDOM_SEED + g["gamePk"])
        d = derive(sim, o.get("total"), o.get("rl_line", -1.5),
                   (manual.get(str(g["gamePk"]), {}).get("f5") or {}).get("total"))

        bets = price_game(g, d, o, manual.get(str(g["gamePk"])))
        best = bets[0] if bets else None
        conf = a_conf and h_conf

        # The number we publish everywhere is the one we actually bet: the
        # simulation after it has been pulled toward the no-vig market price.
        # The raw simulation is kept alongside it rather than shown in its
        # place, so a card never quotes two different win probabilities.
        p_away_f = next((b["p_final"] for b in bets
                         if b["market"] == "ML" and b["selection"] == g["away"]), d["p_away"])
        p_home_f = next((b["p_final"] for b in bets
                         if b["market"] == "ML" and b["selection"] == g["home"]), d["p_home"])
        d["p_away_final"] = round(p_away_f, 4)
        d["p_home_final"] = round(p_home_f, 4)
        d["p_sim_away"] = round(d["p_away"], 4)
        d["p_sim_home"] = round(d["p_home"], 4)

        out_games.append({
            "gamePk": g["gamePk"], "date": date_str, "start": g["gameDate"],
            "status": g["status"], "abstract": g["abstract"], "gameType": g["gameType"],
            "away": g["away"], "home": g["home"],
            "away_name": g["away_name"], "home_name": g["home_name"],
            "venue": park["name"], "park": {k: park[k] for k in ("run", "hr", "roof", "known")},
            "weather": wx,
            "away_sp": _sp_out(a_sp, g["away_sp"]), "home_sp": _sp_out(h_sp, g["home_sp"]),
            "away_pen": _pen_out(a_pen), "home_pen": _pen_out(h_pen),
            "away_lineup": _lineup_out(a_lineup), "home_lineup": _lineup_out(h_lineup),
            "lineups_confirmed": conf,
            "sim": {k: v for k, v in d.items() if k not in ("hist", "margin_hist")},
            "hist": d["hist"], "margin_hist": d["margin_hist"],
            "n_sims": C.N_SIMS,
            "model_line": {"away": fmt_american(prob_to_american(p_away_f)),
                           "home": fmt_american(prob_to_american(p_home_f)),
                           "sim_away": fmt_american(prob_to_american(d["p_away"])),
                           "sim_home": fmt_american(prob_to_american(d["p_home"])),
                           "total": round(d["fair_total"] * 2) / 2},
            "f5_fair": f5_fair(d),
            "odds": o, "bets": bets,
            "best": best,
            "rationale": rationale(g, d, best, a_sp, h_sp, wx, park, conf,
                                   p_away_f, p_home_f),
            "away_score": g["away_score"], "home_score": g["home_score"],
        })
        print(f"  [{gi+1}/{len(games)}] {g['away']}@{g['home']} "
              f"{d['mean_away']:.2f}-{d['mean_home']:.2f} "
              f"{d['p_home']*100:.1f}% home"
              + (f" | {best['label']} {best['edge_pct']:+.2f}% {best['tier']}" if best else ""))

    payload = {"date": date_str, "generated_at": _now(), "games": out_games,
               "n_games": len(out_games)}
    notes = portfolio.apply(payload)
    print(f"  portfolio: {notes['n_plays']} plays, ${notes['staked']:.2f} at risk, "
          f"{notes['n_best']} best bet(s)"
          + (", exposure scaled" if notes["exposure_scaled"] else "")
          + (", DIVERGENCE FLAG" if notes["divergence_flag"] else ""))
    return payload


def _bf(sp) -> float:
    if not sp:
        return C.SP_BF_DEFAULT
    v = sp.get("bf_per_start")
    return float(v) if v else C.SP_BF_DEFAULT


def _sp_out(p, meta):
    if not p:
        return {"name": (meta or {}).get("name", "TBA"), "posted": bool(meta),
                "era": None, "k9": None, "whip": None, "ip": None, "hand": (meta or {}).get("hand")}
    return {"id": p.get("id"), "name": p.get("name"), "posted": True,
            "hand": p.get("hand"), "era": p.get("era"), "whip": p.get("whip"),
            "k9": p.get("k9"), "bb9": p.get("bb9"), "hr9": p.get("hr9"),
            "ip": p.get("ip"), "gs": p.get("gs"), "tbf": p.get("tbf"),
            "bf_per_start": round(p.get("bf_per_start") or C.SP_BF_DEFAULT, 1)}


def _pen_out(pen):
    return {"era": round(pen.get("era", 0.0), 2), "arms": pen.get("n", 0)}


def _lineup_out(lu):
    return [{"name": b["name"], "pos": b.get("pos"), "bats": b.get("bats"),
             "pa": int(b.get("pa", 0)), "ops": b.get("ops"), "avg": b.get("avg"),
             "hr": int(b["counts"].get("hr", 0))} for b in lu]


def _age_hours(iso):
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(iso)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return max((datetime.now(timezone.utc) - t).total_seconds() / 3600.0, 0.0)
    except Exception:
        return None


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------- main ---
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--no-grade", action="store_true")
    ap.add_argument("--out", default=C.DOCS_DATA_DIR)
    args = ap.parse_args(argv)

    t0 = time.time()
    start = args.date or today_et()
    d0 = datetime.strptime(start, "%Y-%m-%d")
    dates = [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(max(args.days, 1))]

    sd = standings()
    lg = League()
    manual = load_json(os.path.join(C.DATA_DIR, "manual_odds.json"), {})

    built = []
    for ds in dates:
        payload = build_date(ds, lg, sd, manual)
        save_json(os.path.join(args.out, f"slate-{ds}.json"), payload)
        built.append(payload)

    print("building power ratings…")
    ratings = power_ratings(lg, sd)
    save_json(os.path.join(args.out, "ratings.json"),
              {"generated_at": _now(), "teams": ratings})

    # shadow book: record every call, then grade whatever has finished
    from .grade import record_calls, grade_all, summarise, results_map
    for p in built:
        record_calls(p)
    if not args.no_grade:
        grade_all()
    perf = summarise()
    save_json(os.path.join(args.out, "performance.json"), perf)
    save_json(os.path.join(args.out, "results.json"),
              {"generated_at": _now(), "games": results_map()})

    index = {
        "generated_at": _now(),
        "dates": sorted({*_existing_dates(args.out), *dates}),
        "latest": built[0]["date"] if built else start,
        "bankroll": C.BANKROLL,
        "settings": {"kelly": C.KELLY_FRACTION, "max_stake_pct": C.MAX_STAKE_PCT,
                     "market_blend": C.MARKET_BLEND, "edge_ceiling": C.EDGE_CEILING,
                     "edge_ceiling_total": C.EDGE_CEILING_TOT,
                     "tier_best": C.TIER_BEST, "tier_good": C.TIER_GOOD,
                     "tier_lean": C.TIER_LEAN,
                     "max_best_bets": C.MAX_BEST_BETS_PER_SLATE,
                     "max_plays": C.MAX_PLAYS_PER_SLATE,
                     "max_slate_exposure_pct": C.MAX_SLATE_EXPOSURE_PCT,
                     "n_sims": C.N_SIMS, "season": C.SEASON},
        "record": perf.get("overall", {}),
    }
    save_json(os.path.join(args.out, "index.json"), index)
    if built:
        save_json(os.path.join(args.out, "latest.json"), built[0])

    print(f"done in {time.time()-t0:.1f}s -> {args.out}")
    return 0


def _existing_dates(out_dir):
    try:
        return [f[6:-5] for f in os.listdir(out_dir)
                if f.startswith("slate-") and f.endswith(".json")]
    except FileNotFoundError:
        return []


if __name__ == "__main__":
    sys.exit(main())
