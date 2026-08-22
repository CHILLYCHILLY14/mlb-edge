"""Turn simulated probabilities plus market prices into graded, staked bets."""
from __future__ import annotations

from .. import config as C
from .market import (american_to_decimal, blend, cap_prob, compress, fmt_american,
                     kelly_stake, lock_rules, no_vig, prob_to_american, raw_edge,
                     tier_for)


def _bet(market, selection, label, price, p_model, p_market, blend_w, ceiling,
         ctx, is_total=False, total_gap=None, book=None, line=None):
    if price is None:
        return None
    p_final = cap_prob(blend(p_model, p_market, blend_w))
    e_raw = raw_edge(p_final, price)
    e_c = compress(e_raw, ceiling)
    stake, kf = kelly_stake(e_c, price, C.BANKROLL)
    tier = tier_for(e_c)
    fails = []
    if tier == "BEST BET":
        fails = lock_rules(price=price, p_model=p_final, p_market=p_market,
                           odds_age_h=ctx.get("odds_age_h"), sim_se=ctx.get("se"),
                           both_sp=ctx.get("both_sp", False), precip=ctx.get("precip"),
                           is_total=is_total, total_gap=total_gap)
        if fails:
            tier = "GOOD"
    if tier == "PASS":
        stake, kf = 0.0, 0.0
    return {
        "market": market, "selection": selection, "label": label,
        "price": price, "price_txt": fmt_american(price), "book": book,
        "line": line,
        "p_model": round(p_model, 4), "p_market": (round(p_market, 4) if p_market is not None else None),
        "p_final": round(p_final, 4),
        "fair_price": fmt_american(prob_to_american(p_final)),
        "edge_raw": round(e_raw, 4), "edge": round(e_c, 4),
        "edge_pct": round(e_c * 100, 2),
        "tier": tier, "stake": round(stake, 2), "kelly": round(kf, 4),
        "to_win": round(stake * (american_to_decimal(price) - 1.0), 2),
        "lock_fails": fails,
        "decimal": round(american_to_decimal(price), 3),
    }


def price_game(g: dict, d: dict, odds: dict | None, manual: dict | None = None) -> list[dict]:
    """All priced markets for one game, most edge first."""
    odds = odds or {}
    manual = manual or {}
    ctx = {"odds_age_h": g.get("odds_age_h"), "se": d.get("se"),
           "both_sp": bool(g.get("away_sp") and g.get("home_sp")),
           "precip": (g.get("weather") or {}).get("precip")}
    away, home = g["away"], g["home"]
    bets = []

    # ---------------------------------------------------------- moneyline ---
    ml_a, ml_h = odds.get("ml_away"), odds.get("ml_home")
    mk_a, mk_h = no_vig(ml_a, ml_h)
    bets.append(_bet("ML", away, f"{away} ML", ml_a, d["p_away"], mk_a,
                     C.MARKET_BLEND, C.EDGE_CEILING, ctx, book=odds.get("book")))
    bets.append(_bet("ML", home, f"{home} ML", ml_h, d["p_home"], mk_h,
                     C.MARKET_BLEND, C.EDGE_CEILING, ctx, book=odds.get("book")))

    # ----------------------------------------------------------- run line ---
    rl = odds.get("rl_line", -1.5)
    rl_h, rl_a = odds.get("rl_home"), odds.get("rl_away")
    mk_rh, mk_ra = no_vig(rl_h, rl_a)
    bets.append(_bet("RL", home, f"{home} {rl:+.1f}", rl_h, d["p_home_rl"], mk_rh,
                     C.MARKET_BLEND, C.EDGE_CEILING, ctx, book=odds.get("book"), line=rl))
    bets.append(_bet("RL", away, f"{away} {-rl:+.1f}", rl_a, d["p_away_rl"], mk_ra,
                     C.MARKET_BLEND, C.EDGE_CEILING, ctx, book=odds.get("book"), line=rl))

    # -------------------------------------------------------------- total ---
    tot = odds.get("total")
    if tot is not None and "p_total_over" in d:
        gap = abs(d["mean_total"] - tot)
        mk_o, mk_u = no_vig(odds.get("over"), odds.get("under"))
        bets.append(_bet("TOTAL", "Over", f"Over {tot}", odds.get("over"),
                         d["p_total_over"], mk_o, C.TOTALS_BLEND, C.EDGE_CEILING_TOT,
                         ctx, is_total=True, total_gap=gap, book=odds.get("book"), line=tot))
        bets.append(_bet("TOTAL", "Under", f"Under {tot}", odds.get("under"),
                         d["p_total_under"], mk_u, C.TOTALS_BLEND, C.EDGE_CEILING_TOT,
                         ctx, is_total=True, total_gap=gap, book=odds.get("book"), line=tot))

    # ---------------------------------------------------------- first five ---
    # ESPN does not publish F5 prices. If you paste them into data/manual_odds.json
    # the model prices them exactly like any other market; without them it still
    # publishes a fair line you can shop against, and the shadow book still grades
    # the F5 call so its accuracy is measured either way.
    f5 = manual.get("f5") or {}
    f5_a, f5_h = f5.get("ml_away"), f5.get("ml_home")
    if f5_a and f5_h:
        mk = no_vig(f5_a, f5_h)
        bets.append(_bet("F5 ML", away, f"{away} F5 ML", f5_a, _f5_side(d, "away"), mk[0],
                         C.F5_BLEND, C.EDGE_CEILING, ctx, book=f5.get("book", "manual")))
        bets.append(_bet("F5 ML", home, f"{home} F5 ML", f5_h, _f5_side(d, "home"), mk[1],
                         C.F5_BLEND, C.EDGE_CEILING, ctx, book=f5.get("book", "manual")))
    f5t = f5.get("total")
    if f5t is not None and "p_f5_over" in d:
        mk_o, mk_u = no_vig(f5.get("over", -110), f5.get("under", -110))
        gap = abs(d["mean_f5_total"] - f5t)
        bets.append(_bet("F5 TOTAL", "Over", f"F5 Over {f5t}", f5.get("over", -110),
                         d["p_f5_over"], mk_o, C.F5_BLEND, C.EDGE_CEILING_TOT, ctx,
                         is_total=True, total_gap=gap, book=f5.get("book", "manual"), line=f5t))
        bets.append(_bet("F5 TOTAL", "Under", f"F5 Under {f5t}", f5.get("under", -110),
                         d["p_f5_under"], mk_u, C.F5_BLEND, C.EDGE_CEILING_TOT, ctx,
                         is_total=True, total_gap=gap, book=f5.get("book", "manual"), line=f5t))

    bets = [b for b in bets if b]
    bets.sort(key=lambda b: -b["edge"])
    return bets


def _f5_side(d: dict, side: str) -> float:
    """F5 moneylines are three-way (ties refund on some books, lose on others).
    We price the two-way 'wins the first five' market, ties excluded."""
    a, h, t = d["p_f5_away"], d["p_f5_home"], d["p_f5_tie"]
    denom = max(a + h, 1e-9)
    return (a / denom) if side == "away" else (h / denom)


def f5_fair(d: dict) -> dict:
    """Fair first-five prices so you can shop them even with no market feed."""
    a = _f5_side(d, "away")
    return {"away": fmt_american(prob_to_american(a)),
            "home": fmt_american(prob_to_american(1 - a)),
            "total": round(d["mean_f5_total"] * 2) / 2,
            "away_pct": round(a * 100, 1), "home_pct": round((1 - a) * 100, 1),
            "tie_pct": round(d["p_f5_tie"] * 100, 1)}
