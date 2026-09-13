/*
 * sync-adapter.js — MLB Edge's half of the shared ledger.
 *
 * Translates between this board's own ledger entries and the flat row shape the
 * sheet stores, and puts the shared bankroll into the staking controls. The
 * board's entry travels along untouched inside `native`, so a bet that goes out
 * from here and comes back on another device is the same object it started as.
 */
(function () {
  "use strict";
  if (!window.BetSync) return;
  var BS = window.BetSync;
  var APP = "mlb-edge", SPORT = "MLB";
  var L = window.MLBLedger;
  var STK = window.MLBStaking;
  /* app.js runs inside its own closure, so it hands out this small door rather
   * than leaving its state on the window for anything to reach. */
  var APPV = window.MLBEdgeApp || null;
  if (!L) return;

  var TO_STATUS = { win: "Win", loss: "Loss", push: "Push", void: "Void" };
  var TO_RESULT = { Win: "win", Loss: "loss", Push: "push", Void: "void" };

  function toCanonical(e) {
    return {
      id: APP + ":" + e.gamePk + "|" + e.market + "|" + e.selection,
      app: APP, sport: SPORT,
      placed_at: e.added_at,
      event_date: e.date,
      event: (e.away || "") + " @ " + (e.home || ""),
      market: e.market,
      selection: e.label || e.selection,
      side: e.selection,
      line: e.line, price: e.price, book: e.book, stake: e.stake,
      tier: e.tier, edge: e.edge, model_prob: e.p_model,
      status: TO_STATUS[e.result] || "Pending",
      pnl: e.pl, closing_price: null,
      score: e.final, notes: "",
      native: e
    };
  }

  function fromCanonical(row) {
    var e = row.native ? JSON.parse(JSON.stringify(row.native)) : {
      gamePk: String(row.id).split(":").pop().split("|")[0],
      date: row.event_date, away: (row.event || "").split(" @ ")[0],
      home: (row.event || "").split(" @ ")[1], market: row.market,
      selection: row.side || row.selection, label: row.selection,
      line: row.line, price: row.price, book: row.book,
      tier: row.tier, edge: row.edge, p_model: row.model_prob,
      added_at: row.placed_at
    };
    if (row.stake != null) e.stake = row.stake;
    e.result = TO_RESULT[row.status] || null;
    e.pl = row.pnl;
    if (row.score) e.final = row.score;
    return e;
  }

  /* What the board would actually notice changing - a bet appearing, a stake
   * edited, a result landing. Anything else is not worth a repaint. */
  function fingerprint(entries) {
    return JSON.stringify((entries || []).map(function (e) {
      return [L.keyOf(e), e.stake, e.result, e.pl];
    }).sort());
  }

  function rerender() {
    if (!APPV) return;
    try {
      APPV.setLedger(L.load());
      APPV.redraw();
    } catch (_) {}
  }

  BS.register({
    app: APP,
    sport: SPORT,
    storageKey: L.STORAGE_KEY,

    readLocal: function () {
      return (L.load() || []).map(toCanonical);
    },

    writeLocal: function (rows) {
      var next = rows.map(fromCanonical);
      var before = fingerprint(L.load());
      L.save(next);
      if (before !== fingerprint(next)) rerender();
    },

    /* One bankroll across all five boards: this board's Kelly sizing reads the
     * number the sheet holds, not a figure typed into this browser alone. */
    applyBankroll: function (bank) {
      if (!STK || !bank || bank.current == null) return;
      try {
        var index = (APPV && APPV.index && APPV.index()) || {};
        var settings = STK.load(index);
        if (Math.abs(Number(settings.bankroll) - bank.current) < 0.005) return;
        settings.bankroll = bank.current;
        STK.save(settings);
        if (APPV) APPV.setStaking(settings);
        rerender();
      } catch (_) {}
    }
  });

  BS.start();
})();
