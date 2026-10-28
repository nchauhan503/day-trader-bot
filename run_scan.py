#!/usr/bin/env python3
"""Pre-market scan -> short plain-text report for iMessage.

Runs the vendored trader-scanner over the full US universe, groups the hits by
setup, optionally pulls recent headlines for the top names, and writes
report/latest.txt (plus a dated copy in report/history/).

Designed to run on a GitHub Actions runner at 05:00 America/Los_Angeles. The
runner's clock is UTC, so the workflow fires at both 12:00 and 13:00 UTC and
this script decides which one is really 5am Pacific.

No third-party dependencies. Python 3.8+ (zoneinfo on 3.9+, tzdata on Linux).
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SCAN = ROOT / "trader-scanner" / "scan.py"
NEWS = ROOT / "trader-news" / "news.py"
REPORT_DIR = ROOT / "report"
DATA_DIR = ROOT / "trader"
PT = ZoneInfo("America/Los_Angeles")

SETUPS = [
    ("breakout_20d", "Breakouts"),
    ("oversold", "Oversold"),
    ("bull_flag", "Bull flags"),
    ("reclaim_ma20", "Reclaimed 20d"),
    ("oversold_bounce", "Bounce"),
]
MAX_PER_SETUP = 3
MAX_NEWS = 3
MAX_MSG = 1400


def sh(cmd, **kw):
    """Run a command, echo it, return stdout (raises on failure)."""
    printable = " ".join(str(c) for c in cmd)
    print("+ " + printable, flush=True)
    return subprocess.run(
        [str(c) for c in cmd], check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw
    ).stdout


def already_ran_today(now):
    """True if a report for this Pacific date already exists."""
    return (REPORT_DIR / "history" / (now.strftime("%Y-%m-%d") + ".txt")).exists()


def universe_is_stale(path, hours=20):
    if not path.exists():
        return True
    age = datetime.now().timestamp() - path.stat().st_mtime
    return age > hours * 3600


def fmt_money(px):
    return "${:,.2f}".format(px) if px >= 1 else "${:.4f}".format(px)


def group_hits(hits):
    groups = {}
    for h in hits:
        for name in h.get("setups") or []:
            groups.setdefault(name, []).append(h)
    for rows in groups.values():
        rows.sort(key=lambda h: h.get("price", 0) * h.get("volume", 0), reverse=True)
    return groups


def headlines(tickers, config):
    """Recent Yahoo/SEC headlines per ticker. Best effort: never fatal."""
    if not tickers:
        return {}
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [sys.executable, NEWS, "--tickers", *tickers, "--hours", "72",
               "--all", "--json", "--state", os.path.join(tmp, "state.json")]
        if config.exists():
            cmd += ["--config", str(config)]
        try:
            out = subprocess.run([str(c) for c in cmd], check=True, text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
            data = json.loads(out)
        except Exception as exc:  # network, rate limit, whatever
            print("news skipped: %s" % exc, file=sys.stderr)
            return {}
    by_sym = {}
    for item in data.get("items", []):
        sym = item.get("ticker")
        title = " ".join((item.get("title") or "").split())
        if sym and title and sym not in by_sym:
            by_sym[sym] = title[:90] + ("..." if len(title) > 90 else "")
    picked = {}
    for sym in tickers:
        if sym in by_sym:
            picked[sym] = by_sym[sym]
        if len(picked) >= MAX_NEWS:
            break
    return picked


def build_report(now, meta, hits, news):
    groups = group_hits(hits)
    lines = []
    as_of = (meta.get("as_of") or meta.get("close_date") or "").replace(" close", "")
    stamp = "5:00 AM PT" if now.hour == 5 else now.strftime("%-I:%M %p PT (manual)")
    lines.append("Pre-market scan - %s, %s" % (now.strftime("%a %b %-d"), stamp))
    scanned = meta.get("scanned") or 0
    lines.append("Closes through %s | %s scanned | %s setups" % (
        as_of or "latest", scanned, len(hits)))
    if scanned and scanned < 900:
        lines.append("WARNING: thin data (%s charts) - source may be throttling this host." % scanned)
    lines.append("")

    ordered = [(k, label) for k, label in SETUPS if groups.get(k)]
    ordered += [(k, k) for k in groups if k not in dict(SETUPS)]
    for key, label in ordered:
        rows = groups[key][:MAX_PER_SETUP]
        names = ", ".join("%s %s" % (h["symbol"], fmt_money(h["price"])) for h in rows)
        lines.append("%s (%d): %s" % (label, len(groups[key]), names))

    if news:
        lines.append("")
        lines.append("News:")
        for sym, title in news.items():
            lines.append("- %s: %s" % (sym, title))

    lines.append("")
    lines.append("Free delayed data. Setups, not advice.")
    text = "\n".join(lines)
    if len(text) > MAX_MSG:
        text = text[: MAX_MSG - 40].rsplit("\n", 1)[0] + "\n... (full report in repo)"
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="run now even if a report already exists today")
    ap.add_argument("--limit", type=int, default=0,
                    help="scan only the first N universe symbols (testing)")
    ap.add_argument("--no-news", action="store_true")
    ap.add_argument("--print", action="store_true", help="print report to stdout")
    args = ap.parse_args()

    now = datetime.now(PT)
    # GitHub's scheduler is best-effort and can fire hours late, so instead of
    # demanding the exact 5am hour we take the first run of the Pacific day and
    # ignore the rest. A punctual external trigger calls with --force.
    if not args.force:
        if now.hour < 4:
            print("Too early (%s Pacific). Nothing to do." % now.strftime("%H:%M"))
            return 0
        if already_ran_today(now):
            print("Already ran today (%s Pacific). Nothing to do." % now.strftime("%H:%M"))
            return 0
    print("Run at %s Pacific" % now.strftime("%Y-%m-%d %H:%M"))

    DATA_DIR.mkdir(exist_ok=True)
    universe = DATA_DIR / "universe.txt"
    if universe_is_stale(universe):
        sh([sys.executable, SCAN, "universe"])

    scan_cmd = [sys.executable, SCAN, "daily", "--quiet"]
    if args.limit:
        scan_cmd += ["--limit", args.limit]
    sh(scan_cmd)

    watchlist = json.loads((DATA_DIR / "watchlist.json").read_text())
    hits = watchlist.get("hits", [])
    meta = {k: watchlist.get(k) for k in ("mode", "as_of", "scanned", "failed", "universe")}
    print("hits: %d" % len(hits))

    news = {}
    if not args.no_news and hits:
        top = group_hits(hits)
        names = []
        for key, _ in SETUPS:
            for h in top.get(key, [])[:2]:
                if h["symbol"] not in names:
                    names.append(h["symbol"])
        news = headlines(names[:5], DATA_DIR / "config.json")

    report = build_report(now, meta, hits, news)

    REPORT_DIR.mkdir(exist_ok=True)
    (REPORT_DIR / "latest.txt").write_text(report + "\n")
    hist = REPORT_DIR / "history"
    hist.mkdir(exist_ok=True)
    stamp = now.strftime("%Y-%m-%d")
    (hist / (stamp + ".txt")).write_text(report + "\n")

    # keep the history directory from growing forever
    old = sorted(hist.glob("*.txt"))[:-90]
    for f in old:
        f.unlink()

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        Path(summary).write_text("```\n%s\n```\n" % report)

    if args.print:
        print()
        print(report)
    else:
        print()
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
