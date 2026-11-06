#!/usr/bin/env python3
"""Risk check on the final rule before it goes anywhere near the phone.

The rule is: entry 'cur', atr_trail exit with a 1.0 x ATR stop, buys only
before 11:00 ET, 5-day uptrend only.

The $50 risk budget assumes you get filled at the stop. A gap through it
loses more than $50. This measures how often, and how badly.

Usage:
  python3 lab5.py
"""

import datetime
import sys
from collections import Counter

import lab
from lab2 import build, split, run, section

TIME_11 = datetime.time(11, 0)


def set_stop(mult, floor_pct):
    def stop(ctx, entry):
        atr = ctx["atr"] or entry * 0.004
        return min(entry - mult * atr, entry * (1 - floor_pct))
    return stop


def report(name, trades, slip):
    if not trades:
        print("%s: no trades" % name)
        return
    pnls = sorted(t["pnl"] for t in trades)
    over = [t for t in trades if t["pnl"] < -50]
    worst = pnls[0]
    total = sum(pnls)
    print("%s" % name)
    print("  trades %d   total %+.2f   avg %+.2f   worst %+.2f   best %+.2f"
          % (len(trades), total, total / len(trades), pnls[0], pnls[-1]))
    print("  lost more than $50: %d of %d (%.0f%%)   more than $75: %d"
          % (len(over), len(trades), 100.0 * len(over) / len(trades),
             sum(1 for t in trades if t["pnl"] < -75)))
    per_day = Counter(t["day"] for t in trades)
    print("  biggest buys on one day: %d   days with a buy: %d"
          % (max(per_day.values()), len(per_day)))
    notionals = [t["entry"] * t["shares"] for t in trades]
    print("  position size: smallest %.0f  biggest %.0f  median %.0f"
          % (min(notionals), max(notionals), sorted(notionals)[len(notionals) // 2]))
    print("  exits:", dict(Counter(t["reason"] for t in trades)))
    print()


def main():
    lab.EXITS["atr_trail"]["stop"] = set_stop(1.0, 0.001)
    recs = build()
    train, _, test, alldays = split(recs)
    print("%d sessions, train %s..%s, test %s..%s, stop 1.0 x ATR\n"
          % (len(recs), alldays[0], sorted(train)[-1], sorted(test)[0], alldays[-1]))

    section("SAME BASKET (the 20 names used to pick the rule)")
    report("train, slip $0.01", run(recs, "cur", "atr_trail", TIME_11, True, 0.01, train), 0.01)
    report("test,  slip $0.01", run(recs, "cur", "atr_trail", TIME_11, True, 0.01, test), 0.01)
    report("test,  slip $0.03", run(recs, "cur", "atr_trail", TIME_11, True, 0.03, test), 0.03)

    section("UNSEEN BASKET (20 names never used to pick the rule)")
    other = __import__("lab2").build_other()
    ot, _, ote, odays = split(other)
    report("train, slip $0.01", run(other, "cur", "atr_trail", TIME_11, True, 0.01, ot), 0.01)
    report("test,  slip $0.01", run(other, "cur", "atr_trail", TIME_11, True, 0.01, ote), 0.01)

    section("THE RULE RUNNING TODAY, for comparison")
    lab.EXITS["atr_trail"]["stop"] = set_stop(1.5, 0.0)
    report("live rules, test", run(recs, "cur", "cur", datetime.time(15, 0), False, 0.01, test), 0.01)
    return 0


if __name__ == "__main__":
    sys.exit(main())
