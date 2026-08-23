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
CORE = ("https://sports.core.api.espn.com/v2/sports/baseball/leagues/mlb/"
        "events/{event}/competitions/{competition}/odds?lang=en&region=us")

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

    # Current scoreboard shape (August 2026): the useful values moved out of
    # awayTeamOdds/homeTeamOdds and into a top-level moneyline block.
    ml = o.get("moneyline") or {}
    if a is None and isinstance(ml.get("away"), dict):
        blk = ml["away"].get("close") or ml["away"]
        a = _num(blk.get("odds") or blk.get("american"))
    if h is None and isinstance(ml.get("home"), dict):
        blk = ml["home"].get("close") or ml["home"]
        h = _num(blk.get("odds") or blk.get("american"))

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

    # Core odds endpoint: direct moneyLine is normally populated, but some
    # payloads carry only current.moneyLine.
    for side, key in (("awayTeamOdds", "a"), ("homeTeamOdds", "h")):
        blk = o.get(side) or {}
        cur = blk.get("current") or {}
        ml_cur = cur.get("moneyLine") or {}
        v = _num(ml_cur.get("american") or ml_cur.get("alternateDisplayValue")
                 if isinstance(ml_cur, dict) else None)
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
    # Current scoreboard shape. Use the explicit side-specific line rather
    # than inferring its sign from `details`: details now contains a moneyline
    # such as "HOU -174", not a spread such as "HOU -1.5".
    ps = o.get("pointSpread") or {}
    ps_h = ps.get("home") or {}
    ps_a = ps.get("away") or {}
    ps_hc = (ps_h.get("close") or ps_h) if isinstance(ps_h, dict) else {}
    ps_ac = (ps_a.get("close") or ps_a) if isinstance(ps_a, dict) else {}
    explicit_h = _num(ps_hc.get("line"))
    explicit_a = _num(ps_ac.get("line"))
    hp = _num(ps_hc.get("odds") or ps_hc.get("american"))
    ap = _num(ps_ac.get("odds") or ps_ac.get("american"))
    if explicit_h is not None or explicit_a is not None:
        line = explicit_h if explicit_h is not None else -explicit_a
        return line, hp, ap

    # Core endpoint shape: pointSpread is the line, spread is its price.
    hs = o.get("homeTeamOdds") or {}
    aws = o.get("awayTeamOdds") or {}
    hc = hs.get("current") or {}
    ac = aws.get("current") or {}
    hline = hc.get("pointSpread") or {}
    aline = ac.get("pointSpread") or {}
    explicit_h = _num(hline.get("american") or hline.get("alternateDisplayValue")
                      if isinstance(hline, dict) else None)
    explicit_a = _num(aline.get("american") or aline.get("alternateDisplayValue")
                      if isinstance(aline, dict) else None)
    hprice = hc.get("spread") or {}
    aprice = ac.get("spread") or {}
    hp = _num(hprice.get("american") or hprice.get("alternateDisplayValue")
              if isinstance(hprice, dict) else None)
    ap = _num(aprice.get("american") or aprice.get("alternateDisplayValue")
              if isinstance(aprice, dict) else None)
    if explicit_h is not None or explicit_a is not None:
        line = explicit_h if explicit_h is not None else -explicit_a
        return line, hp, ap

    # Legacy scoreboard shapes.
    det = (o.get("details") or "").strip()
    raw = _num(o.get("spread"))
    fav = None
    parts = det.split()
    if len(parts) >= 2:
        fav = _abbr(parts[0])
        if raw is None:
            candidate = _num(parts[-1])
            # A baseball run line is normally 1.5. Do not mistake the current
            # `details` moneyline (for example -174) for a spread.
            raw = candidate if candidate is not None and abs(candidate) <= 10 else None
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

    # Current scoreboard shape.
    total = o.get("total") or {}
    over = total.get("over") or {}
    under = total.get("under") or {}
    over_c = (over.get("close") or over) if isinstance(over, dict) else {}
    under_c = (under.get("close") or under) if isinstance(under, dict) else {}
    if tot is None:
        # Lines are strings such as "o9" and "u8.5" in this shape.
        line = str(over_c.get("line") or under_c.get("line") or "").lower()
        tot = _num(line.lstrip("ou"))
    if ov is None:
        ov = _num(over_c.get("odds") or over_c.get("american"))
    if un is None:
        un = _num(under_c.get("odds") or under_c.get("american"))

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
        name = ((o.get("provider") or {}).get("name") or "book")
        # The Core endpoint keeps a separate in-play item after first pitch.
        # This is a pregame model, so never mix live prices into its market.
        if "live odds" in name.lower():
            continue
        ml_a, ml_h = _ml_from(o)
        rl, rl_h, rl_a = _runline_from(o, home_abbr, away_abbr)
        tot, ov, un = _total_from(o)
        # A line by itself is not a price. Keep a provider only when at least
        # one complete two-sided market can be de-vigged.
        if not ((ml_a is not None and ml_h is not None)
                or (rl_h is not None and rl_a is not None)
                or (tot is not None and ov is not None and un is not None)):
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
    out = {"books": list(dict.fromkeys(b["book"] for b in books)),
           "n_books": len(set(b["book"] for b in books))}

    # ---- moneyline
    ml_books = [b for b in books if b["ml_away"] is not None and b["ml_home"] is not None]
    if ml_books:
        pa = [_devig_pair(b["ml_away"], b["ml_home"])[0] for b in ml_books]
        ph = [_devig_pair(b["ml_away"], b["ml_home"])[1] for b in ml_books]
        out["cons_away"], out["cons_home"] = _median(pa), _median(ph)
        ml_a, _ = _best_price([b["ml_away"] for b in ml_books])
        ml_h, _ = _best_price([b["ml_home"] for b in ml_books])
        out["ml_away"], out["ml_home"] = ml_a, ml_h
        out["ml_away_book"] = next((b["book"] for b in ml_books if b["ml_away"] == ml_a), None)
        out["ml_home_book"] = next((b["book"] for b in ml_books if b["ml_home"] == ml_h), None)

    # ---- run line: use the modal line, price only books posting it
    rl_books = [b for b in books if b["rl_line"] is not None
                and b["rl_home"] is not None and b["rl_away"] is not None]
    lines = [b["rl_line"] for b in rl_books]
    if lines:
        rl = max(set(lines), key=lines.count)
        at = [b for b in rl_books if b["rl_line"] == rl]
        out["rl_line"] = rl
        out["rl_home"], _ = _best_price([b["rl_home"] for b in at])
        out["rl_away"], _ = _best_price([b["rl_away"] for b in at])
        out["rl_home_book"] = next((b["book"] for b in at
                                    if b["rl_home"] == out["rl_home"]), None)
        out["rl_away_book"] = next((b["book"] for b in at
                                    if b["rl_away"] == out["rl_away"]), None)
        ch = [_devig_pair(b["rl_home"], b["rl_away"])[0] for b in at]
        ca = [_devig_pair(b["rl_home"], b["rl_away"])[1] for b in at]
        out["cons_rl_home"], out["cons_rl_away"] = _median(ch), _median(ca)

    # ---- total: same treatment, modal line
    total_books = [b for b in books if b["total"] is not None
                   and b["over"] is not None and b["under"] is not None]
    tots = [b["total"] for b in total_books]
    if tots:
        t = max(set(tots), key=tots.count)
        at = [b for b in total_books if b["total"] == t]
        out["total"] = t
        out["over"], _ = _best_price([b["over"] for b in at])
        out["under"], _ = _best_price([b["under"] for b in at])
        co = [_devig_pair(b["over"], b["under"])[0] for b in at]
        cu = [_devig_pair(b["over"], b["under"])[1] for b in at]
        out["cons_over"], out["cons_under"] = _median(co), _median(cu)
        out["over_book"] = next((b["book"] for b in at if b["over"] == out["over"]), None)
        out["under_book"] = next((b["book"] for b in at if b["under"] == out["under"]), None)
        out["total_book"] = out["over_book"]
        # how much books disagree - a wide spread of totals means a soft market
        out["total_spread"] = round(max(tots) - min(tots), 1)
    return out


def has_priced_market(rec: dict | None) -> bool:
    """True only when at least one complete, de-viggable market is present."""
    rec = rec or {}
    return bool((rec.get("ml_away") is not None and rec.get("ml_home") is not None)
                or (rec.get("rl_away") is not None and rec.get("rl_home") is not None
                    and rec.get("rl_line") is not None)
                or (rec.get("total") is not None and rec.get("over") is not None
                    and rec.get("under") is not None))


def suspicious_record(rec: dict | None) -> bool:
    """Reject the exact failure mode that once fabricated an entire -110 slate."""
    rec = rec or {}
    prices = [rec.get(k) for k in ("ml_away", "ml_home", "rl_away", "rl_home",
                                    "over", "under") if rec.get(k) is not None]
    return (len(prices) >= 4 and rec.get("ml_away") is None
            and len(set(float(p) for p in prices)) == 1
            and float(prices[0]) == -110.0)


def feed_health(records: dict, expected_games: int | None = None) -> dict:
    """Small published contract so the dashboard can warn instead of guessing."""
    vals = list(records.values())
    ml = sum(r.get("ml_away") is not None and r.get("ml_home") is not None for r in vals)
    rl = sum(r.get("rl_away") is not None and r.get("rl_home") is not None
             and r.get("rl_line") is not None for r in vals)
    totals = sum(r.get("total") is not None and r.get("over") is not None
                 and r.get("under") is not None for r in vals)
    priced = sum(has_priced_market(r) for r in vals)
    expected = expected_games if expected_games is not None else len(vals)
    if priced == 0:
        status = "unavailable"
    elif expected and priced < expected:
        status = "partial"
    else:
        status = "ok"
    return {"status": status, "expected_games": expected, "priced_games": priced,
            "moneyline_games": ml, "runline_games": rl, "total_games": totals,
            "keyless": True, "region": "US", "provider": "ESPN"}


def _core_books(event_id, competition_id, home_abbr, away_abbr) -> list[dict]:
    """Recover pregame prices after the scoreboard drops them at first pitch."""
    if not event_id or not competition_id:
        return []
    js = get_json(CORE.format(event=event_id, competition=competition_id), quiet=True)
    if not js:
        return []
    return _books_from({"odds": js.get("items") or []}, home_abbr, away_abbr)


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
            source = "ESPN scoreboard"
            if not books:
                books = _core_books(ev.get("id"), comp.get("id") or ev.get("id"),
                                    teams["home"], teams["away"])
                source = "ESPN core"
            if not books:
                continue
            rec = _consensus(books, teams["home"], teams["away"])
            if not has_priced_market(rec) or suspicious_record(rec):
                continue
            rec["book"] = (f"{rec['n_books']} books"
                           if rec["n_books"] > 1 else rec["books"][0])
            rec["fetched_at"] = fetched
            rec["espn_id"] = ev.get("id")
            rec["source"] = source
            out[(teams["away"], teams["home"])] = rec

    # A source/schema regression must never masquerade as a slate of real -110
    # markets again. This guard is intentionally slate-wide to avoid rejecting
    # an individual game whose two prices genuinely happen to be -110.
    all_prices = [r.get(k) for r in out.values()
                  for k in ("ml_away", "ml_home", "rl_away", "rl_home", "over", "under")
                  if r.get(k) is not None]
    if len(all_prices) >= 12 and len(set(float(p) for p in all_prices)) == 1:
        print("  ! odds feed rejected: every parsed price is identical")
        return {}
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
