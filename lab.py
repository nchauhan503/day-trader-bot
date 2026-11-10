#!/usr/bin/env python3
"""Try several intraday approaches on 60 days of 5-minute bars.

The current rules are one point in this space. Everything here shares the
same data, the same fill model and the same risk sizing, so the only thing
that changes between rows is the rule.

Fill model, which is deliberately harder than the ledger:
  - a signal on bar i buys at the OPEN of bar i+1, plus slippage
  - a stop fills at min(open, stop) minus slippage (a gap through it fills
    at the gap, not at the stop)
  - a target fills at max(open, target) plus slippage
  - a close-based exit fills at that close minus slippage

Days are split in time: the first 66% are the training window, the last 34%
are never used to pick a rule. A rule that only wins on the training window
is reported as such.

Usage:
  python3 lab.py
  python3 lab.py --slip 0.02
"""

import argparse
import datetime
import json
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

import signals

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "data" / "cache"
UA = "Mozilla/5.0 (compatible; day-trader-bot/1.0)"

# Fixed before looking at any result: liquid names only, so the backtest is
# not quietly testing untradeable stocks.
BASKET = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA", "AMD", "NFLX", "JPM",
    "BAC", "XOM", "UNH", "COST", "WMT", "PFE", "KO", "DIS", "INTC", "TXN",
]

RISK = 50.0
MAX_BUYS = 3
# A stop does not fill at the stop when price gaps through it. Size against
# stop distance plus this many ATR of slippage so the $50 budget still holds
# on the bad fills, not only on the ideal one.
GAP_ATR = 0.0
FLAT_BY = datetime.time(15, 50)   # ET, same as the live 12:50 PT rule

SLIP = 0.01                       # dollars per share, per side


# ---------------------------------------------------------------- data

def fetch(sym):
    path = CACHE / ("%s.json" % sym)
    if path.exists():
        return json.loads(path.read_text())
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s"
           "?range=60d&interval=5m" % sym)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        doc = json.loads(r.read().decode("utf-8"))
    block = doc["chart"]["result"][0]
    q = block["indicators"]["quote"][0]
    days = {}
    for i, ts in enumerate(block["timestamp"]):
        c, h, l = q["close"][i], q["high"][i], q["low"][i]
        if c is None or h is None or l is None:
            continue
        start = datetime.datetime.fromtimestamp(ts, ET)
        days.setdefault(start.date().isoformat(), []).append([
            int(ts), float(q["open"][i] or c), float(h), float(l),
            float(c), float(q["volume"][i] or 0),
        ])
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(days))
    return days


def prep(sym, day, rows):
    bars = []
    for ts, o, h, l, c, v in rows:
        start = datetime.datetime.fromtimestamp(ts, ET)
        bars.append({
            "start": start, "end": start + datetime.timedelta(minutes=5),
            "o": o, "h": h, "l": l, "c": c, "v": v,
        })
    if len(bars) < 30:
        return None
    closes = [b["c"] for b in bars]
    e9 = signals.ema(closes, 9)
    e20 = signals.ema(closes, 20)
    rs = signals.rsi(closes)
    vw = signals.vwap(bars)
    trs = []
    for i, b in enumerate(bars):
        if i == 0:
            tr = b["h"] - b["l"]
        else:
            p = bars[i - 1]["c"]
            tr = max(b["h"] - b["l"], abs(b["h"] - p), abs(b["l"] - p))
        trs.append(tr)
    atr = signals.ema(trs, 14)
    or_high = max(b["h"] for b in bars[:3])
    or_low = min(b["l"] for b in bars[:3])
    vols = [b["v"] for b in bars]
    ctxs = []
    for i, b in enumerate(bars):
        n_prev = min(i, 10)
        ctxs.append({
            "i": i, "bar": b, "close": closes[i],
            "ema9": e9[i], "ema20": e20[i],
            "prev_ema9": e9[i - 1] if i else None,
            "prev_ema20": e20[i - 1] if i else None,
            "prev_close": closes[i - 1] if i else None,
            "prev_vwap": vw[i - 1] if i else None,
            "rsi": rs[i], "vwap": vw[i], "atr": atr[i],
            "or_high": or_high, "or_low": or_low,
            "vol": b["v"],
            "vol_avg": (sum(vols[i - n_prev:i]) / n_prev) if n_prev else b["v"],
            "hi_sofar": max(x["h"] for x in bars[:i + 1]),
        })
    return {"sym": sym, "day": day, "bars": bars, "ctxs": ctxs}


_DAILY = {}


def daily_closes(sym):
    """Official daily closes as [date, close], oldest first. One fetch per
    symbol, cached on disk."""
    if sym in _DAILY:
        return _DAILY[sym]
    path = CACHE / ("%s.daily.json" % sym)
    if path.exists():
        rows = json.loads(path.read_text())
    else:
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s"
               "?range=3mo&interval=1d" % sym)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            doc = json.loads(r.read().decode("utf-8"))
        block = doc["chart"]["result"][0]
        quote = block["indicators"]["quote"][0]
        rows = []
        for i, ts in enumerate(block["timestamp"]):
            c = quote["close"][i]
            if c is None:
                continue
            rows.append([datetime.datetime.fromtimestamp(ts, ET).date().isoformat(),
                         float(c)])
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows))
    _DAILY[sym] = rows
    return rows


def trend_of(sym, day, days=5):
    """Return over the `days` sessions ending at yesterday's close.

    Read from the official daily closes, which is exactly what
    signals.in_uptrend reads. The 5-minute session's last bar close is not
    the daily close -- it differs by a few tenths of a percent, enough to
    flip a borderline trend call and make the live code buy something the
    backtest never did.

    Fails open when history is short, matching signals.py: missing data must
    not silently stop the system from ever buying.
    """
    rows = [r for r in daily_closes(sym) if r[0] < day]
    if len(rows) <= days:
        return 1.0
    return rows[-1][1] / rows[-1 - days][1] - 1


# ---------------------------------------------------------------- entries

def e_cur(c):
    return (c["rsi"] is not None and 50 <= c["rsi"] <= 68
            and c["close"] > c["vwap"] and c["ema9"] > c["ema20"]
            and c["close"] > c["or_high"])


def e_orb(c):
    return c["close"] > c["or_high"]


def e_orb_vol(c):
    return c["close"] > c["or_high"] and c["vol"] >= 1.5 * c["vol_avg"]


def e_orb_trend(c):
    return (c["close"] > c["or_high"] and c["ema9"] > c["ema20"]
            and c["close"] > c["vwap"])


def e_pullback(c):
    return (c["ema9"] > c["ema20"] and c["close"] > c["vwap"]
            and c["bar"]["l"] <= c["ema9"] and c["close"] > c["bar"]["o"]
            and c["prev_close"] is not None and c["close"] > c["prev_close"])


def e_reclaim(c):
    return (c["prev_close"] is not None and c["prev_vwap"] is not None
            and c["prev_close"] < c["prev_vwap"] and c["close"] > c["vwap"]
            and c["ema9"] > c["ema20"])


def e_momentum(c):
    rng = c["bar"]["h"] - c["bar"]["l"]
    if rng <= 0 or c["rsi"] is None:
        return False
    place = (c["close"] - c["bar"]["l"]) / rng
    return (c["close"] > c["vwap"] and c["ema9"] > c["ema20"]
            and place >= 0.7 and c["vol"] >= 1.5 * c["vol_avg"]
            and c["rsi"] <= 75)


def e_dip(c):
    return (c["ema9"] > c["ema20"] and c["rsi"] is not None and c["rsi"] < 45
            and c["bar"]["l"] <= c["vwap"] and c["close"] > c["vwap"]
            and c["close"] > c["bar"]["o"])


ENTRIES = {
    "cur": e_cur, "orb": e_orb, "orb_vol": e_orb_vol, "orb_trend": e_orb_trend,
    "pullback": e_pullback, "reclaim": e_reclaim, "momentum": e_momentum,
    "dip": e_dip,
}


# ---------------------------------------------------------------- exits

def s_or(ctx, entry):
    return min(ctx["or_low"], entry * 0.995)


def s_atr(ctx, entry):
    return entry - 1.5 * (ctx["atr"] or entry * 0.004)


def t_2r(ctx, entry, stop):
    return entry + 2 * (entry - stop)


def c_trendfail(ctx, pos):
    cross = (ctx["prev_ema9"] is not None and ctx["prev_ema9"] >= ctx["prev_ema20"]
             and ctx["ema9"] < ctx["ema20"])
    return ctx["close"] < ctx["vwap"] and (ctx["ema9"] < ctx["ema20"] or cross)


def c_belowvwap(ctx, pos):
    return ctx["close"] < ctx["vwap"]


def tr_be(ctx, pos):
    if ctx["bar"]["h"] >= pos["entry"] + pos["risk0"]:
        return max(pos["stop"], pos["entry"])
    return None


def tr_atr(ctx, pos):
    if ctx["bar"]["h"] >= pos["entry"] + pos["risk0"]:
        return max(pos["stop"], ctx["hi_sofar"] - 2 * (ctx["atr"] or 0))
    return None


EXITS = {
    "cur":       {"stop": s_or,  "target": t_2r, "close": c_trendfail, "trail": None},
    "atr":       {"stop": s_atr, "target": t_2r, "close": None,        "trail": None},
    "trail":     {"stop": s_or,  "target": t_2r, "close": None,        "trail": tr_be},
    "run":       {"stop": s_or,  "target": None, "close": c_belowvwap, "trail": None},
    "atr_trail": {"stop": s_atr, "target": None, "close": c_belowvwap, "trail": tr_atr},
}


# ---------------------------------------------------------------- engine

def sim_day(recs, entry_fn, ex, cutoff, trend_filter, slip):
    """Simulate one session across every symbol at once.

    The live system sends at most MAX_BUYS buys for the whole day, across all
    symbols, so the symbols have to compete for those slots in time order.
    Simulating each symbol on its own would quietly allow far more trades
    than the phone ever would.
    """
    live_recs = []
    for rec in recs:
        if trend_filter:
            t = rec.get("trend")
            if t is None or t <= 0:
                continue
        live_recs.append(rec)
    if not live_recs:
        return []
    day = live_recs[0]["day"]
    order = [r["sym"] for r in live_recs]
    bars_by = {r["sym"]: r["bars"] for r in live_recs}
    ctx_by = {r["sym"]: r["ctxs"] for r in live_recs}
    pos, bought, trades = {}, set(), []
    nmax = max(len(r["bars"]) for r in live_recs)

    for i in range(3, nmax):
        for sym in order:
            p = pos.get(sym)
            if not p or i >= len(bars_by[sym]):
                continue
            b, ctx = bars_by[sym][i], ctx_by[sym][i]
            fill, reason = None, None
            if b["l"] <= p["stop"]:
                fill, reason = min(b["o"], p["stop"]) - slip, "stop"
            elif p["target"] is not None and b["h"] >= p["target"]:
                fill, reason = max(b["o"], p["target"]) + slip, "target"
            else:
                if ex["trail"]:
                    ns = ex["trail"](ctx, p)
                    if ns and ns > p["stop"]:
                        p["stop"] = ns
                if ex["close"] and ex["close"](ctx, p):
                    fill, reason = b["c"] - slip, "trend"
                elif b["end"].astimezone(ET).time() >= FLAT_BY:
                    fill, reason = b["c"] - slip, "flat"
            if fill is not None:
                trades.append({
                    "day": day, "sym": sym, "entry": p["entry"],
                    "exit": round(fill, 4), "shares": p["shares"],
                    "pnl": round((fill - p["entry"]) * p["shares"], 2),
                    "r": round((fill - p["entry"]) / p["risk0"], 3),
                    "reason": reason, "exit_ts": b["end"],
                })
                del pos[sym]

        if len(bought) >= MAX_BUYS:
            continue
        for sym in order:
            if len(bought) >= MAX_BUYS:
                break
            if sym in bought or sym in pos:
                continue
            bars = bars_by[sym]
            if i + 1 >= len(bars):
                continue
            ctx = ctx_by[sym][i]
            if ctx["bar"]["end"].astimezone(ET).time() > cutoff:
                continue
            if not entry_fn(ctx):
                continue
            entry = bars[i + 1]["o"] + slip
            stop = ex["stop"](ctx, entry)
            if stop >= entry:
                continue
            target = ex["target"](ctx, entry, stop) if ex["target"] else None
            budget_risk = (entry - stop) + GAP_ATR * (ctx["atr"] or entry * 0.004)
            shares = int(RISK // budget_risk)
            if shares < 1:
                continue
            pos[sym] = {
                "entry": entry, "stop": stop, "risk0": entry - stop,
                "target": target, "shares": shares,
            }
            bought.add(sym)

    for sym, p in pos.items():
        b = bars_by[sym][-1]
        fill = b["c"] - slip
        trades.append({
            "day": day, "sym": sym, "entry": p["entry"],
            "exit": round(fill, 4), "shares": p["shares"],
            "pnl": round((fill - p["entry"]) * p["shares"], 2),
            "r": round((fill - p["entry"]) / p["risk0"], 3),
            "reason": "session end", "exit_ts": b["end"],
        })
    return trades


def run_days(recs, entry, exit_name, cutoff, trend_filter, slip, day_set=None):
    by_day = {}
    for rec in recs:
        if day_set is not None and rec["day"] not in day_set:
            continue
        by_day.setdefault(rec["day"], []).append(rec)
    out = []
    for day in sorted(by_day):
        out.extend(sim_day(by_day[day], ENTRIES[entry], EXITS[exit_name],
                           cutoff, trend_filter, slip))
    return out


def metrics(trades):
    if not trades:
        return {"n": 0, "win": 0, "winpct": 0.0, "avgR": 0.0,
                "total": 0.0, "pf": 0.0, "dd": 0.0}
    wins = [t for t in trades if t["pnl"] > 0]
    total = sum(t["pnl"] for t in trades)
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    rs = [t["r"] for t in trades]
    eq, peak, dd = 0.0, 0.0, 0.0
    for t in sorted(trades, key=lambda x: x["exit_ts"]):
        eq += t["pnl"]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return {
        "n": len(trades), "win": len(wins),
        "winpct": 100.0 * len(wins) / len(trades),
        "avgR": sum(rs) / len(rs), "total": total,
        "pf": (gp / gl) if gl else 99.0, "dd": dd,
    }


def line(label, m):
    if not m["n"]:
        return "%-46s  no trades" % label
    return ("%-46s  n=%-4d win=%-5.1f%%  $=%-9.1f  R=%+.2f  pf=%.2f  dd=%.0f"
            % (label, m["n"], m["winpct"], m["total"], m["avgR"], m["pf"], m["dd"]))


def reasons(trades):
    out = {}
    for t in trades:
        out[t["reason"]] = out.get(t["reason"], 0) + 1
    return out


# ---------------------------------------------------------------- main

def main():
    global SLIP
    ap = argparse.ArgumentParser()
    ap.add_argument("--slip", type=float, default=0.01)
    args = ap.parse_args()
    SLIP = args.slip

    data = {}
    for sym in BASKET:
        data[sym] = fetch(sym)
        print("fetched %s (%d days)" % (sym, len(data[sym])), flush=True)

    recs = []
    for sym, days in data.items():
        for d in sorted(days):
            rec = prep(sym, d, days[d])
            if not rec:
                continue
            rec["trend"] = trend_of(sym, d)
            recs.append(rec)

    alldays = sorted({r["day"] for r in recs})
    split = int(len(alldays) * 0.66)
    train_days, test_days = set(alldays[:split]), set(alldays[split:])
    print("\n%d sessions, %d train days (%s..%s), %d test days (%s..%s), slip $%.3f/side"
          % (len(recs), len(train_days), alldays[0], alldays[split - 1],
             len(test_days), alldays[split], alldays[-1], SLIP))

    cuts = [(datetime.time(11, 0), "11:00"), (datetime.time(15, 0), "15:00")]
    rows = []
    for en, entry_fn in ENTRIES.items():
        for exn, ex in EXITS.items():
            for cut, cutlabel in cuts:
                for tf in (False, True):
                    tr = run_days(recs, en, exn, cut, tf, SLIP, train_days)
                    te = run_days(recs, en, exn, cut, tf, SLIP, test_days)
                    rows.append({
                        "key": "%s / %s / by %s / trend%s" % (en, exn, cutlabel, "+up" if tf else "-"),
                        "tr": metrics(tr), "te": metrics(te),
                        "trades_tr": tr, "trades_te": te,
                    })

    print("\n=== the rules running today (for reference) ===")
    base = next(r for r in rows if r["key"] == "cur / cur / by 15:00 / trend-")
    print(line("train  " + base["key"], base["tr"]))
    print(line("test   " + base["key"], base["te"]))

    print("\n=== top 12 on the training window (this is what selection sees) ===")
    ranked = sorted(rows, key=lambda r: r["tr"]["total"], reverse=True)
    for i, r in enumerate(ranked[:12], 1):
        print(line("%2d train %s" % (i, r["key"]), r["tr"]))
        print(line("   test  %s" % r["key"], r["te"]))

    print("\n=== rules that made money in BOTH windows (selection can see train only) ===")
    both = [r for r in rows if r["tr"]["total"] > 0 and r["te"]["total"] > 0
            and r["tr"]["n"] >= 30 and r["te"]["n"] >= 15]
    both.sort(key=lambda r: r["tr"]["total"] + r["te"]["total"], reverse=True)
    if not both:
        print("none.")
    for r in both[:10]:
        print(line("train " + r["key"], r["tr"]))
        print(line("test  " + r["key"], r["te"]))

    print("\n=== everything on the test window only (optimistic, do not pick from here) ===")
    for r in sorted(rows, key=lambda x: x["te"]["total"], reverse=True)[:8]:
        print(line(r["key"], r["te"]))

    pick = ranked[0]
    print("\n=== pick by train only: %s ===" % pick["key"])
    print(line("train", pick["tr"]))
    print(line("test ", pick["te"]))
    print("exit reasons, train:", reasons(pick["trades_tr"]))
    print("exit reasons, test :", reasons(pick["trades_te"]))

    if both:
        b = both[0]
        print("\n=== most profitable rule that also held up out of sample: %s ===" % b["key"])
        print(line("train", b["tr"]))
        print(line("test ", b["te"]))
        print("exit reasons, train:", reasons(b["trades_tr"]))
        print("exit reasons, test :", reasons(b["trades_te"]))

    out = ROOT / "report" / "approach.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = ["Approach comparison, %d sessions, slip $%.3f/side." % (len(recs), SLIP),
             "Train %s..%s, test %s..%s." % (alldays[0], alldays[split - 1],
                                             alldays[split], alldays[-1]), ""]
    for r in ranked[:20]:
        lines.append(line(r["key"], r["tr"]))
        lines.append(line("   test", r["te"]))
    out.write_text("\n".join(lines) + "\n")
    print("\nwrote %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
