#!/usr/bin/env python3
"""What would actually make the rule better, measured rather than guessed.

Four levers, each swept on the same data with the same honest fill model:

  names    the live watchlist is the top 8 by price x volume; the backtest
           that picked the rule used 20. How much opportunity does the cap cost?
  window   buys must start before 11:00 ET, which leaves only 3 bars a day.
  trend    the 5-day uptrend filter. It blocked a winner on 2026-11-09.
  rsi      the RSI ceiling of 68. SNDK's signal sat exactly on it.

Everything uses the picked exits (1.0 x ATR stop, 2 x ATR trail, 0.5 x ATR
gap slack) so only the lever under test changes.

Read this as a tradeoff table, not a menu to order the best row from: the
best row on this data is the one most likely to be curve fitting.

Usage:
  python3 lab8.py
"""

import datetime
import sys

import lab
from lab2 import build, split, row, section

CUTS = [(datetime.time(11, 0), "11:00"), (datetime.time(11, 30), "11:30")]


def picked_stop(ctx, entry):
    a = ctx["atr"] or entry * 0.004
    return min(entry - 1.0 * a, entry * (1 - 0.001))


def make_entry(rsi_hi):
    def entry(c):
        return (c["rsi"] is not None and 50 <= c["rsi"] <= rsi_hi
                and c["close"] > c["vwap"] and c["ema9"] > c["ema20"]
                and c["close"] > c["or_high"])
    return entry


def top_n(recs, n):
    """Keep the n busiest names of each session, ranked the way signals.py
    ranks the watchlist: last close times last volume."""
    by_day = {}
    for r in recs:
        by_day.setdefault(r["day"], []).append(r)
    keep = set()
    for day, group in by_day.items():
        group.sort(key=lambda r: r["bars"][-1]["c"] * r["bars"][-1]["v"],
                   reverse=True)
        for r in group[:n]:
            keep.add((r["day"], r["sym"]))
    return [r for r in recs if (r["day"], r["sym"]) in keep]


def main():
    lab.GAP_ATR = 0.5
    lab.EXITS["atr_trail"]["stop"] = picked_stop
    for hi in (68, 72, 75):
        lab.ENTRIES["e%d" % hi] = make_entry(hi)

    recs = build()
    train, _, test, alldays = split(recs)
    print("%d sessions, train %s..%s, test %s..%s"
          % (len(recs), alldays[0], sorted(train)[-1],
             sorted(test)[0], alldays[-1]))

    n_days = {"train": len(train), "test": len(test)}
    configs = []
    for names in (8, 20):
        for cut, cutlabel in CUTS:
            for trend in (True, False):
                for hi in (68, 72):
                    configs.append((names, cut, cutlabel, trend, hi))

    section("PROFIT vs OPPORTUNITY: every row is the same exits, one lever moved")
    print("%-46s %-34s %s" % ("", "TRAIN", "TEST"))
    print("%-46s %-34s %s" % ("config", "n    $      win%   /day", "n    $      win%   /day"))
    print("-" * 118)

    rows = []
    for names, cut, cutlabel, trend, hi in configs:
        subset = recs if names == 20 else top_n(recs, names)
        key = "e%d" % hi
        label = "%d names, by %s, trend%s, rsi<=%d" % (
            names, cutlabel, "+" if trend else "-", hi)
        out = {}
        for tag, dayset in (("train", train), ("test", test)):
            t = lab.run_days(subset, key, "atr_trail", cut, trend,
                             lab.SLIP, dayset)
            m = lab.metrics(t)
            out[tag] = (m, len(t) / max(1, n_days[tag]))
        rows.append((label, out))
        print("%-46s n=%-3d $=%-8.0f %-5.1f %-5.2f     n=%-3d $=%-8.0f %-5.1f %-5.2f" % (
            label,
            out["train"][0]["n"], out["train"][0]["total"], out["train"][0]["winpct"], out["train"][1],
            out["test"][0]["n"], out["test"][0]["total"], out["test"][0]["winpct"], out["test"][1]))

    section("THE LIVE CONFIG, isolated")
    live = next(r for r in rows if r[0] == "8 names, by 11:00, trend+, rsi<=68")
    print("  what runs today      train $%-7.0f  test $%-7.0f  (%.2f and %.2f trades/day)"
          % (live[1]["train"][0]["total"], live[1]["test"][0]["total"],
             live[1]["train"][1], live[1]["test"][1]))
    same20 = next(r for r in rows if r[0] == "20 names, by 11:00, trend+, rsi<=68")
    print("  same rule, 20 names  train $%-7.0f  test $%-7.0f  (%.2f and %.2f trades/day)"
          % (same20[1]["train"][0]["total"], same20[1]["test"][0]["total"],
             same20[1]["train"][1], same20[1]["test"][1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
