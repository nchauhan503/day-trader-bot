#!/usr/bin/env python3
"""Replay the live buy/sell rules on recent 5-minute bars.

Same rules as signals.py: one buy per name per day, at most 3 buys a day,
exit on stop, target, trend failure, or 12:50 PM Pacific. The fill is the
alert bar's close, which is what the phone would have shown.
"""

import datetime
import json
import urllib.request
from collections import defaultdict
from zoneinfo import ZoneInfo

import signals

ET = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")
# Names from the Nov 5 morning note, fixed before the replay. Not picked after seeing results.
BASKET = ["AAPL", "MSFT", "AMZN", "GOOGL", "INTC", "TSLA", "TXN", "BAC"]
UA = "Mozilla/5.0 (compatible; day-trader-bot/1.0)"


def fetch(sym):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=5d&interval=5m" % sym
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        doc = json.loads(r.read().decode("utf-8"))
    block = doc["chart"]["result"][0]
    quote = block["indicators"]["quote"][0]
    by_day = defaultdict(list)
    for i, ts in enumerate(block["timestamp"]):
        c, h, l = quote["close"][i], quote["high"][i], quote["low"][i]
        if c is None or h is None or l is None:
            continue
        start = datetime.datetime.fromtimestamp(ts, ET)
        by_day[start.date()].append({
            "start": start,
            "end": start + datetime.timedelta(minutes=5),
            "o": float(quote["open"][i] or c),
            "h": float(h), "l": float(l), "c": float(c),
            "v": float(quote["volume"][i] or 0),
        })
    return by_day


def replay():
    books = {}
    for sym in BASKET:
        books[sym] = fetch(sym)
        print("fetched", sym, len(books[sym]), "days")
    days = sorted(set().union(*[set(b) for b in books.values()]))
    closed = []
    for day in days:
        if day.weekday() >= 5:
            continue
        no_buys_after, flat_by = signals.session_bounds(day)
        positions = {}
        bought = set()
        buys = 0
        # Walk the session in 5-minute steps using AAPL's clock as the grid.
        grid = books["AAPL"].get(day, [])
        for bar in grid:
            asof = bar["end"]
            local = asof.astimezone(PT)
            if local.time() < datetime.time(6, 45) or local.time() > datetime.time(13, 5):
                continue
            for sym in BASKET:
                day_bars = books[sym].get(day, [])
                sig = signals.analyze(sym, day_bars, asof)
                if not sig:
                    continue
                pos = positions.get(sym)
                if pos:
                    reason = None
                    if sig["close"] <= pos["stop"]:
                        reason = "stop"
                    elif sig["close"] >= pos["target"]:
                        reason = "target"
                    elif signals.trend_failed(sig):
                        reason = "trend failed"
                    elif local.time() >= flat_by:
                        reason = "flat by close"
                    if reason:
                        risk = pos["entry"] - pos["stop"]
                        pnl = round((sig["close"] - pos["entry"]) * pos["shares"], 2)
                        r_mult = round((sig["close"] - pos["entry"]) / risk, 2) if risk else 0
                        closed.append({
                            "date": day.isoformat(), "symbol": sym,
                            "entry": pos["entry"], "exit": round(sig["close"], 2),
                            "pnl": pnl, "r": r_mult, "reason": reason, "win": pnl > 0,
                        })
                        del positions[sym]
                    continue
                if sym in bought or local.time() >= no_buys_after or buys >= signals.MAX_BUYS:
                    continue
                if not signals.buy_ok(sig):
                    continue
                entry = round(sig["close"], 2)
                stop = round(min(sig["or_low"], entry * 0.995), 2)
                if stop >= entry:
                    continue
                shares, _risk = signals.size(entry, stop)
                if shares < 1:
                    continue
                positions[sym] = {
                    "entry": entry, "stop": stop,
                    "target": round(entry + 2 * (entry - stop), 2),
                    "shares": shares,
                }
                bought.add(sym)
                buys += 1
        # Anything still open at the last bar is closed at that close.
        for sym, pos in list(positions.items()):
            last = books[sym].get(day, [])
            if not last:
                continue
            px = last[-1]["c"]
            risk = pos["entry"] - pos["stop"]
            pnl = round((px - pos["entry"]) * pos["shares"], 2)
            closed.append({
                "date": day.isoformat(), "symbol": sym,
                "entry": pos["entry"], "exit": round(px, 2),
                "pnl": pnl, "r": round((px - pos["entry"]) / risk, 2) if risk else 0,
                "reason": "session end", "win": pnl > 0,
            })
    return closed


def report(rows):
    wins = [r for r in rows if r["win"]]
    pnl = sum(r["pnl"] for r in rows)
    avg_r = sum(r["r"] for r in rows) / len(rows) if rows else 0
    lines = []
    lines.append("Replay of the live rules, %s." % ", ".join(BASKET))
    lines.append("Closed: %d. Wins: %d. Losses: %d. Success rate: %.0f%%." % (
        len(rows), len(wins), len(rows) - len(wins), (100.0 * len(wins) / len(rows)) if rows else 0))
    lines.append("Average R: %+.2f. Hypothetical P&L: %s." % (avg_r, signals.money(pnl)))
    lines.append("Fill is the 5-minute close in the alert. Delayed data, not a broker fill.")
    lines.append("")
    for r in rows:
        lines.append("%s %s  buy %s sell %s  %s  %+.2fR  %s" % (
            r["date"], r["symbol"], signals.money(r["entry"]), signals.money(r["exit"]),
            signals.money(r["pnl"]), r["r"], r["reason"]))
    text = "\n".join(lines)
    print(text)
    out = signals.ROOT / "report" / "replay.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n")
    return text


if __name__ == "__main__":
    report(replay())
