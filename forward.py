#!/usr/bin/env python3
"""Forward test: log what the lab_fund rule would have traded, once per day.

Runs after the close from live.py. Sends nothing. For each trigger that day
(the lab_fund rule, 15-minute delay, 2-hour hold) it writes one row to
report/forward_ledger.json, flagged with the SPY-uptrend input. The decision
rule is fixed in report/forward_rule.txt, written before the first row.

    python3 forward.py YYYY-MM-DD     # log one session
    python3 forward.py summary        # score the logged rows
"""

import datetime
import json
import sys
from pathlib import Path

import lab
import lab_fund as lf
import strategies

ROOT = Path(__file__).resolve().parent
LEDGER = ROOT / "report" / "forward_ledger.json"
RULE = ROOT / "report" / "forward_rule.txt"
SESSIONS = ROOT / "report" / "forward_sessions.json"
START = "2026-11-18"          # first session after the data the lab read
S1_START = "2026-11-30"       # first session after S1 was registered
MIN_SESSIONS = 20


def load():
    if not LEDGER.exists():
        return []
    try:
        doc = json.loads(LEDGER.read_text())
        return doc if isinstance(doc, list) else []
    except Exception:
        return []


def save(rows):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(rows, indent=2) + "\n")


def logged_days(rows):
    return {r["date"] for r in rows}


def log_day(day):
    """Log one session. Idempotent: a day already in the ledger is skipped."""
    if day < START:
        print("forward: %s is before the forward window (%s), not logged" % (day, START))
        return 0
    if not RULE.exists():
        RULE.parent.mkdir(parents=True, exist_ok=True)
        RULE.write_text(RULE_TEXT.format(n=MIN_SESSIONS))
    done = load_sessions()
    if day in done:
        print("forward: %s already logged" % day)
        return 0
    rows = load()
    data = {}
    for sym in lf_universe():
        try:
            full = lab.fetch(sym)
            if day in full:
                data[sym] = {day: full[day]}
        except Exception as exc:
            print("  %s: no bars (%s)" % (sym, exc))
    if not data:
        print("forward: no bars for %s, nothing logged" % day)
        return 0
    try:
        lab.daily_closes("SPY")
    except Exception as exc:
        print("forward: SPY daily unavailable (%s), not logged" % exc)
        return 0
    market = lab_market(day)
    new = []
    for strategy, trades in _strategy_trades(data, day):
        for t in trades:
            new.append({
                "strategy": strategy, "date": day, "symbol": t["sym"],
                "fill": t["fill"], "exit": t["exit"],
                "r": round(t["r"], 4), "pct": round(t["pct"], 6),
                "why": t["why"], "spy_up": market,
            })
    rows.extend(new)
    save(rows)
    done.append(day)
    save_sessions(done)
    print("forward: %s logged %d trades (SPY uptrend: %s)" % (day, len(new), market))
    return len(new)


def _strategy_trades(data, day):
    """Each registered strategy's triggers for one session.

    VWAP is the live baseline and its rows are tagged "VWAP". S1 starts on
    S1_START, the session after it was registered; earlier sessions are not
    back-logged for it.
    """
    out = [("VWAP", lf.build(data, lf.LAG_BARS))]
    if day >= S1_START:
        out.append(("S1", strategies.run(strategies.s1_orb_volume, data)))
    return out


def load_sessions():
    if not SESSIONS.exists():
        return []
    try:
        return json.loads(SESSIONS.read_text())
    except Exception:
        return []


def save_sessions(days):
    SESSIONS.parent.mkdir(parents=True, exist_ok=True)
    SESSIONS.write_text(json.dumps(sorted(set(days)), indent=2) + "\n")


def lf_universe():
    import fund_pit as fp
    return fp.UNIVERSE


def lab_market(day):
    t = lab.trend_of("SPY", day)
    return t is not None and t > 0


def _strat(r):
    return r.get("strategy", "VWAP")


def summary():
    all_rows = load()
    rows = [r for r in all_rows if r["date"] >= START and _strat(r) == "VWAP"]
    days = sorted({r["date"] for r in rows})
    print("forward sessions logged: %d (%s)" % (len(days), ", ".join(days) or "none"))
    s1 = [r for r in all_rows if _strat(r) == "S1" and r["date"] >= S1_START]
    s1_days = sorted({r["date"] for r in s1})
    print("S1 forward sessions logged: %d (%s)" % (len(s1_days), ", ".join(s1_days) or "none"))
    print("S1 (orb_volume):")
    _print_stats("all S1 triggers", s1)
    print("VWAP (live baseline):")
    for label, sel in (("all triggers", rows),
                       ("SPY uptrend (the candidate)", [r for r in rows if r["spy_up"]])):
        _print_stats(label, sel)
    return 0


def _print_stats(label, sel):
    rs = [r["r"] for r in sel]
    n = len(rs)
    if n == 0:
        print("  %-28s n=0" % label)
        return
    mean = sum(rs) / n
    sd = (sum((x - mean) ** 2 for x in rs) / (n - 1)) ** 0.5 if n > 1 else 0
    t = mean / sd * n ** 0.5 if sd else 0
    win = 100.0 * sum(1 for x in rs if x > 0) / n
    print("  %-28s n=%-4d meanR %+.3f  t %+.2f  win %.0f%%" % (label, n, mean, t, win))


RULE_TEXT = """Forward test rule, written 2026-11-17 before the first forward row.

Candidate: SPY in a 5-day uptrend on the signal day, the lab_fund buy rule,
entry 15 minutes after the signal bar closes, 1 ATR stop, 1R target,
2-hour time stop. Only sessions from 2026-11-18 onward count.

Decision, read after {n} logged sessions with the SPY-uptrend flag:
  PASS only if ALL of these hold on the SPY-uptrend trades:
    1. mean R > 0 over all forward sessions
    2. t >= 2 over all forward sessions
    3. mean R > 0 in the first half of the forward sessions
    4. mean R > 0 in the second half of the forward sessions
  Otherwise REJECT. A reject means the training result was chance.

No parameter may change during the forward test. Any change restarts it.
"""


def main(argv):
    if len(argv) > 1 and argv[1] == "summary":
        return summary()
    day = argv[1] if len(argv) > 1 else datetime.date.today().isoformat()
    log_day(day)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
