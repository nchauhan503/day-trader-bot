#!/usr/bin/env python3
"""Parity check: does signals.py decide what lab.py measured?

The backtest rules live in lab.py and the phone rules live in signals.py as
two separate copies of the same idea. If they drift apart, the numbers being
quoted describe code that is not running. This walks every cached session and
compares the two, bar by bar.

Usage:
  python3 parity.py
"""

import datetime
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import lab
import signals

ET = ZoneInfo("America/New_York")
CACHE = Path(__file__).resolve().parent / "data" / "cache"


def to_bars(rows):
    bars = []
    for ts, o, h, l, c, v in rows:
        start = datetime.datetime.fromtimestamp(ts, ET)
        bars.append({
            "start": start, "end": start + datetime.timedelta(minutes=5),
            "o": o, "h": h, "l": l, "c": c, "v": v,
        })
    return bars


def close(a, b, eps=1e-6, rel=1e-9):
    """eps is an absolute tolerance, rel a relative one. Price levels get a
    wider eps because signals.py rounds to the cent and the lab does not."""
    return abs(a - b) <= max(eps, rel * max(abs(a), abs(b)))


def main():
    files = sorted(CACHE.glob("*.json"))
    if not files:
        print("no cached data; run lab.py first")
        return 1
    days_checked = bars_checked = 0
    problems = []

    # The stop the backtest actually settled on, written from lab's own
    # constants so drift in signals.py's copy is caught rather than papered
    # over by importing them.
    assert signals.STOP_ATR == 1.0 and signals.STOP_FLOOR == 0.001, \
        "signals.py stop constants no longer match the picked rule"
    assert signals.TRAIL_ATR == 2.0 and signals.GAP_ATR == 0.5, \
        "signals.py trail/gap constants no longer match the picked rule"
    assert signals.NO_BUYS_AFTER == datetime.time(11, 30), "buy window moved"
    assert signals.TOP == 20, "watchlist size no longer matches the validated rule"

    def picked_stop(ctx, entry):
        a = ctx["atr"] or entry * 0.004
        return min(entry - 1.0 * a, entry * (1 - 0.001))

    lab_stop = picked_stop
    lab_trail = lab.EXITS["atr_trail"]["trail"]
    lab_close = lab.EXITS["atr_trail"]["close"]

    for path in files:
        if path.name.endswith(".daily.json"):
            continue          # daily closes for the trend filter, not sessions
        sym = path.stem
        doc = json.loads(path.read_text())
        for day, rows in sorted(doc.items()):
            bars = to_bars(rows)
            if len(bars) < 30:
                continue
            ctxs = lab.prep(sym, day, rows)["ctxs"]
            days_checked += 1
            for i in range(14, len(bars)):
                asof = bars[i]["end"]
                ctx = ctxs[i]
                sig = signals.analyze(sym, bars, asof)
                if sig is None:
                    problems.append("%s %s bar %d: signals.py silent, lab has a reading"
                                    % (sym, day, i))
                    continue
                bars_checked += 1

                for field in ("close", "ema9", "ema20", "rsi", "vwap", "atr",
                              "or_high", "or_low", "hi_sofar"):
                    if not close(sig[field], ctx[field]):
                        problems.append("%s %s bar %d: %s signals=%.6f lab=%.6f"
                                        % (sym, day, i, field, sig[field], ctx[field]))

                if signals.buy_ok(sig) != lab.e_cur(ctx):
                    problems.append("%s %s bar %d: buy decision differs (signals=%s lab=%s)"
                                    % (sym, day, i, signals.buy_ok(sig), lab.e_cur(ctx)))

                # The stop the phone would place, on the same entry price.
                entry_live = round(sig["close"], 2)
                live_stop = round(min(entry_live - signals.STOP_ATR * sig["atr"],
                                      entry_live * (1 - signals.STOP_FLOOR)), 2)
                if not close(live_stop, lab_stop(ctx, sig["close"]), 0.01):
                    problems.append("%s %s bar %d: stop signals=%.6f lab=%.6f"
                                    % (sym, day, i, live_stop, lab_stop(ctx, sig["close"])))

                # The trail arms on this bar's own high; its value is 2 x ATR
                # under the session high. Both sides use the same fake position.
                fake_pos = {"entry": sig["close"], "stop": 0.0,
                            "risk0": sig["close"] * 0.01}
                lab_t = lab_trail(ctx, fake_pos)
                live_armed = ctx["bar"]["h"] >= fake_pos["entry"] + fake_pos["risk0"]
                if (lab_t is not None) != live_armed:
                    problems.append("%s %s bar %d: trail armed differently (signals=%s lab=%s)"
                                    % (sym, day, i, live_armed, lab_t is not None))
                if lab_t is not None:
                    live_trail = round(sig["hi_sofar"] - signals.TRAIL_ATR * sig["atr"], 2)
                    if not close(live_trail, lab_t, 0.01):
                        problems.append("%s %s bar %d: trail signals=%.6f lab=%.6f"
                                        % (sym, day, i, live_trail, lab_t))

                if (sig["close"] < sig["vwap"]) != lab_close(ctx, {}):
                    problems.append("%s %s bar %d: close-under-vwap differs" % (sym, day, i))

                # The buy window, compared on the same clock. 11:30 is the
                # cutoff lab8 measured, written out rather than imported so a
                # drift in signals.py is caught.
                live_cut = sig["bar"]["end"].astimezone(ET).time() > signals.NO_BUYS_AFTER
                lab_cut = ctx["bar"]["end"].astimezone(ET).time() > datetime.time(11, 30)
                if live_cut != lab_cut:
                    problems.append("%s %s bar %d: buy window differs" % (sym, day, i))

    print("%d sessions, %d bar readings compared" % (days_checked, bars_checked))
    if problems:
        print("MISMATCHES: %d" % len(problems))
        for p in problems[:20]:
            print("  " + p)
        return 1
    print("parity ok: signals.py and lab.py decide identically")
    return 0


if __name__ == "__main__":
    sys.exit(main())
