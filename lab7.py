#!/usr/bin/env python3
"""Size the position so the $50 loss budget survives a gapped stop.

The stop is 1.0 x ATR below the entry. When price jumps through it, the fill
is worse than the stop and the loss exceeds the budget. GAP_ATR in lab.py
adds slack to the size calculation: shares = $50 / (stop distance + GAP_ATR
of ATR). This measures how much slack the budget actually needs, and what it
costs in profit.

Usage:
  python3 lab7.py
"""

import datetime
import sys

import lab
from lab2 import build, build_other, split, row, section

TIME_11 = datetime.time(11, 0)


def set_stop(mult, floor_pct=0.001):
    def stop(ctx, entry):
        atr = ctx["atr"] or entry * 0.004
        return min(entry - mult * atr, entry * (1 - floor_pct))
    return stop


def run(recs, slip, day_set):
    return lab.run_days(recs, "cur", "atr_trail", TIME_11, True, slip, day_set)


def budget(name, trades):
    if not trades:
        print("  %-34s no trades" % name)
        return
    pnls = sorted(t["pnl"] for t in trades)
    over50 = sum(1 for t in trades if t["pnl"] < -50)
    over75 = sum(1 for t in trades if t["pnl"] < -75)
    print("  %-34s $=%-8.0f worst=%-6.0f over$50=%-3d over$75=%-3d n=%d"
          % (name, sum(pnls), pnls[0], over50, over75, len(trades)))


def main():
    recs = build()
    train, _, test, alldays = split(recs)
    other = build_other()
    ot, _, ote, _ = split(other)
    print("%d sessions, train %s..%s, test %s..%s\n"
          % (len(recs), alldays[0], sorted(train)[-1], sorted(test)[0], alldays[-1]))

    section("GAP SLACK x SLIPPAGE, stop = 1.0 x ATR (rule A)")
    lab.EXITS["atr_trail"]["stop"] = set_stop(1.0)
    for gap in (0.0, 0.5, 1.0, 1.5):
        lab.GAP_ATR = gap
        for slip in (0.01, 0.03):
            budget("gap %.1fATR slip %.2f train" % (gap, slip), run(recs, slip, train))
            budget("gap %.1fATR slip %.2f test " % (gap, slip), run(recs, slip, test))
        print()

    section("SAME, on the 20 symbols never used to pick the rule")
    for gap in (0.0, 0.5, 1.0):
        lab.GAP_ATR = gap
        budget("gap %.1fATR slip .01 train unseen" % gap, run(other, 0.01, ot))
        budget("gap %.1fATR slip .01 test  unseen" % gap, run(other, 0.01, ote))
        budget("gap %.1fATR slip .03 test  unseen" % gap, run(other, 0.03, ote))
    print()

    section("DOES A WIDER STOP NEED LESS SLACK? (gap 1.0 ATR, slip 0.01)")
    lab.GAP_ATR = 1.0
    for mult in (1.0, 1.25, 1.5):
        lab.EXITS["atr_trail"]["stop"] = set_stop(mult)
        budget("stop %.2fxATR train" % mult, run(recs, 0.01, train))
        budget("stop %.2fxATR test " % mult, run(recs, 0.01, test))
    lab.EXITS["atr_trail"]["stop"] = set_stop(1.0)
    lab.GAP_ATR = 0.0
    return 0


if __name__ == "__main__":
    sys.exit(main())
