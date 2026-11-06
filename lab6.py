#!/usr/bin/env python3
"""Final head-to-head on the two rules that survived selection.

A = cur entry, atr_trail exit, buys before 11:00 ET, 5-day uptrend only
B = reclaim entry, trail exit, buys before 11:00 ET, no trend filter

A is the better risk-adjusted rule and won the out-of-sample window harder.
B earned more on the training window alone, which is the metric selection
is supposed to use. Both get the same checks here so the choice is not made
on whichever one happens to look best in one table.

Usage:
  python3 lab6.py
"""

import datetime
import sys
from collections import Counter

import lab
from lab2 import build, build_other, split, row, section

TIME_11 = datetime.time(11, 0)
CANDS = [("A cur/atr_trail/11:00/trend+", "cur", "atr_trail", True),
         ("B reclaim/trail/11:00/notrend", "reclaim", "trail", False)]


def set_stop(mult, floor_pct=0.001):
    def stop(ctx, entry):
        atr = ctx["atr"] or entry * 0.004
        return min(entry - mult * atr, entry * (1 - floor_pct))
    return stop


def run(recs, entry, ex, cutoff, trend, slip, day_set=None):
    return lab.run_days(recs, entry, ex, cutoff, trend, slip, day_set)


def risk_line(name, trades):
    if not trades:
        print("  %s: no trades" % name)
        return
    pnls = sorted(t["pnl"] for t in trades)
    over = sum(1 for t in trades if t["pnl"] < -50)
    per_day = Counter(t["day"] for t in trades)
    print("  %-46s worst %+.0f  over $50: %d/%d  max buys/day %d  exits %s"
          % (name, pnls[0], over, len(trades), max(per_day.values()),
             dict(Counter(t["reason"] for t in trades))))


def main():
    recs = build()
    train, _, test, alldays = split(recs)
    other = build_other()
    ot, _, ote, odays = split(other)
    print("%d sessions  train %s..%s  test %s..%s"
          % (len(recs), alldays[0], sorted(train)[-1], sorted(test)[0], alldays[-1]))
    print("%d unseen sessions  train %s..%s  test %s..%s\n"
          % (len(other), odays[0], sorted(ot)[-1], sorted(ote)[0], odays[-1]))

    section("1. SLIPPAGE")
    for slip in (0.01, 0.02, 0.03):
        for name, en, ex, tf in CANDS:
            print(row("train slip $%.2f  %s" % (slip, name),
                      run(recs, en, ex, TIME_11, tf, slip, train))[0])
            print(row("test  slip $%.2f  %s" % (slip, name),
                      run(recs, en, ex, TIME_11, tf, slip, test))[0])
        print()

    section("2. EACH THIRD OF THE 60 DAYS")
    thirds = [alldays[i * len(alldays) // 3:(i + 1) * len(alldays) // 3]
              for i in range(3)]
    for days in thirds:
        for name, en, ex, tf in CANDS:
            print(row("%s..%s  %s" % (days[0], days[-1], name),
                      run(recs, en, ex, TIME_11, tf, lab.SLIP, set(days)))[0])
        print()

    section("3. SYMBOLS NEVER USED TO PICK EITHER RULE")
    for name, en, ex, tf in CANDS:
        print(row("train unseen  " + name, run(other, en, ex, TIME_11, tf, 0.01, ot))[0])
        print(row("test  unseen  " + name, run(other, en, ex, TIME_11, tf, 0.01, ote))[0])
    print()

    section("4. A's STOP WIDTH, re-checked with the corrected 3-buys-a-day cap")
    original = lab.EXITS["atr_trail"]["stop"]
    for mult in (1.0, 1.25, 1.5, 2.0):
        lab.EXITS["atr_trail"]["stop"] = set_stop(mult)
        print(row("train  stop %.2fxATR" % mult,
                  run(recs, "cur", "atr_trail", TIME_11, True, 0.01, train))[0])
        print(row("test   stop %.2fxATR" % mult,
                  run(recs, "cur", "atr_trail", TIME_11, True, 0.01, test))[0])
    lab.EXITS["atr_trail"]["stop"] = original
    print()

    section("5. RISK: does any trade break the $50 loss budget?")
    for name, en, ex, tf in CANDS:
        risk_line("train  " + name, run(recs, en, ex, TIME_11, tf, 0.01, train))
        risk_line("test   " + name, run(recs, en, ex, TIME_11, tf, 0.01, test))
        risk_line("train  " + name + " @ $0.03 slip",
                  run(recs, en, ex, TIME_11, tf, 0.03, train))
        risk_line("test   " + name + " @ $0.03 slip",
                  run(recs, en, ex, TIME_11, tf, 0.03, test))
        print()

    section("6. WHAT RUNS TODAY")
    print(row("train  live", run(recs, "cur", "cur", datetime.time(15, 0), False, 0.01, train))[0])
    print(row("test   live", run(recs, "cur", "cur", datetime.time(15, 0), False, 0.01, test))[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
