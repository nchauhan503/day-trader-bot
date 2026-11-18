#!/usr/bin/env python3
"""The remaining inputs, pre-registered, on the same trigger.

Added on top of lab_fund.py (which is not changed):

  Earnings window  a 10-Q or 10-K was filed within the last 45 days, i.e. the
                   name is in its post-earnings window. Read from the filing
                   dates in SEC data, filed before the signal day only.
                   (Proxy: the quarterly report, not the 8-K press release.)
  Market regime    SPY is in a 5-day uptrend on the signal day (daily closes,
                   same rule as the stock trend filter).
  Longer hold      2-hour time stop (baseline) vs 3-hour time stop.

Variants are fixed here, before any result is read. Each is run with the
15-minute delay, which is the only entry that matters.

Honesty note: the held-out window in lab_fund.py has already been read once.
Results here on that window are contaminated by that earlier look. The clean
test is the forward window: sessions after the last one in this file's data.
"""

import datetime
import sys

import fund_pit as fp
import lab
import lab_fund as lf

EARNINGS_DAYS = 45
FORMS_Q = ("10-Q", "10-K")


def quarterly_filed_dates(sym):
    d = fp.load(sym)
    out = set()
    for metric in d.values():
        for f in metric:
            if f.get("form") in FORMS_Q:
                out.add(f["filed"])
    return sorted(out)


def in_earnings_window(dates, day):
    dd = datetime.date.fromisoformat(day)
    for s in dates:
        gap = (dd - datetime.date.fromisoformat(s)).days
        if 0 <= gap <= EARNINGS_DAYS:
            return True
    return False


def market_up(day):
    t = lab.trend_of("SPY", day)
    return t is not None and t > 0


def run(data, hold_min):
    lf.HOLD = datetime.timedelta(minutes=hold_min)
    rows = lf.build(data, lf.LAG_BARS)
    cache = {}
    for r in rows:
        if r["sym"] not in cache:
            cache[r["sym"]] = quarterly_filed_dates(r["sym"])
        r["earnings"] = in_earnings_window(cache[r["sym"]], r["day"])
        r["market"] = market_up(r["day"])
    return rows


def show(rows, days, label):
    train, test, cut = lf.split_days(rows, days)
    variants = [
        ("baseline", lambda r: True),
        ("earnings window", lambda r: r["earnings"]),
        ("SPY uptrend", lambda r: r["market"]),
        ("earnings + SPY up", lambda r: r["earnings"] and r["market"]),
    ]
    print("\n== %s ==" % label)
    print("%-20s %22s   %22s" % ("variant", "train  n / meanR / t", "held-out  n / meanR / t"))
    for name, f in variants:
        a = lf.stats([r for r in train if f(r)])
        b = lf.stats([r for r in test if f(r)])
        fa = "%4s  %+.2f  %+.1f" % (a["n"], a["meanR"], a["t"]) if a["n"] else "   -"
        fb = "%4s  %+.2f  %+.1f" % (b["n"], b["meanR"], b["t"]) if b["n"] else "   -"
        print("%-20s %22s   %22s" % (name, fa, fb))


def main():
    data = lf.fetch_universe()
    lab.daily_closes("SPY")
    days = lf.sessions_for(data)
    print("sessions %d (%s .. %s), held-out from %s" % (
        len(days), days[0], days[-1], days[int(len(days) * lf.TRAIN_SHARE)]))
    show(run(data, 120), days, "2-hour hold, 15-min delay")
    show(run(data, 180), days, "3-hour hold, 15-min delay")
    return 0


if __name__ == "__main__":
    sys.exit(main())
