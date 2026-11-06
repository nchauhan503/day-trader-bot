#!/usr/bin/env python3
"""Decide the stop width: 1.0 x ATR tested better than the 1.5 x ATR the
grid picked, but a tight stop is the one most likely to be a mirage once
real fills are worse than the model.

Crosses stop width against slippage, and adds a floor on the stop as a
percentage of price, which is how a real order behaves on a cheap stock.

Usage:
  python3 lab4.py
"""

import datetime
import sys

import lab
from lab2 import build, split, run, row, section

TIME_11 = datetime.time(11, 0)


def set_stop(mult, floor_pct):
    """Stop distance is at least floor_pct of price, and at most mult x ATR."""
    def stop(ctx, entry):
        atr = ctx["atr"] or entry * 0.004
        return min(entry - mult * atr, entry * (1 - floor_pct))
    return stop


def main():
    recs = build()
    train, _, test, alldays = split(recs)
    print("%d sessions, train %s..%s, test %s..%s"
          % (len(recs), alldays[0], sorted(train)[-1], sorted(test)[0], alldays[-1]))

    original = lab.EXITS["atr_trail"]["stop"]

    section("STOP WIDTH x SLIPPAGE (floor 0.10% of price, so a stop can never be paper thin)")
    for mult in (1.0, 1.25, 1.5):
        lab.EXITS["atr_trail"]["stop"] = set_stop(mult, 0.001)
        for slip in (0.01, 0.02, 0.03):
            tr = run(recs, "cur", "atr_trail", TIME_11, True, slip, train)
            te = run(recs, "cur", "atr_trail", TIME_11, True, slip, test)
            print(row("train  stop %.2fxATR slip $%.2f" % (mult, slip), tr)[0])
            print(row("test   stop %.2fxATR slip $%.2f" % (mult, slip), te)[0])
        print()

    section("STOP FLOOR as a percent of price (stop = entry - max(1.5 x ATR, floor x price))")
    for floor in (0.000, 0.001, 0.002, 0.003, 0.005):
        lab.EXITS["atr_trail"]["stop"] = set_stop(1.5, floor)
        tr = run(recs, "cur", "atr_trail", TIME_11, True, 0.01, train)
        te = run(recs, "cur", "atr_trail", TIME_11, True, 0.01, test)
        print(row("train  floor %.2f%%" % (floor * 100), tr)[0])
        print(row("test   floor %.2f%%" % (floor * 100), te)[0])

    section("UNSEEN SYMBOLS at the two candidate stops (never used to pick anything)")
    other = __import__("lab2").build_other()
    ot, _, ote, odays = split(other)
    for mult in (1.0, 1.5):
        lab.EXITS["atr_trail"]["stop"] = set_stop(mult, 0.001)
        tr = run(other, "cur", "atr_trail", TIME_11, True, 0.01, ot)
        te = run(other, "cur", "atr_trail", TIME_11, True, 0.01, ote)
        print(row("train  unseen, stop %.2fxATR" % mult, tr)[0])
        print(row("test   unseen, stop %.2fxATR" % mult, te)[0])

    lab.EXITS["atr_trail"]["stop"] = original
    return 0


if __name__ == "__main__":
    sys.exit(main())
