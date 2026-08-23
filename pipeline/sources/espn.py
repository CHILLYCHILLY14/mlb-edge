"""
ESPN public scoreboard - keyless moneyline / run line / total prices.

ESPN has shipped several odds payload shapes over the years; this reads all of
them and returns a single normalised record per game.
"""
from __future__ import annotations
from datetime import datetime, timezone

from .http import get_json

SB = ("https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
      "?dates={d}&limit=100")

# ESPN abbreviation -> our canonical abbreviation
ESPN_MAP = {"ARI": "AZ", "CHW": "CWS", "WSH": "WSH", "SFG": "SF", "SDG": "SD",
            "TBR": "TB", "KCR": "KC", "OAK": "ATH", "ATH": "ATH", "LAA": "LAA"}

# books we prefer, best first
BOOK_RANK = ["ESPN BET", "DraftKings", "FanDuel", "Caesars", "BetMGM",
             "William Hill (New Jersey)", "Bet365", "consensus"]


def _num(v):
    try:
        if v is None or v == "" or str(v).upper() in ("EVEN", "EV"):
            return 100.0 if str(v).upper() in ("EVEN", "EV") else None
        return float(str(v).replace("+", ""))
    except (TypeError, ValueError):
        return None


def _abbr(a: str) -> str:
    a = (a or "").upper()
    return ESPN_MAP.get(a, a)


def _pick_odds(odds_list):
    """Choose the best-ranked odds block that actually carries a moneyline."""
    if not odds_list:
        return None
    def score(o):
        name = ((o.get("provider") or {}).get("name") or "")
        try:
            r = BOOK_RANK.index(name)
        except ValueError:
            r = len(BOOK_RANK)
        has_ml = _ml_from(o)[0] is not None
        return (0 if has_ml else 1, r)
    return sorted(odds_list, key=score)[0]


def _ml_from(o):
    """(away_ml, home_ml) out of whichever shape ESPN used."""
    a = _num((o.get("awayTeamOdds") or {}).get("moneyLine"))
    h = _num((o.get("homeTeamOdds") or {}).get("moneyLine"))
    if a is None or h is None:
        cur = o.get("current") or {}
        a = a if a is not None else _num(((cur.get("away") or {}).get("moneyLine") or {}).get("american")
                                         if isinstance(cur.get("away"), dict) else None)
        h = h if h is not None else _num(((cur.get("home") or {}).get("moneyLine") or {}).get("american")
                                         if isinstance(cur.get("home"), dict) else None)
    if a is None or h is None:
        for side, key in (("awayTeamOdds", "a"), ("homeTeamOdds", "h")):
            blk = o.get(side) or {}
            v = _num((blk.get("moneyLine") or {}).get("american") if isinstance(blk.get("moneyLine"), dict) else None)
            if key == "a" and a is None:
                a = v
            if key == "h" and h is None:
                h = v
    return a, h


def _runline_from(o, home_abbr, away_abbr):
    """
    (run_line_as_it_applies_to_HOME, home_price, away_price).

    ESPN publishes the spread from the favourite's point of view and names the
    favourite in `details` ("MIL -1.5"). Taking the raw number as the home line
    silently inverts the run line on every game the road team is favoured in,
    which is roughly half the slate, so the favourite is resolved explicitly.
    """
    det = (o.get("details") or "").strip()
    raw = _num(o.get("spread"))
    fav = None
    parts = det.split()
    if len(parts) >= 2:
        fav = _abbr(parts[0])
        if raw is None:
            raw = _num(parts[-1])
    if raw is None:
        line = -1.5
    else:
        mag = abs(raw)
        if fav == away_abbr and fav != home_abbr:
            line = +mag                     # home is the underdog: +1.5
        elif fav == home_abbr:
            line = -mag
        else:                               # no favourite named; trust the sign
            line = raw
    hp = _num((o.get("homeTeamOdds") or {}).get("spreadOdds"))
    ap = _num((o.get("awayTeamOdds") or {}).get("spreadOdds"))
    cur = o.get("current") or {}
    if hp is None and isinstance(cur.get("home"), dict):
        hp = _num(((cur["home"].get("close") or cur["home"]).get("odds")))
    if ap is None and isinstance(cur.get("away"), dict):
        ap = _num(((cur["away"].get("close") or cur["away"]).get("odds")))
    return line, hp, ap


def _total_from(o):
    tot = _num(o.get("overUnder"))
    ov = _num((o.get("overOdds")))
    un = _num((o.get("underOdds")))
    cur = o.get("current") or {}
    if tot is None and isinstance(cur.get("total"), dict):
        tot = _num((cur["total"].get("alternateDisplayValue") or cur["total"].get("value")))
    if ov is None and isinstance(cur.get("over"), dict):
        ov = _num(cur["over"].get("american") or (cur["over"].get("close") or {}).get("odds"))
    if un is None and isinstance(cur.get("under"), dict):
        un = _num(cur["under"].get("american") or (cur["under"].get("close") or {}).get("odds"))
    return tot, ov, un


def _books_from(comp, home_abbr, away_abbr) -> list[dict]:
    """Every provider ESPN returned for this game, normalised."""
    books = []
    for o in (comp.get("odds") or []):
        ml_a, ml_h = _ml_from(o)
        rl, rl_h, rl_a = _runline_from(o, home_abbr, away_abbr)
        tot, ov, un = _total_from(o)
        name = ((o.get("provider") or {}).get("name") or "book")
        if ml_a is None and tot is None:
            continue
        books.append({"book": name, "ml_away": ml_a, "ml_home": ml_h,
                      "rl_line": rl, "rl_home": rl_h, "rl_away": rl_a,
                      "total": tot, "over": ov, "under": un})
    return books


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _best_price(prices):
    """The most favourable American price on offer for one selection."""
    prices = [p for p in prices if p is not None]
    if not prices:
        return None, None
    return max(prices, key=lambda p: american_decimal(p)), None


def american_decimal(a: float) -> float:
    a = float(a)
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / abs(a))


def _devig_pair(pa, pb):
    """Power-method de-vig, kept local so this module has no model imports."""
    if pa is None or pb is None:
        return None, None
    ia = 100.0 / (pa + 100.0) if pa > 0 else abs(pa) / (abs(pa) + 100.0)
    ib = 100.0 / (pb + 100.0) if pb > 0 else abs(pb) / (abs(pb) + 100.0)
    if ia <= 0 or ib <= 0:
        s = ia + ib
        return (ia / s, ib / s) if s > 0 else (0.5, 0.5)
    lo, hi = 0.5, 2.0
    for _ in range(60):
        k = (lo + hi) / 2
        if ia ** k + ib ** k > 1:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    a, b = ia ** k, ib ** k
    s = a + b
    return a / s, b / s


def _consensus(books: list[dict], home_abbr, away_abbr) -> dict:
    """
    Collapse several books into one market view.

    Two different numbers come out of this and they do different jobs. The
    CONSENSUS - the median no-vig probability across books - is the market's
    real opinion, and that is what the edge is measured against. The BEST PRICE
    is the number you would actually bet, which is usually not from the same
    book. Measuring edge against the best price instead would manufacture an
    edge on every game simply by shopping.
    """
    if not books:
        return {}
    out = {"books": [b["book"] for b in books], "n_books": len(books)}

    # ---- moneyline
    pa = [_devig_pair(b["ml_away"], b["ml_home"])[0] for b in books]
    ph = [_devig_pair(b["ml_away"], b["ml_home"])[1] for b in books]
    out["cons_away"], out["cons_home"] = _median(pa), _median(ph)
    ml_a, _ = _best_price([b["ml_away"] for b in books])
    ml_h, _ = _best_price([b["ml_home"] for b in books])
    out["ml_away"], out["ml_home"] = ml_a, ml_h
    out["ml_away_book"] = next((b["book"] for b in books if b["ml_away"] == ml_a), None)
    out["ml_home_book"] = next((b["book"] for b in books if b["ml_home"] == ml_h), None)

    # ---- run line: use the modal line, price only books posting it
    lines = [b["rl_line"] for b in books if b["rl_line"] is not None]
    rl = max(set(lines), key=lines.count) if lines else -1.5
    at = [b for b in books if b["rl_line"] == rl] or books
    out["rl_line"] = rl
    out["rl_home"], _ = _best_price([b["rl_home"] for b in at])
    out["rl_away"], _ = _best_price([b["rl_away"] for b in at])
    ch = [_devig_pair(b["rl_home"], b["rl_away"])[0] for b in at]
    ca = [_devig_pair(b["rl_home"], b["rl_away"])[1] for b in at]
    out["cons_rl_home"], out["cons_rl_away"] = _median(ch), _median(ca)

    # ---- total: same treatment, modal line
    tots = [b["total"] for b in books if b["total"] is not None]
    if tots:
        t = max(set(tots), key=tots.count)
        at = [b for b in books if b["total"] == t]
        out["total"] = t
        out["over"], _ = _best_price([b["over"] for b in at])
        out["under"], _ = _best_price([b["under"] for b in at])
        co = [_devig_pair(b["over"], b["under"])[0] for b in at]
        cu = [_devig_pair(b["over"], b["under"])[1] for b in at]
        out["cons_over"], out["cons_under"] = _median(co), _median(cu)
        out["total_book"] = next((b["book"] for b in at if b["over"] == out["over"]), None)
        # how much books disagree - a wide spread of totals means a soft market
        out["total_spread"] = round(max(tots) - min(tots), 1)
    return out


def odds_for_date(date_str: str) -> dict:
    """'YYYY-MM-DD' -> {(AWAY,HOME): odds record}."""
    d = date_str.replace("-", "")
    js = get_json(SB.format(d=d))
    out = {}
    if not js:
        return out
    fetched = datetime.now(timezone.utc).isoformat()
    for ev in js.get("events", []):
        for comp in ev.get("competitions", []):
            teams = {}
            for c in comp.get("competitors", []):
                teams[c.get("homeAway")] = _abbr((c.get("team") or {}).get("abbreviation"))
            if "home" not in teams or "away" not in teams:
                continue
            books = _books_from(comp, teams["home"], teams["away"])
            if not books:
                continue
            rec = _consensus(books, teams["home"], teams["away"])
            if rec.get("ml_away") is None and rec.get("total") is None:
                continue
            rec.setdefault("rl_line", -1.5)
            rec["rl_home"] = rec.get("rl_home") if rec.get("rl_home") is not None else -110.0
            rec["rl_away"] = rec.get("rl_away") if rec.get("rl_away") is not None else -110.0
            rec["over"] = rec.get("over") if rec.get("over") is not None else -110.0
            rec["under"] = rec.get("under") if rec.get("under") is not None else -110.0
            rec["book"] = (f"{rec['n_books']} books"
                           if rec["n_books"] > 1 else rec["books"][0])
            rec["fetched_at"] = fetched
            rec["espn_id"] = ev.get("id")
            out[(teams["away"], teams["home"])] = rec
    return out


def scores_for_date(date_str: str) -> dict:
    """Backup final scores if the MLB API is having a day."""
    d = date_str.replace("-", "")
    js = get_json(SB.format(d=d))
    out = {}
    if not js:
        return out
    for ev in js.get("events", []):
        for comp in ev.get("competitions", []):
            st = ((comp.get("status") or {}).get("type") or {})
            if not st.get("completed"):
                continue
            rec = {}
            for c in comp.get("competitors", []):
                rec[c.get("homeAway")] = {"abbr": _abbr((c.get("team") or {}).get("abbreviation")),
                                          "score": _num(c.get("score"))}
            if "home" in rec and "away" in rec:
                out[(rec["away"]["abbr"], rec["home"]["abbr"])] = {
                    "away": rec["away"]["score"], "home": rec["home"]["score"]}
    return out
