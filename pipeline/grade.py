"""
The shadow book.

Every call the model makes is recorded - BEST BET, GOOD, LEAN and PASS alike -
and every one of them is graded once the game is final. Grading only the bets
you would have placed tells you nothing about whether PASS was the right call,
which is exactly the number you need to know whether the thresholds are set in
the right place.

Also tracks closing line value, because beating the closing number is the only
short-run evidence that an edge was real rather than lucky.
"""
from __future__ import annotations
import json, os
from collections import defaultdict
from datetime import datetime, timezone

from . import config as C
from .model.market import american_to_decimal, price_ok, american_to_prob, no_vig
from .sources.mlb_api import final_scores

SHADOW = os.path.join(C.DATA_DIR, "shadow.json")


def _load():
    try:
        with open(SHADOW) as fh:
            return json.load(fh)
    except Exception:
        return {"calls": {}}


def _save(db):
    os.makedirs(C.DATA_DIR, exist_ok=True)
    tmp = SHADOW + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(db, fh, separators=(",", ":"))
    os.replace(tmp, SHADOW)


def _key(gamePk, market, selection):
    return f"{gamePk}|{market}|{selection}"


# ------------------------------------------------------------- recording ----
def record_calls(payload: dict) -> None:
    """Snapshot today's calls. First price seen becomes the open; every later
    run refreshes the close, which is what CLV is measured against."""
    db = _load()
    calls = db["calls"]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for g in payload.get("games", []):
        if g.get("gameType") not in ("R", "F", "D", "L", "W", None):
            continue                                    # skip exhibitions
        for b in g.get("bets", []):
            k = _key(g["gamePk"], b["market"], b["selection"])
            row = calls.get(k)
            if row is None:
                row = {
                    "gamePk": g["gamePk"], "date": g["date"], "start": g["start"],
                    "away": g["away"], "home": g["home"],
                    "market": b["market"], "selection": b["selection"],
                    "label": b["label"], "book": b.get("book"),
                    "open_price": b["price"], "open_at": now,
                    "open_total": (g.get("odds") or {}).get("total"),
                    "p_model_open": b["p_final"],
                    "status": "pending",
                }
                calls[k] = row
            if row.get("status") != "pending":
                continue
            row.update({
                "close_price": b["price"], "close_at": now,
                "close_total": (g.get("odds") or {}).get("total"),
                "p_model": b["p_final"], "p_market": b.get("p_market"),
                "edge": b["edge"], "tier": b["tier"], "stake": b["stake"],
                "line": (g.get("odds") or {}).get("total") if "TOTAL" in b["market"] else
                        (g.get("odds") or {}).get("rl_line") if b["market"] == "RL" else None,
                "lineups_confirmed": g.get("lineups_confirmed"),
            })
    _save(db)


# --------------------------------------------------------------- grading ----
def grade_all(max_dates: int = 14) -> int:
    db = _load()
    calls = db["calls"]
    pend = [r for r in calls.values() if r.get("status") == "pending"]
    if not pend:
        return 0
    dates = sorted({r["date"] for r in pend})[-max_dates:]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    graded = 0
    for ds in dates:
        if ds > today:
            continue
        finals = final_scores(ds)
        if not finals:
            continue
        for r in [x for x in pend if x["date"] == ds]:
            f = finals.get(r["gamePk"])
            if not f or f.get("away") is None or f.get("home") is None:
                continue
            res = _settle(r, f)
            if res is None:
                continue
            r.update(res)
            r["status"] = "graded"
            graded += 1
    if graded:
        _save(db)
        print(f"  graded {graded} calls")
    return graded


def _settle(r, f) -> dict | None:
    a, h = float(f["away"]), float(f["home"])
    m, sel = r["market"], r["selection"]
    line = r.get("line")
    result = None

    if m == "ML":
        won = (a > h) if sel == r["away"] else (h > a)
        result = "win" if won else "loss"
    elif m == "RL":
        # signed line, stored from the home team's point of view
        ln = float(line if line is not None else -1.5)
        marg = h - a
        cover = (marg + ln) if sel == r["home"] else (-marg - ln)
        result = "win" if cover > 0 else ("push" if cover == 0 else "loss")
    elif m == "TOTAL":
        tot = a + h
        ln = float(line if line is not None else r.get("close_total") or 0)
        if not ln:
            return None
        if tot == ln:
            result = "push"
        elif sel == "Over":
            result = "win" if tot > ln else "loss"
        else:
            result = "win" if tot < ln else "loss"
    elif m == "F5 ML":
        fa, fh = float(f.get("f5_away", 0)), float(f.get("f5_home", 0))
        if f.get("innings_played", 9) < 5:
            return None
        if fa == fh:
            result = "push"
        else:
            won = (fa > fh) if sel == r["away"] else (fh > fa)
            result = "win" if won else "loss"
    elif m == "F5 TOTAL":
        fa, fh = float(f.get("f5_away", 0)), float(f.get("f5_home", 0))
        if f.get("innings_played", 9) < 5:
            return None
        tot, ln = fa + fh, float(line or 0)
        if not ln:
            return None
        result = "push" if tot == ln else (
            "win" if ((tot > ln) == (sel == "Over")) else "loss")
    if result is None:
        return None

    stake = float(r.get("stake") or 0.0)
    # Settle at the price actually recorded. A call with no price stored is a
    # call we could not have made, so it grades as no result rather than being
    # scored at an assumed -110 and quietly polluting the accuracy record.
    price = r.get("close_price") or r.get("open_price")
    if not price_ok(price):
        return None
    price = float(price)
    if result == "win":
        pl = stake * (american_to_decimal(price) - 1.0)
    elif result == "loss":
        pl = -stake
    else:
        pl = 0.0

    clv = None
    op, cp = r.get("open_price"), r.get("close_price")
    if op is not None and cp is not None:
        clv = round((american_to_prob(cp) - american_to_prob(op)) * -100, 2)
        # positive clv = we took a better number than the market closed at

    return {"result": result, "pl": round(pl, 2), "final_away": a, "final_home": h,
            "f5_away": f.get("f5_away"), "f5_home": f.get("f5_home"),
            "clv": clv, "graded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


# ------------------------------------------------------------- summaries ----
def summarise() -> dict:
    db = _load()
    rows = [r for r in db["calls"].values() if r.get("status") == "graded"]
    rows.sort(key=lambda r: (r.get("date", ""), r.get("gamePk", 0)))

    def block(subset):
        w = sum(1 for r in subset if r["result"] == "win")
        l = sum(1 for r in subset if r["result"] == "loss")
        p = sum(1 for r in subset if r["result"] == "push")
        staked = sum(float(r.get("stake") or 0) for r in subset)
        pl = sum(float(r.get("pl") or 0) for r in subset)
        clvs = [r["clv"] for r in subset if r.get("clv") is not None]
        return {"n": len(subset), "w": w, "l": l, "p": p,
                "win_pct": round(w / max(w + l, 1), 4),
                "staked": round(staked, 2), "pl": round(pl, 2),
                "roi": round(pl / staked, 4) if staked > 0 else None,
                "avg_clv": round(sum(clvs) / len(clvs), 2) if clvs else None}

    placed = [r for r in rows if float(r.get("stake") or 0) > 0]
    by_tier = {t: block([r for r in rows if r.get("tier") == t])
               for t in ("BEST BET", "GOOD", "LEAN", "PASS")}
    by_market = {m: block([r for r in rows if r.get("market") == m])
                 for m in sorted({r.get("market", "?") for r in rows})}

    # bankroll curve over placed bets
    curve, bal = [], C.BANKROLL
    for r in placed:
        bal += float(r.get("pl") or 0)
        curve.append({"date": r["date"], "label": r["label"],
                      "pl": r.get("pl"), "balance": round(bal, 2)})

    # calibration: did things the model called 60% actually happen 60% of the time
    buckets = defaultdict(lambda: {"n": 0, "hit": 0, "sum_p": 0.0})
    for r in rows:
        if r["result"] == "push":
            continue
        p = float(r.get("p_model") or 0)
        b = buckets[min(int(p * 10), 9)]
        b["n"] += 1
        b["hit"] += 1 if r["result"] == "win" else 0
        b["sum_p"] += p
    calib = [{"bucket": f"{k*10}-{k*10+10}%", "n": v["n"],
              "predicted": round(v["sum_p"] / v["n"], 4) if v["n"] else None,
              "actual": round(v["hit"] / v["n"], 4) if v["n"] else None}
             for k, v in sorted(buckets.items()) if v["n"] >= 5]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall": block(placed),
        "all_calls": block(rows),
        "by_tier": by_tier, "by_market": by_market,
        "last20": block(placed[-20:]),
        "last50": block(placed[-50:]),
        "curve": curve[-250:],
        "calibration": calib,
        "bankroll_start": C.BANKROLL,
        "bankroll_now": round(C.BANKROLL + sum(float(r.get("pl") or 0) for r in placed), 2),
        "open_calls": sum(1 for r in db["calls"].values() if r.get("status") == "pending"),
        "ledger": [
            {k: r.get(k) for k in ("gamePk", "date", "away", "home", "label", "market", "tier",
                                   "close_price", "stake", "result", "pl", "clv",
                                   "final_away", "final_home", "p_model", "edge")}
            for r in rows[-300:]
        ],
    }


def results_map() -> dict:
    """
    Final scores by gamePk, for the dashboard to settle a personal ledger with.

    The browser cannot call the MLB API from a static page, and asking it to
    would double the traffic anyway. Everything the shadow book already learned
    when it graded its own calls is published here so a bet you clicked into
    your own ledger settles by exactly the same rule the model's did.
    """
    db = _load()
    out = {}
    for r in db["calls"].values():
        if r.get("status") != "graded" or r.get("final_away") is None:
            continue
        out[str(r["gamePk"])] = {
            "date": r.get("date"), "away": r.get("away"), "home": r.get("home"),
            "away_score": r.get("final_away"), "home_score": r.get("final_home"),
            "f5_away": r.get("f5_away"), "f5_home": r.get("f5_home"),
        }
    return out
