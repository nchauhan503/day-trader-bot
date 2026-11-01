#!/usr/bin/env python3
"""Intraday ORB monitor: watches the daily watchlist and emits a complete
trade ticket (entry, stop, target, size) the moment a setup triggers.

Paper trading by default. Never places an order; every signal requires a
human yes through trader-broker. Run with --once for a single pass (ideal for
cron/launchd) or --loop to keep polling.

Usage:
  python3 intraday.py --once                       # one pass, paper mode
  python3 intraday.py --loop --poll 60             # poll every 60s while open
  python3 intraday.py --once --commit              # also commit signals to git
  python3 intraday.py --once --live                # live mode (needs broker)
  python3 intraday.py --once --top 10 --symbols TSLA,NVDA
  python3 intraday.py --once --ntfy my-topic       # push to ntfy.sh/my-topic
  python3 intraday.py --once --imessage-to "+15551234567"
"""

import argparse
import datetime
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
ET = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")

DATA = ROOT / "trader"
ENTRY = ROOT / "trader-entry" / "entry.py"
EXIT = ROOT / "trader-exit" / "exit.py"
SIZE = ROOT / "trader-budget" / "size.py"
RISK = ROOT / "trader-risk" / "check.py"
SIGNALS = ROOT / "signals.json"
LIVE = ROOT / "report" / "live.txt"
STATE = DATA / "intraday-state.json"
PAPER = DATA / "paper-ledger.json"
KILL = DATA / "kill"
CONFIG = DATA / "config.json"

NYSE_HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}
NYSE_EARLY_CLOSE = {"2026-11-27", "2026-12-24", "2027-11-26"}

sys.path.insert(0, str(ROOT))
from monitor.notify import deliver


def sh(cmd, timeout=120):
    return subprocess.run([str(c) for c in cmd], check=False, text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)


def ny_now():
    return datetime.datetime.now(ET)


def market_status(now):
    if now.weekday() >= 5:
        return False, "weekend"
    if now.date().isoformat() in NYSE_HOLIDAYS:
        return False, "holiday"
    open_t = datetime.time(9, 30)
    close_t = datetime.time(13, 0) if now.date().isoformat() in NYSE_EARLY_CLOSE else datetime.time(16, 0)
    if now.time() < open_t:
        return False, "pre-market"
    if now.time() >= close_t:
        return False, "closed"
    return True, "open"


def load_state():
    today = ny_now().date().isoformat()
    if STATE.exists():
        try:
            s = json.loads(STATE.read_text())
            if s.get("date") == today:
                return s
        except Exception:
            pass
    return {"date": today, "trades": 0, "risk_used": 0.0, "seen": []}


def save_state(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2))


def load_config():
    if CONFIG.exists():
        try:
            return json.loads(CONFIG.read_text())
        except Exception:
            pass
    return {}


def ensure_config():
    DATA.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    defaults = {
        "budget": 5000, "cash_buffer_pct": 10, "max_position_pct": 75,
        "max_order_usd": 4000, "min_order_usd": 5,
        "max_loss_per_trade_usd": 50, "max_daily_loss_usd": 150,
        "max_trades_per_day": 3, "max_orders_per_day": 10,
        "min_reward_risk": 1.0, "require_stop": True,
        "no_entries_before": "09:45", "no_entries_after": "11:00",
        "flat_by": "15:55", "fractional": False, "broker": "alpaca",
        "paper": True, "blocked": [],
        "sec_user_agent": cfg.get("sec_user_agent", "Nitin Chauhan daarknight94@gmail.com"),
    }
    for k, v in defaults.items():
        cfg.setdefault(k, v)
    CONFIG.write_text(json.dumps(cfg, indent=2))
    return cfg


def watchlist_tickers(top, explicit):
    if explicit:
        return [s.strip().upper() for s in explicit.split(",") if s.strip()]
    wl_path = DATA / "watchlist.json"
    if not wl_path.exists():
        return []
    wl = json.loads(wl_path.read_text())
    hits = wl.get("hits", [])
    hits.sort(key=lambda h: (h.get("price", 0) or 0) * (h.get("volume", 0) or 0), reverse=True)
    if top:
        hits = hits[:top]
    return [h["symbol"] for h in hits]


def backup_entry():
    p = DATA / "entry.json"
    if p.exists():
        return p.read_text()
    return None


def restore_entry(backup):
    p = DATA / "entry.json"
    if backup is None:
        p.unlink(missing_ok=True)
    else:
        p.write_text(backup)


def entry_triggered(sym, interval, now=None):
    backup = backup_entry()
    cmd = [sys.executable, ENTRY, "check", sym, "--interval", interval, "--write"]
    if now:
        cmd += ["--now", now]
    r = sh(cmd)
    triggered = (r.returncode == 0)
    if not triggered:
        restore_entry(backup)
    return triggered, r.stdout


def build_ticket(sym, now=None):
    c1 = [sys.executable, EXIT, "plan", "--write"]
    if now:
        c1 += ["--now", now]
    r1 = sh(c1)
    if r1.returncode != 0:
        return False, "exit refused: %s" % r1.stdout.strip()
    c2 = [sys.executable, SIZE, "--write"]
    if now:
        c2 += ["--now", now]
    r2 = sh(c2)
    if r2.returncode != 0:
        return False, "sizing refused: %s" % r2.stdout.strip()
    return True, r2.stdout.strip()


def risk_check(now):
    r = sh([sys.executable, RISK, "--json",
            "--now", now.strftime("%Y-%m-%d %H:%M")])
    out = r.stdout.strip()
    verdict = "BLOCK"
    reasons = []
    try:
        doc = json.loads(out)
        results = doc.get("results", doc.get("orders", []))
        if results:
            verdict = results[0].get("verdict", "BLOCK")
            rr = results[0].get("reasons", results[0].get("reason", []))
            reasons = [rr] if isinstance(rr, str) else (rr or [])
    except Exception:
        if "PASS" in out.upper():
            verdict = "PASS"
    return verdict, reasons


def risk_ok(state, max_trades, max_daily_risk):
    if state["trades"] >= max_trades:
        return False, "max trades/day reached (%d)" % max_trades
    if state["risk_used"] >= max_daily_risk:
        return False, "daily risk budget exhausted ($%.0f)" % state["risk_used"]
    return True, ""


def signal_text(sym, plan, order, reasons, mode):
    lines = []
    lines.append("[%s] SIGNAL %s  %s" % (mode, sym, datetime.datetime.now(ET).strftime("%H:%M ET")))
    lines.append("Entry $%s  Stop $%s  Target $%s" % (plan.get("entry"), plan.get("stop"), plan.get("target")))
    lines.append("Risk $%s/share  R:R %s" % (plan.get("risk_per_share", plan.get("risk")), plan.get("reward_risk")))
    if order:
        val = order.get("value_usd")
        if val is None:
            try:
                val = round(float(order.get("qty", 0)) * float(plan.get("entry", 0)), 2)
            except Exception:
                val = "?"
        lines.append("Size: %s shares  (~$%s)" % (order.get("qty"), val))
    lines.append("Setup: ORB breakout (1m close above range high)")
    if reasons:
        lines.append("Notes: %s" % "; ".join(reasons))
    lines.append("Paper trade. Say yes to place.")
    return "\n".join(lines)


def record_signal(sym, text, plan, order, verdict):
    doc = {}
    if SIGNALS.exists():
        try:
            doc = json.loads(SIGNALS.read_text())
        except Exception:
            doc = {}
    signals = doc.get("signals", [])
    signals.insert(0, {
        "time": datetime.datetime.now(ET).isoformat(),
        "symbol": sym,
        "verdict": verdict,
        "plan": plan,
        "order": order,
        "text": text,
    })
    signals = signals[:50]
    SIGNALS.write_text(json.dumps({"updated": datetime.datetime.now(ET).isoformat(),
                                  "signals": signals}, indent=2))
    ledger = []
    if PAPER.exists():
        try:
            ledger = json.loads(PAPER.read_text())
        except Exception:
            ledger = []
    ledger.insert(0, {"time": datetime.datetime.now(ET).isoformat(), "symbol": sym,
                      "verdict": verdict, "plan": plan, "order": order})
    ledger = ledger[:500]
    PAPER.write_text(json.dumps(ledger, indent=2))


def update_live(sym, text, status):
    lines = []
    if LIVE.exists():
        lines = LIVE.read_text().splitlines()
    out = [l for l in lines if not (l.startswith("  [") and sym in l)]
    # also drop the detail line that followed a previous entry for this symbol
    out.append("  [%s] %s" % (status, sym))
    out.append("    %s" % text.replace("\n", " | "))
    LIVE.parent.mkdir(parents=True, exist_ok=True)
    LIVE.write_text("\n".join(out) + "\n")


def commit_if_changed():
    try:
        subprocess.run(["git", "add", "signals.json", "report/live.txt",
                        "trader/intraday-state.json"], check=False, cwd=ROOT)
        r = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
        if r.returncode != 0:
            subprocess.run(["git", "commit", "-m", "intraday: %s" % ny_now().strftime("%Y-%m-%d %H:%M ET")],
                           check=False, cwd=ROOT)
            subprocess.run(["git", "push"], check=False, cwd=ROOT)
    except Exception as e:
        print("commit failed: %s" % e, file=sys.stderr)


def _now(args):
    if getattr(args, "now", None):
        naive = datetime.datetime.strptime(args.now, "%Y-%m-%d %H:%M")
        return naive.replace(tzinfo=ET)
    return ny_now()

def one_pass(args, cfg, state):
    now = _now(args)
    if KILL.exists():
        print("Kill switch present (%s). Exiting." % KILL)
        return 0
    ok, why = market_status(now)
    if not ok:
        print("Market %s (%s). Nothing to do." % (why, now.strftime("%H:%M ET")))
        return 0
    max_trades = cfg.get("max_trades_per_day", 3)
    max_daily_risk = cfg.get("max_daily_loss_usd", 150)
    cutoff = cfg.get("no_entries_after", "11:00")
    ch, cm = [int(x) for x in cutoff.split(":")]
    if now.time() >= datetime.time(ch, cm):
        print("Past entry cutoff %s ET. Nothing to do." % cutoff)
        return 0

    can, reason = risk_ok(state, max_trades, max_daily_risk)
    if not can:
        print(reason)
        return 0

    tickers = watchlist_tickers(args.top, args.symbols)
    if not tickers:
        print("No watchlist. Run the daily scan first (run_scan.py).")
        return 0
    print("Scanning %d tickers at %s ET" % (len(tickers), now.strftime("%H:%M")))

    imessage_to = args.imessage_to or cfg.get("imessage_to")
    ntfy_topic = args.ntfy or cfg.get("ntfy_topic")
    if ntfy_topic and not cfg.get("ntfy_topic"):
        cfg["ntfy_topic"] = ntfy_topic
        CONFIG.write_text(json.dumps(cfg, indent=2))

    signaled = 0
    for sym in tickers:
        if sym in state["seen"]:
            continue
        can, reason = risk_ok(state, max_trades, max_daily_risk)
        if not can:
            print("Risk budget exhausted; stopping.")
            break
        triggered, out = entry_triggered(sym, args.interval, args.now)
        if not triggered:
            for line in out.splitlines():
                if any(k in line for k in ("WAITING", "EXPIRED", "NO DATA")):
                    print("  %s: %s" % (sym, line.strip()[:70]))
                    break
            else:
                print("  %s: no trigger" % sym)
            continue
        print("  %s: TRIGGERED" % sym)
        ok2, msg = build_ticket(sym, args.now)
        if not ok2:
            print("    ticket refused: %s" % msg)
            state["seen"].append(sym)
            save_state(state)
            continue
        plan = json.loads((DATA / "plan.json").read_text()) if (DATA / "plan.json").exists() else {}
        order_doc = json.loads((DATA / "orders.json").read_text()) if (DATA / "orders.json").exists() else {}
        orders = order_doc.get("orders", order_doc) if isinstance(order_doc, dict) else order_doc
        order = orders[0] if isinstance(orders, list) and orders else (orders if isinstance(orders, dict) else {})
        verdict, reasons = risk_check(now)
        mode = "LIVE" if args.live else "PAPER"
        text = signal_text(sym, plan, order, reasons if verdict != "PASS" else [], mode)
        print("\n" + text + "\n")
        record_signal(sym, text, plan, order, verdict)
        update_live(sym, text, verdict)
        state["seen"].append(sym)
        if verdict == "PASS":
            state["trades"] += 1
            risk = plan.get("risk")
            try:
                state["risk_used"] += float(risk) * float(order.get("qty", 0) or 0)
            except Exception:
                state["risk_used"] += cfg.get("max_loss_per_trade_usd", 50)
            signaled += 1
        save_state(state)
        im_ok, nt_ok = deliver(text, cfg.get("imessage_to") or args.imessage_to,
                               cfg.get("ntfy_topic") or args.ntfy)
        print("    notified: imessage=%s ntfy=%s" % (im_ok, nt_ok))

    print("\nPass complete: %d new signal(s)." % signaled)
    if args.commit:
        commit_if_changed()
    return 0


def risk_ok(state, max_trades, max_daily_risk):
    if state["trades"] >= max_trades:
        return False, "max trades/day reached (%d)" % max_trades
    if state["risk_used"] >= max_daily_risk:
        return False, "daily risk budget exhausted ($%.0f)" % state["risk_used"]
    return True, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single pass (default)")
    ap.add_argument("--loop", action="store_true", help="poll until market close")
    ap.add_argument("--poll", type=int, default=60, help="seconds between passes in --loop")
    ap.add_argument("--top", type=int, default=20, help="top N watchlist names by dollar volume")
    ap.add_argument("--symbols", help="comma-separated tickers (overrides watchlist)")
    ap.add_argument("--interval", default="1m", choices=["1m", "5m"])
    ap.add_argument("--imessage-to", help="iMessage handle (phone/email) for Mac delivery")
    ap.add_argument("--ntfy", help="ntfy.sh topic for phone push")
    ap.add_argument("--commit", action="store_true", help="commit signals to git")
    ap.add_argument("--live", action="store_true", help="live mode (default paper)")
    ap.add_argument("--max-trades", type=int)
    ap.add_argument("--max-daily-risk", type=float)
    ap.add_argument("--now", help="pretend it is this New York time, e.g. \"2026-10-30 10:15\" (testing/replay)")
    args = ap.parse_args()

    cfg = ensure_config()
    if args.max_trades is not None:
        cfg["max_trades_per_day"] = args.max_trades
    if args.max_daily_risk is not None:
        cfg["max_daily_loss_usd"] = args.max_daily_risk
    if args.imessage_to:
        cfg["imessage_to"] = args.imessage_to
    if args.ntfy:
        cfg["ntfy_topic"] = args.ntfy
    if not cfg.get("ntfy_topic"):
        cfg["ntfy_topic"] = "daytrader-" + secrets.token_hex(6)
        print("Generated ntfy topic (subscribe in the ntfy app): %s" % cfg["ntfy_topic"])
    if not cfg.get("imessage_to"):
        print("NOTE: no imessage_to set; iMessage delivery disabled. "
              "Set it with --imessage-to or in trader/config.json.")
    if args.live:
        cfg["paper"] = False
    CONFIG.write_text(json.dumps(cfg, indent=2))

    state = load_state()
    save_state(state)

    if args.loop:
        print("Loop mode: polling every %ds until market close. Ctrl-C to stop." % args.poll)
        while True:
            ok, why = market_status(now)
            if not ok:
                print("%s: %s" % (ny_now().strftime("%H:%M ET"), why))
                if why in ("closed", "weekend", "holiday"):
                    print("Market closed. Exiting loop.")
                    break
            else:
                one_pass(args, cfg, state)
                state = load_state()
            time.sleep(args.poll)
    else:
        one_pass(args, cfg, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
