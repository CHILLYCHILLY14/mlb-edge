"""Assemble a team's batting order and pitching staff into model inputs."""
from __future__ import annotations
import numpy as np

from .. import config as C
from . import rates as R

POSITION_PLAYER = lambda p: (p.get("pos") or "?").upper() not in ("P", "SP", "RP", "TWP")


def project_lineup(batters: list[dict], confirmed_ids: list[int],
                   extra: dict | None = None) -> tuple[list[dict], bool]:
    """
    Confirmed batting order if the API has posted one, otherwise the nine
    position players with the most plate appearances. Returns (lineup, confirmed).
    """
    by_id = {b["id"]: b for b in batters}
    if extra:
        by_id.update({k: v for k, v in extra.items() if v})
    if confirmed_ids:
        lu = [by_id[i] for i in confirmed_ids[:9] if i in by_id]
        if len(lu) >= 8:
            while len(lu) < 9:
                lu.append(_replacement())
            return lu[:9], True
    pool = [b for b in batters if POSITION_PLAYER(b) and b["pa"] >= C.MIN_PA_LINEUP]
    pool.sort(key=lambda b: -b["pa"])
    lu = pool[:9]
    while len(lu) < 9:
        lu.append(_replacement())
    return lu, False


def _replacement() -> dict:
    return {"id": None, "name": "Replacement", "pos": "?", "bats": "R", "pa": 0.0,
            "sb": 0.0, "counts": {"bb": 0, "k": 0, "s": 0, "d": 0, "t": 0, "hr": 0},
            "ops": 0.0, "obp": 0.0, "slg": 0.0, "avg": 0.0}


def starter_profile(pitchers: list[dict], sp_meta: dict | None,
                    extra: dict | None = None) -> dict | None:
    """Find the probable starter's season line."""
    if not sp_meta:
        return None
    pid = sp_meta.get("id")
    for p in pitchers:
        if p["id"] == pid:
            return p
    if extra and pid in extra:
        return extra[pid]
    return None


def bullpen_composite(pitchers: list[dict], exclude_id=None) -> dict:
    """
    One synthetic reliever standing in for the whole pen.

    Relievers are weighted by batters faced and then again by how often the
    manager trusts them late - saves plus holds per appearance - so the arms
    that actually pitch the seventh through ninth of a close game drive the
    number instead of the long man who ate five runs in a blowout.
    """
    tot = {k: 0.0 for k in ("bb", "k", "s", "d", "t", "hr")}
    denom = 0.0
    n = 0
    era_w, era_d = 0.0, 0.0
    for p in pitchers:
        if p["id"] == exclude_id or p.get("is_sp"):
            continue
        tbf = p.get("tbf", 0.0)
        if tbf < 15:
            continue
        gp = max(p.get("gp", 1.0), 1.0)
        lev = (p.get("saves", 0.0) + p.get("holds", 0.0)) / gp
        w = 1.0 + min(lev, 0.6) * 1.2
        for k in tot:
            tot[k] += p["counts"].get(k, 0.0) * w
        denom += tbf * w
        era_w += p.get("era", 0.0) * tbf
        era_d += tbf
        n += 1
    return {"counts": tot, "tbf": denom, "n": n,
            "era": (era_w / era_d) if era_d else 0.0}


def side_matrices(lineup, opp_sp_vec, opp_sp3_vec, opp_pen_vec, league,
                  sp_hand, hr_mult, hit_mult, hfa):
    """
    Build the three 9x7 outcome matrices for one batting side.

    Order of operations matters: matchup first (log5 against that pitcher),
    then platoon, then park and weather, then home field. Each step is a
    multiplier on specific buckets with the in-play out bucket absorbing the
    remainder, so every matrix is still a valid probability distribution.
    """
    P_sp = np.zeros((9, R.NOUT))
    P_sp3 = np.zeros((9, R.NOUT))
    P_pen = np.zeros((9, R.NOUT))
    hfa_hr = 1.0 + 2.2 * hfa
    hfa_hit = 1.0 + 0.6 * hfa
    for i, b in enumerate(lineup):
        bat = R.shrink(b["counts"], b["pa"], league, C.PRIOR_PA_BATTER)
        pl_hr, pl_hit, pl_k = R.platoon_mults(b.get("bats", "R"), sp_hand)

        v = R.log5(bat, opp_sp_vec, league)
        P_sp[i] = R.apply_multipliers(v, hr_mult * pl_hr * hfa_hr,
                                      hit_mult * pl_hit * hfa_hit, pl_k)

        v3 = R.log5(bat, opp_sp3_vec, league)
        P_sp3[i] = R.apply_multipliers(v3, hr_mult * pl_hr * hfa_hr,
                                       hit_mult * pl_hit * hfa_hit, pl_k)

        vp = R.log5(bat, opp_pen_vec, league)      # bullpens are mixed-handed
        P_pen[i] = R.apply_multipliers(vp, hr_mult * hfa_hr, hit_mult * hfa_hit)
    return P_sp, P_sp3, P_pen


def tto_vector(sp_vec: np.ndarray) -> np.ndarray:
    """The starter's rates the third time through the order."""
    t = C.TTO_PENALTY
    return R.apply_multipliers(sp_vec, 1.0 + 2.0 * t, 1.0 + t, 1.0 - 0.5 * t)
