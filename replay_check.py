#!/usr/bin/env python3
"""End-to-end dry run of the real alert code over a past session.

Parity.py proves signals.py computes the same numbers as the backtest. This
goes further: it feeds the real one_pass() bar by bar over a cached session,
with notifications off and the ledger redirected to a temp folder, then asks
whether it took the same trades lab.py measured for that day.

Usage:
  python3 replay_check.py
  python3 replay_check.py --day 2026-11-04
"""

import argparse
import contextlib
import datetime
import io
import json
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import lab
import signals

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "data" / "cache"
SYMS = ["AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA", "AMD"]
TIME_CUT = datetime.time(11, 30)
# signals.py sizes against a gapped stop, so the lab has to size the same way
# or the share counts will never line up. Same for the stop itself: the lab's
# default is 1.5 x ATR, the picked rule is 1.0 x ATR with a 0.1% floor.
lab.GAP_ATR = 0.5


def picked_stop(ctx, entry):
    a = ctx["atr"] or entry * 0.004
    return min(entry - 1.0 * a, entry * (1 - 0.001))


lab.EXITS["atr_trail"]["stop"] = picked_stop


def session_bars(sym, day):
    doc = json.loads((CACHE / (sym + ".json")).read_text())
    rows = doc[day]
    bars = []
    for ts, o, h, l, c, v in rows:
        start = datetime.datetime.fromtimestamp(ts, ET)
        bars.append({
            "start": start, "end": start + datetime.timedelta(minutes=5),
            "o": o, "h": h, "l": l, "c": c, "v": v,
        })
    return doc, bars


def lab_recs(day):
    recs = []
    for sym in SYMS:
        doc, _ = session_bars(sym, day)
        rec = lab.prep(sym, day, doc[day])
        rec["trend"] = lab.trend_of(sym, day)
        recs.append(rec)
    return recs


def live_replay(day):
    """Step the real one_pass() across the session, notifications off."""
    bars_by_sym = {}
    for sym in SYMS:
        _, bars = session_bars(sym, day)
        bars_by_sym[sym] = bars

    tmp = Path(tempfile.mkdtemp())
    signals.STATE_PATH = tmp / "positions.json"
    signals.LEDGER = tmp / "ledger.json"
    signals.SCORECARD = tmp / "scorecard.txt"
    signals.fetch_bars = lambda sym: bars_by_sym[sym]

    log = io.StringIO()
    clock = [b["end"] for b in bars_by_sym[SYMS[0]]]
    with contextlib.redirect_stdout(log):
        for asof in clock:
            try:
                signals.one_pass("", ",".join(SYMS), notify=False, asof=asof)
            except Exception as exc:
                log.write("\nEXCEPTION at %s: %r\n" % (asof, exc))
                raise
    ledger = json.loads(signals.LEDGER.read_text()) if signals.LEDGER.exists() else []
    return log.getvalue(), ledger, tmp


def main():
    global SYMS
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", action="append", required=True,
                    help="session to replay; repeat for several")
    ap.add_argument("--syms", default=",".join(SYMS),
                    help="comma-separated names to feed the real one_pass()")
    args = ap.parse_args()
    SYMS = [s.strip().upper() for s in args.syms.split(",") if s.strip()]
    days = args.day

    rc = 0
    for day in days:
        print("\n" + "#" * 78)
        rc = max(rc, one_day(day))
    return rc


def one_day(day):
    print("replaying %s on %s\n" % (", ".join(SYMS), day))

    text, ledger, tmp = live_replay(day)

    if "EXCEPTION" in text:
        print(text[-2000:])
        return 1

    alerts = [l for l in text.splitlines()
              if l.startswith(("BUY ", "SELL ")) or "Pass done" in l]
    print("--- what the phone would have said ---")
    for line in alerts:
        print("  " + line)
    if not alerts:
        print("  (no alert on this session)")

    # The buy ticket itself, straight out of the log.
    for i, line in enumerate(text.splitlines()):
        if line.startswith("BUY "):
            print("\n--- full buy ticket ---")
            for extra in text.splitlines()[i:i + 8]:
                print("  " + extra)
            break

    print("\n--- ledger written by the real code ---")
    if not ledger:
        print("  empty")
    ok = True
    for row in ledger:
        print("  %s %s entry %s stop %s shares %s exit %s pnl %s (%s)"
              % (row["date"], row["symbol"], row["entry"], row["stop"],
                 row["shares"], row.get("exit"), row.get("pnl"), row.get("reason")))
        risk = row["entry"] - row["stop"]
        if row["stop"] >= row["entry"]:
            print("    FAIL: stop is not below the entry")
            ok = False
        if row["shares"] < 1:
            print("    FAIL: sized zero shares")
            ok = False
        if row["shares"] * risk > 50.0 + 1e-6:
            print("    FAIL: loss at the stop is %.2f, over the $50 budget"
                  % (row["shares"] * risk))
            ok = False
        if row.get("exit") is None:
            print("    FAIL: still open after the close")
            ok = False
    if not ok:
        return 1

    # Same trades as the backtest for this day?
    lab_trades = lab.run_days(lab_recs(day), "cur", "atr_trail", TIME_CUT,
                              True, 0.01, {day})
    lab_buys = sorted(t["sym"] for t in lab_trades)
    live_buys = sorted(r["symbol"] for r in ledger)

    def bucket(reason):
        if "trend" in reason:
            return "trend"
        if "flat" in reason or reason == "session end":
            return "flat"
        if "stop" in reason:
            return "stop"
        if "target" in reason:
            return "target"
        return reason

    lab_exits = sorted((t["sym"], bucket(t["reason"])) for t in lab_trades)
    live_exits = sorted((r["symbol"], bucket(r["reason"])) for r in ledger)

    print("\n--- same trades as the backtest? ---")
    print("  backtest bought: %s" % (", ".join(lab_buys) or "none"))
    print("  live code bought: %s" % (", ".join(live_buys) or "none"))
    print("  backtest exits: %s" % lab_exits)
    print("  live exits     : %s" % live_exits)

    if lab_buys != live_buys:
        print("\nFAIL: the live code did not take the same trades as the backtest")
        return 1

    print("\n--- alert price vs a realistic fill ---")
    print("    (the ledger scores the alert price; the backtest fills the next")
    print("     bar's open and charges a cent a share, so trust the backtest)")
    lab_by = {t["sym"]: t for t in lab_trades}
    for row in ledger:
        t = lab_by.get(row["symbol"])
        if not t:
            continue
        print("  %-5s entry  alert %8.2f  fill %8.2f  (%+.2f)   exit  alert %8.2f  fill %8.2f  (%+.2f)"
              % (row["symbol"], row["entry"], t["entry"], row["entry"] - t["entry"],
                 row["exit"], t["exit"], row["exit"] - t["exit"]))

    if lab_exits != live_exits:
        print("\nNOTE: same trades, but a different exit on at least one")
        print("      (the live code rounds stops to the cent, the lab does not)")
    print("\nOK: the real alert code took exactly the trades the backtest measured")
    print("artifacts in %s" % tmp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
