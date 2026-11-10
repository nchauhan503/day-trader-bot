#!/usr/bin/env python3
"""Score the alerts the system gave on given days, with honest fills.

Fetches fresh 5-minute bars, replays the exact live rules over each session,
then prices every trade at the next bar's open plus slippage -- the same fill
model the backtest used -- instead of the alert price the ledger would show.

Usage:
  python3 score_days.py                       # 2026-11-06 2026-11-09
  python3 score_days.py 2026-11-06
"""

import datetime
import json
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

import lab
import signals

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent
TMP = ROOT / "data" / "fresh"
UA = "Mozilla/5.0 (compatible; day-trader-bot/1.0)"
SLIP = 0.01

# The watchlist the scan actually published on these days.
SYMS = ["AMD", "GOOG", "GOOGL", "INTC", "MU", "NVDA", "SNDK", "TSM"]
DAYS = ["2026-11-06", "2026-11-09"]


def fetch(sym):
    path = TMP / (sym + ".json")
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
    TMP.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(days))
    return days


def recs_for(day):
    out = []
    for sym in SYMS:
        doc = fetch(sym)
        if day not in doc:
            print("  %s: no bars for %s" % (sym, day))
            continue
        rec = lab.prep(sym, day, doc[day])
        if not rec:
            continue
        dsorted = sorted(doc)
        closes = {d: doc[d][-1][4] for d in dsorted}
        idx = dsorted.index(day)
        rec["trend"] = (closes[dsorted[idx - 1]] / closes[dsorted[idx - 6]] - 1
                        if idx >= 6 else None)
        out.append(rec)
    return out


def main():
    global SLIP
    days = sys.argv[1:] or DAYS
    # The picked rule, written from the lab's own knobs.
    lab.GAP_ATR = 0.5

    def stop_1atr(ctx, entry):
        a = ctx["atr"] or entry * 0.004
        return min(entry - 1.0 * a, entry * (1 - 0.001))

    lab.EXITS["atr_trail"]["stop"] = stop_1atr

    total, rows = 0.0, []
    for day in days:
        recs = recs_for(day)
        if not recs:
            continue
        trades = lab.run_days(recs, "cur", "atr_trail", datetime.time(11, 0),
                              True, SLIP, {day})
        print("\n=== %s (%d names) ===" % (day, len(recs)))
        if not trades:
            print("  no signal: the rules wanted to buy nothing")
        for t in sorted(trades, key=lambda x: x["exit_ts"]):
            total += t["pnl"]
            rows.append(t)
            print("  %-5s buy %8.2f  sell %8.2f  %2d sh  $%+7.2f  %+.2fR  (%s)"
                  % (t["sym"], t["entry"], t["exit"], t["shares"],
                     t["pnl"], t["r"], t["reason"]))

    print("\n" + "=" * 74)
    if not rows:
        print("TOTAL: $0.00 -- no alerts were given, so nothing to act on.")
        return 0
    wins = sum(1 for t in rows if t["pnl"] > 0)
    worst = min(t["pnl"] for t in rows)
    print("TOTAL: $%+.2f over %d trades  (wins %d/%d, worst $%+.2f)"
          % (total, len(rows), wins, len(rows), worst))
    print("Fill model: next bar's open + $%.2f/share slippage, stop gaps fill" % SLIP)
    print("at the gap. The ledger would score the alert price instead, which")
    print("is always kinder.")
    over = sum(1 for t in rows if t["pnl"] < -50)
    print("Trades over the $50 budget: %d" % over)
    return 0


if __name__ == "__main__":
    sys.exit(main())
