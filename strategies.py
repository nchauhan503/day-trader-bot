#!/usr/bin/env python3
"""Registered strategies, one trade simulator, one scorer.

Each strategy is a function that looks at one session's bars and returns at
most one signal. The rules it implements are written in report/strategy_register.txt,
which is the authority. This file must match it, not the other way round.

    python3 strategies.py S1        # run one strategy on the cached universe
"""

import datetime
import sys

import lab
import lab_fund as lf

SLIP = lab.SLIP
LAG_BARS = 4
HOLD = datetime.timedelta(minutes=120)
FLAT_BY = datetime.time(15, 50)


def simulate(bars, j0, fill, stop, target, be_at=None):
    """Walk bars from the entry bar. Returns (exit_px, reason, exit_time)."""
    risk = fill - stop
    be_armed = False
    deadline = bars[j0]["start"] + HOLD
    for b in bars[j0:]:
        if b["l"] <= stop:
            return min(b["o"], stop) - SLIP, "stop", b["start"]
        if b["h"] >= target:
            return target - SLIP, "target", b["start"]
        if b["start"] >= deadline:
            return b["o"] - SLIP, "time", b["start"]
        if b["start"].time() >= FLAT_BY:
            return b["o"] - SLIP, "flat", b["start"]
        if be_at is not None and not be_armed and b["h"] >= be_at:
            be_armed = True
            stop = max(stop, fill)
    last = bars[-1]
    return last["c"] - SLIP, "close", last["start"]


def trade_from(rec, i, stop, target, be_at=None):
    bars = rec["bars"]
    j0 = i + LAG_BARS
    if j0 >= len(bars):
        return None
    fill = round(bars[j0]["o"] + SLIP, 4)
    if target <= fill or stop >= fill:
        return None
    if bars[j0]["h"] >= target:
        return None
    exit_px, why, t_exit = simulate(bars, j0, fill, stop, target, be_at)
    risk = fill - stop
    return {"fill": fill, "exit": round(exit_px, 4), "why": why,
            "r": (exit_px - fill) / risk, "pct": (exit_px - fill) / fill,
            "t": bars[j0]["start"]}


def s1_orb_volume(rec):
    """S1, see report/strategy_register.txt."""
    bars, ctxs = rec["bars"], rec["ctxs"]
    if len(bars) < 6:
        return None
    or_h = max(b["h"] for b in bars[:4])
    or_l = min(b["l"] for b in bars[:4])
    w = or_h - or_l
    if w <= 0:
        return None
    broke_before = False
    for i in range(4, len(bars)):
        c = ctxs[i]
        t = c["bar"]["end"].time()
        closed_above = c["close"] > or_h
        if closed_above and not broke_before:
            if datetime.time(10, 0) <= t <= datetime.time(11, 30):
                vol_avg = c["vol_avg"] or 0
                if vol_avg > 0 and c["vol"] >= 1.2 * vol_avg:
                    fill_guess = bars[i + LAG_BARS]["o"] if i + LAG_BARS < len(bars) else None
                    if fill_guess is None:
                        return None
                    fill = fill_guess + SLIP
                    return trade_from(rec, i, stop=fill - 0.5 * w,
                                      target=fill + 0.75 * w, be_at=fill + w)
            return None
        if closed_above:
            broke_before = True
    return None


REGISTER = {"S1": s1_orb_volume}


def run(strategy_fn, data):
    """All trades from every session in `data` ({sym: {day: rows}})."""
    rows = []
    for sym, days in data.items():
        for day in sorted(days):
            rec = lab.prep(sym, day, days[day])
            if rec is None:
                continue
            t = strategy_fn(rec)
            if t is None:
                continue
            rows.append({"sym": sym, "day": day, **t})
    return rows


def main(argv):
    name = argv[1] if len(argv) > 1 else "S1"
    fn = REGISTER[name]
    data = lf.fetch_universe()
    days = lf.sessions_for(data)
    rows = run(fn, data)
    train, test, cut = lf.split_days(rows, days)
    print("%s  sessions %d (%s .. %s)  held-out from %s" % (
        name, len(days), days[0], days[-1], cut))
    for label, part in (("all", rows), ("train", train), ("held-out", test)):
        s = lf.stats(part)
        if s["n"] == 0:
            print("  %-9s n=0" % label)
            continue
        print("  %-9s n=%-4d win %3.0f%%  meanR %+.3f  t %+.2f  sum%% %+.1f" % (
            label, s["n"], s["win"], s["meanR"], s["t"], s["sumpct"]))
    import collections
    print("  exits:", dict(collections.Counter(r["why"] for r in rows)))
    print("  per session: %.2f trades" % (len(rows) / len(days)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
