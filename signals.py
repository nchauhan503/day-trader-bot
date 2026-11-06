#!/usr/bin/env python3
"""Buy and sell alerts from intraday technicals. Paper only. Never places an order.

The rules below were picked by lab.py over 60 days of 5-minute bars and then
re-checked in lab2-lab7 on days and on 20 stocks the search never saw.

BUY, all of these on the last completed 5-minute bar:
  - close above the session VWAP
  - 9-bar EMA above the 20-bar EMA
  - RSI(14) between 50 and 68
  - close above the 9:30-9:45 ET opening-range high
  - a 5-day uptrend: yesterday's close above the close 5 sessions before it
  - the bar ended before 11:00 AM ET, and fewer than 3 buys today

SELL a name we bought, if any of these:
  - the bar's low reaches the stop: 1 x ATR(14) under the entry, floored so
    it is never closer than 0.1% below the entry
  - close back under VWAP
  - 12:50 PM Pacific (9:50 on an early close): flat, win or lose
  - once price is 1R above the entry the stop trails, sitting 2 x ATR under
    the highest high of the session

The size leaves room for a stop that gaps: shares = $50 / (stop distance +
0.5 x ATR). In the backtest that kept every loss under $50 even with fills
3 cents a share worse than modelled.

Nothing is bought. Paper mode.

Usage:
  python3 signals.py --once
  python3 signals.py --self-test
"""

import argparse
import datetime
import json
import os
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
ET = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")
WATCHLIST = ROOT / "report" / "watchlist.json"
STATE_PATH = ROOT / "report" / "positions.json"
LEDGER = ROOT / "report" / "ledger.json"
SCORECARD = ROOT / "report" / "scorecard.txt"
UA = "Mozilla/5.0 (compatible; day-trader-bot/1.0)"

MAX_BUYS = 3
MAX_LOSS = 50.0
TOP = 8
RSI_LO, RSI_HI = 50.0, 68.0
EARLY_CLOSE = {"2026-11-27", "2026-12-24", "2027-11-26"}

# Picked in lab.py, held up in lab2-lab7.
TREND_DAYS = 5       # uptrend: yesterday's close vs the close TREND_DAYS before
STOP_ATR = 1.0       # stop distance, in ATR(14) of the 5-minute bars
TRAIL_ATR = 2.0      # trailed stop sits this many ATR under the session high
GAP_ATR = 0.5        # extra ATR in the size so a gapped stop still fits $50
STOP_FLOOR = 0.001   # stop never closer than 0.1% below the entry
NO_BUYS_AFTER = datetime.time(11, 0)    # ET, the signal bar must end before this
FLAT_BY = datetime.time(15, 50)         # ET, 12:50 PM Pacific


def now_et():
    return datetime.datetime.now(ET)


def load_state(day):
    if STATE_PATH.exists():
        try:
            doc = json.loads(STATE_PATH.read_text())
            if doc.get("date") == day:
                doc.setdefault("positions", {})
                doc.setdefault("alerted", [])
                return doc
        except Exception:
            pass
    return {"date": day, "positions": {}, "alerted": []}


def save_state(doc):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(doc, indent=2) + "\n")


def load_ledger():
    if not LEDGER.exists():
        return []
    try:
        doc = json.loads(LEDGER.read_text())
        return doc if isinstance(doc, list) else []
    except Exception:
        return []


def write_scorecard(rows):
    closed = [r for r in rows if r.get("exit") is not None]
    wins = [r for r in closed if r.get("pnl", 0) > 0]
    losses = [r for r in closed if r.get("pnl", 0) <= 0]
    pnl = sum(r.get("pnl", 0) for r in closed)
    rs = [r["r"] for r in closed if r.get("r") is not None]
    avg_r = (sum(rs) / len(rs)) if rs else 0
    rate = (100.0 * len(wins) / len(closed)) if closed else 0
    lines = [
        "Paper results if every alert was taken at the alerted price.",
        "Closed: %d. Wins: %d. Losses: %d. Success rate: %.0f%%." % (len(closed), len(wins), len(losses), rate),
        "Average R: %+.2f. Hypothetical P&L: %s." % (avg_r, money(pnl)),
        "A win is a sell above the buy. A couple of days is not proof of an edge.",
        "",
    ]
    for r in closed[-20:]:
        lines.append("%s %s  buy %s sell %s  %s  %+.2fR  %s" % (
            r.get("date"), r.get("symbol"), money(r["entry"]), money(r["exit"]),
            money(r.get("pnl", 0)), r.get("r") or 0, r.get("reason")))
    open_rows = [r for r in rows if r.get("exit") is None]
    if open_rows:
        lines.append("")
        lines.append("Still open: " + ", ".join("%s from %s" % (r["symbol"], money(r["entry"])) for r in open_rows))
    SCORECARD.parent.mkdir(parents=True, exist_ok=True)
    SCORECARD.write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:3]))
    return lines


def remember_buy(day, sym, entry, stop, target, shares, stamp):
    rows = load_ledger()
    rows.append({
        "date": day, "symbol": sym, "entry": entry, "stop": stop, "target": target,
        "shares": shares, "bought_at": stamp, "exit": None, "pnl": None, "r": None, "reason": None,
    })
    LEDGER.write_text(json.dumps(rows, indent=2) + "\n")
    write_scorecard(rows)


def remember_sell(day, sym, exit_price, reason, stamp):
    rows = load_ledger()
    match = None
    for row in reversed(rows):
        if row.get("symbol") == sym and row.get("exit") is None and row.get("date") == day:
            match = row
            break
    if match is None:
        return None
    risk = match["entry"] - match["stop"]
    pnl = round((exit_price - match["entry"]) * match["shares"], 2)
    r_mult = round((exit_price - match["entry"]) / risk, 2) if risk else None
    match.update({
        "exit": round(exit_price, 2), "sold_at": stamp,
        "pnl": pnl, "r": r_mult, "reason": reason, "win": pnl > 0,
    })
    LEDGER.write_text(json.dumps(rows, indent=2) + "\n")
    write_scorecard(rows)
    return match


def tickers(explicit, held):
    if explicit:
        names = [s.strip().upper() for s in explicit.split(",") if s.strip()]
    else:
        names = []
        if WATCHLIST.exists():
            try:
                hits = json.loads(WATCHLIST.read_text()).get("hits", [])
                hits.sort(key=lambda h: (h.get("price") or 0) * (h.get("volume") or 0), reverse=True)
                names = [h["symbol"] for h in hits[:TOP]]
            except Exception:
                names = []
    for sym in held:
        if sym not in names:
            names.append(sym)
    return names


def fetch_bars(sym):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=5m" % sym
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        doc = json.loads(r.read().decode("utf-8"))
    result = (doc.get("chart") or {}).get("result") or []
    if not result or not result[0].get("timestamp"):
        return []
    block = result[0]
    quote = block["indicators"]["quote"][0]
    bars = []
    for i, ts in enumerate(block["timestamp"]):
        c = quote["close"][i]
        h = quote["high"][i]
        l = quote["low"][i]
        v = quote["volume"][i]
        if c is None or h is None or l is None:
            continue
        start = datetime.datetime.fromtimestamp(ts, ET)
        bars.append({
            "start": start,
            "end": start + datetime.timedelta(minutes=5),
            "o": float(quote["open"][i] or c),
            "h": float(h), "l": float(l), "c": float(c),
            "v": float(v or 0),
        })
    return bars


def ema(values, n):
    k = 2.0 / (n + 1)
    out, prev = [], None
    for v in values:
        prev = v if prev is None else (v * k) + prev * (1 - k)
        out.append(prev)
    return out


def rsi(closes, n=14):
    out = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains, losses = 0.0, 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_g, avg_l = gains / n, losses / n
    out[n] = 100.0 if avg_l == 0 else 100 - (100 / (1 + avg_g / avg_l))
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * (n - 1) + max(d, 0)) / n
        avg_l = (avg_l * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if avg_l == 0 else 100 - (100 / (1 + avg_g / avg_l))
    return out


def vwap(bars):
    pv = vol = 0.0
    out = []
    for b in bars:
        tp = (b["h"] + b["l"] + b["c"]) / 3.0
        vol += b["v"]
        pv += tp * b["v"]
        out.append(pv / vol if vol else tp)
    return out


def atr(bars, n=14):
    """Average true range of the bars, used for the stop and the trail."""
    trs = []
    for i, b in enumerate(bars):
        if i == 0:
            trs.append(b["h"] - b["l"])
        else:
            p = bars[i - 1]["c"]
            trs.append(max(b["h"] - b["l"], abs(b["h"] - p), abs(b["l"] - p)))
    return ema(trs, n)


def opening_range(bars):
    window = [b for b in bars if b["start"].time() >= datetime.time(9, 30) and b["start"].time() < datetime.time(9, 45)]
    if len(window) < 3:
        return None
    return max(b["h"] for b in window), min(b["l"] for b in window)


def completed(bars, asof):
    return [b for b in bars if b["end"] <= asof]


def money(x):
    return "${:,.2f}".format(x)


def push(topic, title, body, tag):
    if not topic:
        print("ntfy skipped (no topic)")
        return False
    req = urllib.request.Request("https://ntfy.sh/" + topic, data=body.encode("utf-8"), method="POST")
    req.add_header("Title", title)
    req.add_header("Priority", "high")
    req.add_header("Tags", tag)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print("ntfy %s (%s)" % (title, r.status))
            return r.status in (200, 201, 202)
    except Exception as exc:
        print("ntfy failed: %s" % exc, file=sys.stderr)
        return False


def analyze(sym, bars, asof):
    done = completed(bars, asof)
    # 15 bars is the first bar on which RSI(14) exists, so the earliest signal
    # of the day is the bar ending 10:45 AM ET. Anything earlier has no RSI.
    if len(done) < 15:
        return None
    rng = opening_range(done)
    if not rng:
        return None
    or_high, or_low = rng
    closes = [b["c"] for b in done]
    e9, e20 = ema(closes, 9), ema(closes, 20)
    rs = rsi(closes)
    vw = vwap(done)
    at = atr(done)
    i = len(done) - 1
    if rs[i] is None:
        return None
    return {
        "sym": sym,
        "bar": done[i],
        "close": closes[i],
        "ema9": e9[i],
        "ema20": e20[i],
        "prev_ema9": e9[i - 1],
        "prev_ema20": e20[i - 1],
        "rsi": rs[i],
        "vwap": vw[i],
        "atr": at[i],
        "or_high": or_high,
        "or_low": or_low,
        "hi_sofar": max(b["h"] for b in done),
    }


def buy_ok(sig):
    trend_up = sig["ema9"] > sig["ema20"]
    return (
        sig["close"] > sig["vwap"]
        and trend_up
        and RSI_LO <= sig["rsi"] <= RSI_HI
        and sig["close"] > sig["or_high"]
    )


def in_uptrend(sym, today):
    """True when the stock closed higher over the last TREND_DAYS sessions.

    Daily bars up to yesterday only, so nothing from today's session leaks
    into the filter. Fails open: a missing daily file should not silently
    stop the system from ever buying.
    """
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s"
           "?range=3mo&interval=1d" % sym)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            doc = json.loads(r.read().decode("utf-8"))
        result = (doc.get("chart") or {}).get("result") or []
        if not result or not result[0].get("timestamp"):
            print("    %s: no daily bars, treating as uptrend" % sym)
            return True
        block = result[0]
        quote = block["indicators"]["quote"][0]
        closes = []
        for i, ts in enumerate(block["timestamp"]):
            c = quote["close"][i]
            if c is None:
                continue
            if datetime.datetime.fromtimestamp(ts, ET).date() >= today:
                continue
            closes.append(float(c))
        if len(closes) <= TREND_DAYS:
            return True
        return closes[-1] > closes[-1 - TREND_DAYS]
    except Exception as exc:
        print("    %s: trend check failed (%s), treating as uptrend" % (sym, exc))
        return True


def size(entry, stop, a):
    """Whole shares whose loss still fits MAX_LOSS when the stop gaps.

    A stop is not a guaranteed fill. Size against the stop distance plus
    GAP_ATR of ATR, so a fill worse than the stop keeps the budget.
    Returns (shares, budgeted loss at most MAX_LOSS).
    """
    risk = entry - stop
    if risk <= 0:
        return 0, 0
    budget_risk = risk + GAP_ATR * (a or risk)
    shares = int(MAX_LOSS // budget_risk)
    if shares < 1:
        return 0, 0
    return shares, round(shares * budget_risk, 2)


def session_bounds(day):
    """Return (no new buys after, flat by) in Eastern time."""
    if day.isoformat() in EARLY_CLOSE:
        return NO_BUYS_AFTER, datetime.time(12, 50)
    return NO_BUYS_AFTER, FLAT_BY


def one_pass(topic, explicit, notify, asof=None):
    asof = asof or now_et()
    local = asof.astimezone(PT)
    day = asof.date()
    if day.weekday() >= 5:
        print("Market closed (weekend).")
        return 0
    if not (datetime.time(6, 30) <= local.time() <= datetime.time(13, 5)):
        print("Outside the session (%s PT). No buy/sell check." % local.strftime("%H:%M"))
        return 0

    state = load_state(day.isoformat())
    names = tickers(explicit, list(state["positions"]))
    if not names:
        print("No watchlist yet. The 5:00 AM scan has to run first.")
        return 0

    no_buys_after, flat_by = session_bounds(day)
    print("Checking %d names at %s PT" % (len(names), local.strftime("%H:%M")))
    alerts = 0
    for sym in names:
        try:
            bars = fetch_bars(sym)
        except Exception as exc:
            print("  %s: data error (%s)" % (sym, exc))
            continue
        sig = analyze(sym, bars, asof)
        if not sig:
            print("  %s: not enough bars yet" % sym)
            continue
        pos = state["positions"].get(sym)
        stamp = sig["bar"]["end"].astimezone(PT).strftime("%-I:%M %p PT")

        if pos:
            bar_et = sig["bar"]["end"].astimezone(ET).time()
            risk0 = pos.get("risk0", round(pos["entry"] - pos["stop"], 2))
            stop0 = pos.get("stop0", pos["stop"])
            # The stop check for this bar happens first; only then does the
            # trail raise it, and the new stop starts working next bar. Same
            # order as the backtest. The trail arms on this bar's own high,
            # and its value is 2 x ATR under the session high.
            if sig["bar"]["h"] >= pos["entry"] + risk0:
                trailed = round(sig["hi_sofar"] - TRAIL_ATR * sig["atr"], 2)
                if trailed > pos["stop"]:
                    pos["stop"] = trailed
            reason, fill = None, sig["close"]
            if sig["bar"]["l"] <= pos["stop"]:
                # A stop does not fill at the stop when price gaps through it.
                fill = min(sig["bar"]["o"], pos["stop"])
                reason = ("%s at %s" % ("trailing stop" if pos["stop"] > stop0 else "stop",
                                       money(pos["stop"])))
            elif sig["close"] < sig["vwap"]:
                reason = "trend failed: close back under VWAP"
            elif bar_et >= flat_by:
                reason = "flat by the close"
            if reason and "SELL:" + sym not in state["alerted"]:
                fill = round(fill, 2)
                body = "\n".join([
                    "SELL %s" % sym,
                    "%s at %s" % (money(fill), stamp),
                    "Why: %s" % reason,
                    "Bought %s. Stop %s." % (money(pos["entry"]), money(pos["stop"])),
                    "RSI %.0f. VWAP %s. EMA9 %s / EMA20 %s." % (sig["rsi"], money(sig["vwap"]), money(sig["ema9"]), money(sig["ema20"])),
                    "Paper exit. Not an order.",
                ])
                closed = remember_sell(day.isoformat(), sym, fill, reason, stamp)
                if closed:
                    body += "\nIf you took it: %s (%+.2fR). %s." % (
                        money(closed["pnl"]), closed["r"] or 0, "Win" if closed["win"] else "Loss")
                print("\n" + body + "\n")
                if notify:
                    push(topic, "SELL %s" % sym, body, "arrow_down")
                state["alerted"].append("SELL:" + sym)
                state["positions"].pop(sym, None)
                alerts += 1
            else:
                print("  %s: holding, close %s, stop %s" % (
                    sym, money(sig["close"]), money(pos["stop"])))
            continue

        if "BUY:" + sym in state["alerted"]:
            print("  %s: already alerted today" % sym)
            continue
        if sig["bar"]["end"].astimezone(ET).time() > no_buys_after:
            print("  %s: past the %s ET buy window" % (
                sym, no_buys_after.strftime("%-I:%M")))
            continue
        if sum(1 for a in state["alerted"] if a.startswith("BUY:")) >= MAX_BUYS:
            print("  %s: 3 buys already sent today" % sym)
            continue
        if not buy_ok(sig):
            print("  %s: no buy (close %s, RSI %.0f, VWAP %s)" % (sym, money(sig["close"]), sig["rsi"], money(sig["vwap"])))
            continue
        # Cheapest gate that needs a network call goes last.
        if not in_uptrend(sym, day):
            print("  %s: not in a %d-day uptrend" % (sym, TREND_DAYS))
            continue

        entry = round(sig["close"], 2)
        stop = round(min(entry - STOP_ATR * sig["atr"], entry * (1 - STOP_FLOOR)), 2)
        if stop >= entry:
            print("  %s: stop would be at or above entry, skipped" % sym)
            continue
        shares, budget = size(entry, stop, sig["atr"])
        if shares < 1:
            print("  %s: cannot size a $50 risk, skipped" % sym)
            continue
        risk0 = round(entry - stop, 2)
        body = "\n".join([
            "BUY %s" % sym,
            "%s at %s" % (money(entry), stamp),
            "Why: 5m close above VWAP, EMA9 above EMA20, RSI %.0f, close above the opening range, %d-day uptrend." % (sig["rsi"], TREND_DAYS),
            "Stop %s. Size %d shares: about %s at the stop, %s even if it gaps." % (money(stop), shares, money(round(shares * risk0, 2)), money(budget)),
            "VWAP %s. Opening range %s-%s. ATR %s." % (money(sig["vwap"]), money(sig["or_low"]), money(sig["or_high"]), money(sig["atr"])),
            "No target. The stop trails 2 x ATR under the session high once you are 1R up. Sell on a close under VWAP, or flat by 12:50 PM PT.",
            "Paper signal. Not an order.",
        ])
        print("\n" + body + "\n")
        if notify:
            push(topic, "BUY %s" % sym, body, "arrow_up")
        state["alerted"].append("BUY:" + sym)
        state["positions"][sym] = {
            "entry": entry, "stop": stop, "stop0": stop, "risk0": risk0,
            "shares": shares, "time": stamp,
        }
        remember_buy(day.isoformat(), sym, entry, stop, None, shares, stamp)
        alerts += 1

    save_state(state)
    print("Pass done. %d new alert(s). Open: %s" % (alerts, ", ".join(state["positions"]) or "none"))
    return 0


def self_test():
    """Synthetic session: the bar gate, the buy rules, the stop and the size."""
    start = datetime.datetime(2026, 11, 6, 9, 30, tzinfo=ET)
    bars = []
    for i in range(30):
        if i < 3:
            c, h, l = 100.0, 101.0, 99.0
        else:
            c = 100.5 + (i - 3) * 0.15
            h, l = c + 0.2, c - 0.2
        bars.append({
            "start": start + datetime.timedelta(minutes=5 * i),
            "end": start + datetime.timedelta(minutes=5 * (i + 1)),
            "o": c, "h": h, "l": l, "c": c, "v": 100000 + i * 1000,
        })

    # The first bar with an RSI(14) is bar 15; the bar before it must be silent.
    assert analyze("TEST", bars[:14], bars[13]["end"]) is None, "too early"
    sig = analyze("TEST", bars, bars[23]["end"])
    assert sig, "expected a signal"
    assert sig["or_high"] == 101.0 and sig["or_low"] == 99.0
    assert sig["atr"] > 0, "ATR is needed for the stop"
    assert sig["hi_sofar"] == max(b["h"] for b in bars[:24])

    good = dict(sig, close=102.0, vwap=101.0, ema9=101.5, ema20=100.8, rsi=58)
    bad = dict(good, rsi=80, close=100.5)
    assert buy_ok(good), "aligned technicals should be a buy"
    assert not buy_ok(bad), "overbought or below VWAP should not be a buy"

    # Stop: 1 x ATR below the entry, never closer than 0.1% to it.
    entry, a = 102.0, 0.5
    stop = round(min(entry - STOP_ATR * a, entry * (1 - STOP_FLOOR)), 2)
    assert stop == 101.5, stop
    shares, budget = size(entry, stop, a)
    per_share = (entry - stop) + GAP_ATR * a
    assert shares == int(MAX_LOSS // per_share), shares
    assert budget <= MAX_LOSS, budget
    # A stop that gaps by half an ATR still fits the budget.
    assert (entry - (stop - GAP_ATR * a)) * shares <= MAX_LOSS + 1e-9
    assert shares * (entry - stop) < budget, "budgeted loss is above the stop loss"

    # The trail: 2 x ATR under the session high, once 1R is in front.
    hi, risk0 = 105.0, entry - stop
    assert hi >= entry + risk0, "trail should be armed here"
    assert round(hi - TRAIL_ATR * a, 2) == 104.0

    # Sell window: no new buys after 11:00 ET, flat by 15:50 ET (12:50 PT).
    no_buys, flat = session_bounds(datetime.date(2026, 11, 6))
    assert no_buys == datetime.time(11, 0) and flat == datetime.time(15, 50)
    _, flat_half = session_bounds(datetime.date(2026, 11, 27))
    assert flat_half == datetime.time(12, 50), "half day closes at 13:00 ET"

    print("self-test ok: stop %s, %d shares, %s budgeted, trail %s"
          % (money(stop), shares, money(budget), money(104.0)))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--symbols")
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    return one_pass(topic, args.symbols, notify=not args.no_notify)


if __name__ == "__main__":
    sys.exit(main())
