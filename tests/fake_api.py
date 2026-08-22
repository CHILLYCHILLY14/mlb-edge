"""
A synthetic stand-in for every upstream feed, shaped exactly like the real
payloads. Lets the whole pipeline run end-to-end with no network so the maths,
the pricing, the grading and the JSON contract are all testable in CI.
"""
from __future__ import annotations
import json, random, re
from datetime import datetime, timedelta, timezone

from pipeline.sources.mlb_api import TEAM_ABBR
from pipeline.sources import parks as P

RNG = random.Random(1234)
TEAM_IDS = list(TEAM_ABBR.keys())

VENUE_FOR = {
    108: "Angel Stadium", 109: "Chase Field", 110: "Oriole Park at Camden Yards",
    111: "Fenway Park", 112: "Wrigley Field", 113: "Great American Ball Park",
    114: "Progressive Field", 115: "Coors Field", 116: "Comerica Park",
    117: "Daikin Park", 118: "Kauffman Stadium", 119: "Dodger Stadium",
    120: "Nationals Park", 121: "Citi Field", 133: "Sutter Health Park",
    134: "PNC Park", 135: "Petco Park", 136: "T-Mobile Park", 137: "Oracle Park",
    138: "Busch Stadium", 139: "George M. Steinbrenner Field", 140: "Globe Life Field",
    141: "Rogers Centre", 142: "Target Field", 143: "Citizens Bank Park",
    144: "Truist Park", 145: "Rate Field", 146: "loanDepot park",
    147: "Yankee Stadium", 158: "American Family Field",
}

# fixed per-team talent so repeated calls agree with each other
TALENT = {tid: {"off": RNG.gauss(0, 0.045), "sp": RNG.gauss(0, 0.050),
                "pen": RNG.gauss(0, 0.040)} for tid in TEAM_IDS}
_PID = {}


def pid(tid, i, kind):
    return _PID.setdefault((tid, i, kind), 100000 + len(_PID))


def _hitting_line(rng, quality):
    pa = rng.randint(220, 620)
    hr = max(0, int(pa * max(0.005, rng.gauss(0.034 * (1 + quality), 0.014))))
    bb = int(pa * max(0.02, rng.gauss(0.085 * (1 + quality * 0.5), 0.028)))
    so = int(pa * min(0.42, max(0.08, rng.gauss(0.222 - quality * 0.03, 0.055))))
    d2 = int(pa * max(0.01, rng.gauss(0.045 * (1 + quality), 0.013)))
    d3 = int(pa * max(0.0, rng.gauss(0.004, 0.003)))
    s1 = int(pa * max(0.05, rng.gauss(0.140 * (1 + quality * 0.6), 0.025)))
    hits = s1 + d2 + d3 + hr
    ab = pa - bb - int(pa * 0.01)
    return {"plateAppearances": pa, "atBats": ab, "hits": hits, "doubles": d2,
            "triples": d3, "homeRuns": hr, "baseOnBalls": bb, "intentionalWalks": 2,
            "hitByPitch": int(pa * 0.01), "strikeOuts": so, "sacFlies": 3,
            "stolenBases": rng.randint(0, 14), "rbi": int(hits * 0.55),
            "runs": int(hits * 0.55), "avg": f"{hits/max(ab,1):.3f}",
            "obp": f"{(hits+bb)/max(pa,1):.3f}",
            "slg": f"{(s1+2*d2+3*d3+4*hr)/max(ab,1):.3f}",
            "ops": f"{((hits+bb)/max(pa,1))+((s1+2*d2+3*d3+4*hr)/max(ab,1)):.3f}"}


def _pitching_line(rng, quality, starter):
    tbf = rng.randint(500, 750) if starter else rng.randint(90, 300)
    gs = rng.randint(18, 30) if starter else 0
    gp = gs if starter else rng.randint(35, 70)
    so = int(tbf * max(0.10, rng.gauss(0.225 + quality * 0.04, 0.045)))
    bb = int(tbf * max(0.02, rng.gauss(0.080 - quality * 0.02, 0.022)))
    hr = int(tbf * max(0.005, rng.gauss(0.032 - quality * 0.012, 0.011)))
    d2 = int(tbf * max(0.015, rng.gauss(0.045 - quality * 0.008, 0.010)))
    d3 = int(tbf * 0.004)
    s1 = int(tbf * max(0.06, rng.gauss(0.140 - quality * 0.02, 0.020)))
    hits = s1 + d2 + d3 + hr
    outs = int(tbf * 0.70)
    ip = f"{outs//3}.{outs%3}"
    er = int((hits * 0.35 + hr * 1.4))
    era = er * 9 / max(outs / 3, 1)
    return {"battersFaced": tbf, "gamesStarted": gs, "gamesPitched": gp,
            "gamesPlayed": gp, "inningsPitched": ip, "strikeOuts": so,
            "baseOnBalls": bb, "intentionalWalks": 1, "hitByPitch": int(tbf * 0.01),
            "hits": hits, "doubles": d2, "triples": d3, "homeRuns": hr,
            "earnedRuns": er, "era": f"{era:.2f}",
            "whip": f"{(hits+bb)/max(outs/3,1):.2f}",
            "strikeoutsPer9Inn": f"{so*9/max(outs/3,1):.2f}",
            "walksPer9Inn": f"{bb*9/max(outs/3,1):.2f}",
            "homeRunsPer9": f"{hr*9/max(outs/3,1):.2f}",
            "saves": rng.randint(0, 25) if not starter else 0,
            "holds": rng.randint(0, 20) if not starter else 0}


def _roster(tid, group):
    rng = random.Random(tid * 31 + (7 if group == "hitting" else 13))
    t = TALENT[tid]
    people = []
    if group == "hitting":
        for i in range(14):
            q = t["off"] + rng.gauss(0, 0.060)
            people.append({
                "person": {"id": pid(tid, i, "b"), "fullName": f"{TEAM_ABBR[tid]} Batter {i+1}",
                           "primaryPosition": {"abbreviation": ["C","1B","2B","3B","SS","LF","CF","RF","DH"][i % 9]},
                           "batSide": {"code": rng.choice("RRRLLS")},
                           "stats": [{"splits": [{"stat": _hitting_line(rng, q)}]}]}})
    else:
        for i in range(6):
            q = t["sp"] + rng.gauss(0, 0.045)
            people.append({"person": {"id": pid(tid, i, "p"), "fullName": f"{TEAM_ABBR[tid]} Starter {i+1}",
                                      "pitchHand": {"code": rng.choice("RRRL")},
                                      "primaryPosition": {"abbreviation": "P"},
                                      "stats": [{"splits": [{"stat": _pitching_line(rng, q, True)}]}]}})
        for i in range(9):
            q = t["pen"] + rng.gauss(0, 0.050)
            people.append({"person": {"id": pid(tid, 100 + i, "p"), "fullName": f"{TEAM_ABBR[tid]} Reliever {i+1}",
                                      "pitchHand": {"code": rng.choice("RRRL")},
                                      "primaryPosition": {"abbreviation": "P"},
                                      "stats": [{"splits": [{"stat": _pitching_line(rng, q, False)}]}]}})
    return {"roster": people}


def _matchups(date_str):
    rng = random.Random(hash(date_str) & 0xFFFF)
    ids = TEAM_IDS[:]
    rng.shuffle(ids)
    return [(ids[i], ids[i + 1]) for i in range(0, 30, 2)]


def _schedule(date_str, final=False):
    games = []
    base = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc) + timedelta(hours=23)
    for n, (away, home) in enumerate(_matchups(date_str)):
        rng = random.Random(hash((date_str, away, home)) & 0xFFFFFF)
        venue = VENUE_FOR[home]
        pk = P.lookup(venue)
        gp = 800000 + n + (hash(date_str) & 0xFFFF)
        g = {
            "gamePk": gp, "gameDate": (base + timedelta(minutes=15 * n)).isoformat().replace("+00:00", "Z"),
            "gameType": "R", "dayNight": "night", "doubleHeader": "N",
            "status": {"detailedState": "Final" if final else "Scheduled",
                       "abstractGameState": "Final" if final else "Preview"},
            "venue": {"name": venue, "location": {"defaultCoordinates":
                      {"latitude": pk["lat"], "longitude": pk["lon"]}}},
            "teams": {
                "away": {"team": {"id": away, "name": TEAM_ABBR[away], "abbreviation": TEAM_ABBR[away]},
                         "probablePitcher": {"id": pid(away, rng.randint(0, 4), "p"),
                                             "fullName": f"{TEAM_ABBR[away]} Starter",
                                             "pitchHand": {"code": "R"}}},
                "home": {"team": {"id": home, "name": TEAM_ABBR[home], "abbreviation": TEAM_ABBR[home]},
                         "probablePitcher": {"id": pid(home, rng.randint(0, 4), "p"),
                                             "fullName": f"{TEAM_ABBR[home]} Starter",
                                             "pitchHand": {"code": "L"}}}},
            "lineups": {} if n % 3 else {
                "awayPlayers": [{"id": pid(away, i, "b")} for i in range(9)],
                "homePlayers": [{"id": pid(home, i, "b")} for i in range(9)]},
        }
        if final:
            ar, hr_ = rng.randint(0, 11), rng.randint(0, 11)
            if ar == hr_:
                hr_ += 1
            g["teams"]["away"]["score"] = ar
            g["teams"]["home"]["score"] = hr_
            g["linescore"] = {"innings": [
                {"away": {"runs": 1 if i < ar else 0}, "home": {"runs": 1 if i < hr_ else 0}}
                for i in range(9)]}
        games.append(g)
    return {"totalGames": len(games), "dates": [{"date": date_str, "games": games}]}


def _standings():
    recs = []
    for i in range(0, 30, 5):
        trs = []
        for tid in TEAM_IDS[i:i + 5]:
            rng = random.Random(tid)
            w = rng.randint(50, 80)
            trs.append({"team": {"id": tid, "name": TEAM_ABBR[tid]}, "wins": w,
                        "losses": 128 - w, "runsScored": rng.randint(480, 660),
                        "runsAllowed": rng.randint(480, 660),
                        "runDifferential": rng.randint(-120, 140),
                        "winningPercentage": f"{w/128:.3f}",
                        "streak": {"streakCode": "W2"}, "gamesBack": "-",
                        "records": {"splitRecords": [{"type": "lastTen", "wins": 5, "losses": 5}]}})
        recs.append({"division": {"id": 200 + i}, "teamRecords": trs})
    return {"records": recs}


def _espn(date_str):
    events = []
    for away, home in _matchups(date_str):
        rng = random.Random(hash((date_str, away, home, "odds")) & 0xFFFFFF)
        # a market that is roughly right, the way a real one is: price off the
        # talent gap plus home field, then add a little noise and some vig
        gap = (TALENT[home]["off"] + TALENT[home]["sp"] + TALENT[home]["pen"]
               - TALENT[away]["off"] - TALENT[away]["sp"] - TALENT[away]["pen"])
        p_home = min(max(0.532 + gap * 1.6 + rng.gauss(0, 0.02), 0.28), 0.72)
        def price(p):
            p = min(max(p * 1.022, 0.02), 0.97)
            return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)
        ml_h, ml_a = price(p_home), price(1 - p_home)
        fav_home = p_home >= 0.5
        events.append({"id": str(rng.randint(1, 10**6)), "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"abbreviation": TEAM_ABBR[home]}, "score": None},
                {"homeAway": "away", "team": {"abbreviation": TEAM_ABBR[away]}, "score": None}],
            "odds": [{"provider": {"name": "DraftKings"},
                      "details": f"{TEAM_ABBR[home if fav_home else away]} -1.5",
                      "spread": -1.5, "overUnder": rng.choice([7.0, 7.5, 8.0, 8.5, 9.0, 9.5]),
                      "overOdds": -110, "underOdds": -110,
                      "awayTeamOdds": {"moneyLine": ml_a, "spreadOdds": rng.choice([-135, 145])},
                      "homeTeamOdds": {"moneyLine": ml_h, "spreadOdds": rng.choice([-135, 145])}}],
            "status": {"type": {"completed": False}}}]})
    return {"events": events}


def _weather():
    rng = random.Random(99)
    times = [(datetime.now(timezone.utc) + timedelta(hours=h)).strftime("%Y-%m-%dT%H:00")
             for h in range(-24, 96)]
    n = len(times)
    return {"hourly": {"time": times,
                       "temperature_2m": [rng.uniform(55, 95) for _ in range(n)],
                       "relative_humidity_2m": [rng.uniform(25, 90) for _ in range(n)],
                       "precipitation_probability": [rng.choice([0, 0, 5, 10, 40, 70]) for _ in range(n)],
                       "wind_speed_10m": [rng.uniform(0, 18) for _ in range(n)],
                       "wind_direction_10m": [rng.uniform(0, 360) for _ in range(n)],
                       "apparent_temperature": [rng.uniform(55, 95) for _ in range(n)]}}


FINAL_DATES: set[str] = set()


def responder(url: str, **kw):
    """Route a URL to a synthetic payload."""
    if "statsapi.mlb.com" in url:
        if "/schedule?" in url:
            d = re.search(r"date=(\d{4}-\d{2}-\d{2})", url).group(1)
            return _schedule(d, final=(d in FINAL_DATES))
        if "/standings?" in url:
            return _standings()
        m = re.search(r"/teams/(\d+)/roster", url)
        if m:
            group = "hitting" if "group=hitting" in url else "pitching"
            return _roster(int(m.group(1)), group)
        if "/people?" in url:
            ids = re.search(r"personIds=([\d,]+)", url)
            group = "hitting" if "group=hitting" in url else "pitching"
            people = []
            for i in (ids.group(1).split(",") if ids else []):
                rng = random.Random(int(i))
                st = _hitting_line(rng, 0) if group == "hitting" else _pitching_line(rng, 0, False)
                people.append({"id": int(i), "fullName": f"Player {i}",
                               "primaryPosition": {"abbreviation": "DH" if group == "hitting" else "P"},
                               "batSide": {"code": "R"}, "pitchHand": {"code": "R"},
                               "stats": [{"splits": [{"stat": st}]}]})
            return {"people": people}
        if "/stats?" in url:
            return {"stats": []}
    if "site.api.espn.com" in url:
        d = re.search(r"dates=(\d{8})", url).group(1)
        return _espn(f"{d[:4]}-{d[4:6]}-{d[6:]}")
    if "api.open-meteo.com" in url:
        return _weather()
    return None
