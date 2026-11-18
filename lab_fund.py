#!/usr/bin/env python3
"""Fundamentals as a filter on the intraday technical trigger.

Pre-registered before any held-out number was looked at:

  Trigger   the live buy rule (signals.py), applied to each S&P 100 name:
            5m close above VWAP, EMA9 above EMA20, RSI 50-68, close above
            the 9:30-9:45 opening-range high, 5-day uptrend, signal bar ends
            by 11:30 ET, one trade per name per day.
  Entry     the open of the bar that starts 15 minutes after the signal bar
            ends, plus slippage. That is the delay the test must survive.
  Stop      1 ATR under the fill (floor 0.1%).
  Target    1R above the fill, i.e. 1 ATR.
  Time stop 120 minutes after the fill, or 15:50 ET, whichever is first.
  Filters   none (baseline); revenue up YoY; diluted EPS up YoY; operating
            margin up vs the prior quarter. Each read as of the signal day,
            filed before that day only.

Four variants, chronological split (first 2/3 train, last 1/3 held out).
Reports all four, not the best one.
"""

import datetime
import math
import sys
import time

import fund_pit as fp
import lab

SLIP = lab.SLIP
LAG_BARS = 4                       # signal bar ends s; bar i+4 starts s+15 min
NO_LAG_BARS = 1                    # reference only: next bar's open
NO_BUYS_AFTER = datetime.time(11, 30)
FLAT_BY = datetime.time(15, 50)
HOLD = datetime.timedelta(minutes=120)
FLOOR = 0.001
TRAIN_SHARE = 2 / 3


def fetch_universe():
    data, days_by_sym = {}, {}
    for sym in fp.UNIVERSE:
        try:
            data[sym] = lab.fetch(sym)
            lab.daily_closes(sym)
        except Exception as exc:
            print("  %s: no intraday/daily data (%s)" % (sym, exc))
            continue
        time.sleep(0.12)
    return data


def tech_trigger(sym, ctx, day):
    if any(ctx[k] is None for k in ("vwap", "ema9", "ema20", "rsi", "atr")):
        return False
    if not (ctx["close"] > ctx["vwap"] and ctx["ema9"] > ctx["ema20"]
            and 50 <= ctx["rsi"] <= 68 and ctx["close"] > ctx["or_high"]):
        return False
    t = lab.trend_of(sym, day)
    return t is not None and t > 0


def margin_up(data, asof):
    op = fp.quarters_as_of(data, "operating_income", asof)
    rev = {q["end"]: q["val"] for q in fp.quarters_as_of(data, "revenue", asof)}
    m = [(q["val"] / rev[q["end"]]) for q in op
         if rev.get(q["end"]) and q["end"] in rev]
    if len(m) < 2:
        return None
    return m[-1] > m[-2]


def fund_checks(data, day):
    """name -> {'revenue': bool|None, 'eps': bool|None, 'margin': bool|None}"""
    out = {}
    if not data:
        return {"revenue": None, "eps": None, "margin": None}
    rg = fp.yoy_growth(data, "revenue", day)
    eg = fp.yoy_growth(data, "eps_diluted", day)
    out["revenue"] = None if rg is None else rg > 0
    out["eps"] = None if eg is None else eg > 0
    out["margin"] = margin_up(data, day)
    return out


def trade(rec, i, lag):
    bars, ctx = rec["bars"], rec["ctxs"][i]
    j0 = i + lag
    if j0 >= len(bars):
        return None
    fill = round(bars[j0]["o"] + SLIP, 4)
    atr = ctx["atr"] or fill * 0.004
    stop = min(fill - atr, fill * (1 - FLOOR))
    risk = fill - stop
    if risk <= 0:
        return None
    target = fill + risk
    t_fill = bars[j0]["start"]
    deadline = t_fill + HOLD
    exit_px, why = None, None
    for b in bars[j0:]:
        if b["l"] <= stop:
            exit_px = min(b["o"], stop) - SLIP
            why = "stop"
            break
        if b["h"] >= target:
            exit_px = target - SLIP
            why = "target"
            break
        if b["start"] >= deadline:
            exit_px = b["o"] - SLIP
            why = "time"
            break
        if b["start"].time() >= FLAT_BY:
            exit_px = b["o"] - SLIP
            why = "flat"
            break
    if exit_px is None:
        last = bars[-1]
        exit_px = last["c"] - SLIP
        why = "close"
    r = (exit_px - fill) / risk
    return {"fill": fill, "exit": round(exit_px, 4), "r": r,
            "pct": (exit_px - fill) / fill, "why": why,
            "t": t_fill}


def sessions_for(data):
    days = set()
    for d in data.values():
        days |= set(d)
    return sorted(days)


def build(data, lag):
    """All trades under the trigger, one per name per day, each tagged with
    the day and the fundamental checks read as of that day."""
    rows = []
    cache_fund = {}
    for sym, days in data.items():
        for day in sessions_for({sym: days}):
            rec = lab.prep(sym, day, days[day])
            if rec is None:
                continue
            for i, ctx in enumerate(rec["ctxs"]):
                if ctx["bar"]["end"].time() > NO_BUYS_AFTER:
                    break
                if not tech_trigger(sym, ctx, day):
                    continue
                t = trade(rec, i, lag)
                if t is None:
                    break
                if sym not in cache_fund:
                    cache_fund[sym] = None
                fund = fund_checks(_fund(sym), day)
                rows.append({"sym": sym, "day": day, **t, "fund": fund})
                break
    return rows


_FUND = {}


def _fund(sym):
    if sym not in _FUND:
        try:
            _FUND[sym] = fp.load(sym)
        except Exception:
            _FUND[sym] = {}
    return _FUND[sym]


def stats(trades):
    n = len(trades)
    if n == 0:
        return {"n": 0}
    rs = [t["r"] for t in trades]
    mean = sum(rs) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in rs) / (n - 1)) if n > 1 else 0
    return {"n": n, "win": 100.0 * sum(1 for r in rs if r > 0) / n,
            "meanR": mean, "t": (mean / sd * math.sqrt(n)) if sd else 0,
            "sumpct": sum(t["pct"] for t in trades) * 100}


def variant(rows, key):
    if key is None:
        return rows
    return [r for r in rows if r["fund"].get(key) is True]


def split_days(rows, days):
    cut = days[int(len(days) * TRAIN_SHARE)]
    train = [r for r in rows if r["day"] < cut]
    test = [r for r in rows if r["day"] >= cut]
    return train, test, cut


def report(rows, days, label):
    train, test, cut = split_days(rows, days)
    print("\n== %s  (train %s..%s, held-out from %s) ==" % (
        label, days[0], days[int(len(days) * TRAIN_SHARE) - 1], cut))
    print("%-22s %6s %6s %8s %7s %8s   %6s %6s %8s %7s" % (
        "variant", "tr n", "win", "meanR", "t", "sum%", "te n", "win", "meanR", "sum%"))
    for name, key in [("baseline (none)", None), ("revenue up YoY", "revenue"),
                      ("EPS up YoY", "eps"), ("op. margin up", "margin")]:
        a = stats(variant(train, key))
        b = stats(variant(test, key))
        f = lambda s, k, fmt: (fmt % s[k]) if s.get("n") else "   -  "
        print("%-22s %6s %6s %8s %7s %8s   %6s %6s %8s %7s" % (
            name, a["n"], f(a, "win", "%.0f%%") if a["n"] else "-",
            f(a, "meanR", "%+.2f"), f(a, "t", "%+.1f"), f(a, "sumpct", "%+.1f%%"),
            b["n"], f(b, "win", "%.0f%%") if b["n"] else "-",
            f(b, "meanR", "%+.2f"), f(b, "sumpct", "%+.1f%%")))
    return train, test


def main():
    print("loading the S&P 100 universe (intraday + daily, cached)...")
    data = fetch_universe()
    days = sessions_for(data)
    print("names with data: %d   sessions: %d (%s .. %s)" % (
        len(data), len(days), days[0], days[-1]))
    rows_lag = build(data, LAG_BARS)
    rows_now = build(data, NO_LAG_BARS)
    report(rows_lag, days, "15-minute delay (the test that matters)")
    report(rows_now, days, "reference: next bar's open, no delay")
    cov = {k: sum(1 for r in rows_lag if r["fund"].get(k) is not None)
           for k in ("revenue", "eps", "margin")}
    print("\nfundamental data available for %d / %d trades (revenue %d, EPS %d, margin %d)"
          % (sum(1 for r in rows_lag if any(v is not None for v in r["fund"].values())),
             len(rows_lag), cov["revenue"], cov["eps"], cov["margin"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
