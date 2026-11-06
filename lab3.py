#!/usr/bin/env python3
"""Knife-edge checks on the rule lab picked.

If moving one dial a little flips the result from good to bad, the rule was
fitted to noise, not to a pattern. Each block moves one dial away from the
picked rule and leaves everything else alone.

Usage:
  python3 lab3.py
"""

import datetime
import sys

import lab
from lab2 import build, split, run, row, section

TIME_11 = datetime.time(11, 0)


def sweep_cuts(recs, train, test):
    section("A. BUY CUTOFF: is 11:00 ET a spike or a plateau?")
    print("entry cur, atr_trail exit, 5-day uptrend only, slip $0.01/side\n")
    for h, m in [(11, 0), (11, 30), (12, 0), (12, 30), (13, 0), (14, 0), (15, 0)]:
        cut = datetime.time(h, m)
        tr = run(recs, "cur", "atr_trail", cut, True, lab.SLIP, train)
        te = run(recs, "cur", "atr_trail", cut, True, lab.SLIP, test)
        print(row("train  buys before %02d:%02d ET" % (h, m), tr)[0])
        print(row("test   buys before %02d:%02d ET" % (h, m), te)[0])


def sweep_stops(recs, train, test):
    section("B. STOP WIDTH: 1.5 x ATR is the picked value")
    original = lab.EXITS["atr_trail"]["stop"]
    for mult in (1.0, 1.25, 1.5, 1.75, 2.0, 2.5):
        def make(m):
            def stop(ctx, entry):
                return entry - m * (ctx["atr"] or entry * 0.004)
            return stop
        lab.EXITS["atr_trail"]["stop"] = make(mult)
        tr = run(recs, "cur", "atr_trail", TIME_11, True, lab.SLIP, train)
        te = run(recs, "cur", "atr_trail", TIME_11, True, lab.SLIP, test)
        print(row("train  stop %.2f x ATR" % mult, tr)[0])
        print(row("test   stop %.2f x ATR" % mult, te)[0])
    lab.EXITS["atr_trail"]["stop"] = original


def sweep_trail(recs, train, test):
    section("C. TRAIL WIDTH: 2.0 x ATR below the high is the picked value")
    original = lab.EXITS["atr_trail"]["trail"]
    for mult in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0):
        def make(m):
            def trail(ctx, pos):
                if ctx["bar"]["h"] >= pos["entry"] + pos["risk0"]:
                    return max(pos["stop"], ctx["hi_sofar"] - m * (ctx["atr"] or 0))
                return None
            return trail
        lab.EXITS["atr_trail"]["trail"] = make(mult)
        tr = run(recs, "cur", "atr_trail", TIME_11, True, lab.SLIP, train)
        te = run(recs, "cur", "atr_trail", TIME_11, True, lab.SLIP, test)
        print(row("train  trail %.1f x ATR" % mult, tr)[0])
        print(row("test   trail %.1f x ATR" % mult, te)[0])
    lab.EXITS["atr_trail"]["trail"] = original


def sweep_trend(recs, train, test):
    section("D. UPTREND FILTER: on vs off, and does it have to be 5 days?")
    for n, on in [(None, False), (2, True), (4, True), (5, True), (10, True), (20, True)]:
        r = recs if n is None else build(n)
        t, _, e, _ = split(r)
        tr = run(r, "cur", "atr_trail", TIME_11, on, lab.SLIP, t)
        te = run(r, "cur", "atr_trail", TIME_11, on, lab.SLIP, e)
        label = "no trend filter" if n is None else "%d-day uptrend" % n
        print(row("train  " + label, tr)[0])
        print(row("test   " + label, te)[0])


def main():
    recs = build()
    train, _, test, alldays = split(recs)
    print("%d sessions, train %s..%s, test %s..%s"
          % (len(recs), alldays[0], sorted(train)[-1], sorted(test)[0], alldays[-1]))
    sweep_cuts(recs, train, test)
    sweep_stops(recs, train, test)
    sweep_trail(recs, train, test)
    sweep_trend(recs, train, test)
    return 0


if __name__ == "__main__":
    sys.exit(main())
