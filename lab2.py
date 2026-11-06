#!/usr/bin/env python3
"""Robustness checks for the rule lab picked out of lab.py.

A grid search will always find something that won. These are the checks that
say whether it survives:

  1. ablation  - turn on one change at a time and see which one earns
  2. slippage  - the same rule at 0, 1, 2 and 3 cents per side
  3. thirds    - the same rule on each third of the 60 days
  4. symbols   - the same rule per stock, to see if two lucky names carry it
  5. new names - 20 different stocks that were never in the search

Usage:
  python3 lab2.py
"""

import datetime
import sys

import lab

ET = lab.ET
TIME_11 = datetime.time(11, 0)
TIME_15 = datetime.time(15, 0)


def build(days_back_trend=5):
    data = {}
    for sym in lab.BASKET:
        data[sym] = lab.fetch(sym)
    return assemble(data, days_back_trend)


def build_other():
    other = ["AVGO", "ADBE", "CRM", "ORCL", "V", "MA", "HD", "PG", "JNJ", "MRK",
             "ABBV", "TMO", "MCD", "SBUX", "NKE", "LLY", "CAT", "BA", "GS", "MU"]
    data = {}
    for sym in other:
        data[sym] = lab.fetch(sym)
    return assemble(data, 5)


def assemble(data, days_back_trend):
    recs = []
    for sym, days in data.items():
        dsorted = sorted(days)
        closes = {d: days[d][-1][4] for d in dsorted}
        for idx, d in enumerate(dsorted):
            rec = lab.prep(sym, d, days[d])
            if not rec:
                continue
            rec["trend"] = None
            # Return over the N trading days that ended at yesterday's close,
            # so nothing from today's session leaks into the filter.
            if idx >= days_back_trend + 1:
                rec["trend"] = (closes[dsorted[idx - 1]]
                                / closes[dsorted[idx - 1 - days_back_trend]] - 1)
            recs.append(rec)
    return recs


def split(recs):
    alldays = sorted({r["day"] for r in recs})
    cut = int(len(alldays) * 0.66)
    return set(alldays[:cut]), set(alldays[:cut]), set(alldays[cut:]), alldays


def run(recs, entry, exit_name, cutoff, trend_on, slip, day_set=None):
    return lab.run_days(recs, entry, exit_name, cutoff, trend_on, slip, day_set)


def row(label, trades):
    m = lab.metrics(trades)
    return lab.line(label, m), m


def section(title):
    print("\n" + "=" * 100)
    print(title)
    print("=" * 100)


def main():
    recs = build()
    train, _, test, alldays = split(recs)
    print("%d sessions, train %s..%s (%d days), test %s..%s (%d days)"
          % (len(recs), alldays[0], sorted(train)[-1], len(train),
             sorted(test)[0], alldays[-1], len(test)))

    section("1. ABLATION: one change at a time from the rules that run today")
    print("all rows use entry 'cur'. Live is entry cur / exit cur / by 15:00 / no trend filter.\n")
    variants = [
        ("live (what runs today)",            "cur",   TIME_15, False),
        ("only cutoff moved to 11:00",        "cur",   TIME_11, False),
        ("only 5-day uptrend filter",         "cur",   TIME_15, True),
        ("cutoff 11:00 + uptrend filter",     "cur",   TIME_11, True),
        ("+ atr stop / 2R target",            "atr",   TIME_11, True),
        ("+ atr stop + trail to breakeven",   "trail", TIME_11, True),
        ("+ atr stop + trailing stop, no target", "atr_trail", TIME_11, True),
        ("+ or-low stop + close<vwap exit",   "run",   TIME_11, True),
    ]
    for label, ex, cut, tf in variants:
        tr = run(recs, "cur", ex, cut, tf, lab.SLIP, train)
        te = run(recs, "cur", ex, cut, tf, lab.SLIP, test)
        print(row("train  " + label, tr)[0])
        print(row("test   " + label, te)[0])
        print()

    best = dict(entry="cur", ex="atr_trail", cut=TIME_11, tf=True)

    section("2. SLIPPAGE: the same rule, harsher and kinder fills")
    print("rule: cur entry, atr_trail exit, buys only before 11:00 ET, 5-day uptrend only\n")
    for slip in (0.0, 0.01, 0.02, 0.03):
        tr = run(recs, "cur", "atr_trail", TIME_11, True, slip, train)
        te = run(recs, "cur", "atr_trail", TIME_11, True, slip, test)
        print(row("train  slip $%.2f/side" % slip, tr)[0])
        print(row("test   slip $%.2f/side" % slip, te)[0])

    section("3. STABILITY: same rule, each third of the 60 days")
    thirds = [alldays[i * len(alldays) // 3:(i + 1) * len(alldays) // 3]
              for i in range(3)]
    for i, days in enumerate(thirds, 1):
        t = run(recs, "cur", "atr_trail", TIME_11, True, lab.SLIP, set(days))
        print(row("days %s..%s" % (days[0], days[-1]), t)[0])

    section("4. BY SYMBOL: does one stock carry the result?")
    per = {}
    for tr in run(recs, "cur", "atr_trail", TIME_11, True, lab.SLIP):
        per.setdefault(tr["sym"], []).append(tr)
    for sym in sorted(per, key=lambda s: -lab.metrics(per[s])["total"]):
        print(row("  " + sym, per[sym])[0])
    print("\nexit reasons, all days:", lab.reasons(
        run(recs, "cur", "atr_trail", TIME_11, True, lab.SLIP)))

    section("5. UNSEEN SYMBOLS: 20 stocks that were never in the search")
    other = build_other()
    ot, _, ote, odays = split(other)
    print("%d sessions, train %s..%s, test %s..%s"
          % (len(other), odays[0], sorted(ot)[-1], sorted(ote)[0], odays[-1]))
    for label, ex, cut, tf in [
        ("live rules", "cur", TIME_15, False),
        ("picked rule", "atr_trail", TIME_11, True),
    ]:
        tr = run(other, "cur", ex, cut, tf, lab.SLIP, ot)
        te = run(other, "cur", ex, cut, tf, lab.SLIP, ote)
        print(row("train  " + label, tr)[0])
        print(row("test   " + label, te)[0])
        print()

    section("6. TUNING SENSITIVITY: is the 5-day trend window a lucky number?")
    for n in (3, 5, 10, 15):
        r = build(n)
        t, _, e, _ = split(r)
        tr = run(r, "cur", "atr_trail", TIME_11, True, lab.SLIP, t)
        te = run(r, "cur", "atr_trail", TIME_11, True, lab.SLIP, e)
        print(row("train  %d-day trend" % n, tr)[0])
        print(row("test   %d-day trend" % n, te)[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
