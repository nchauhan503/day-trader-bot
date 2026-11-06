#!/usr/bin/env python3
"""One pass for the single cron job.

Weekday behavior, America/Los_Angeles:
  - at or after 5:00 AM, if today's report is not in the repo yet: run the
    pre-market scan and push that report to ntfy
  - then one intraday pass (no-ops when the market is closed or the entry
    window is over; a real signal is pushed to ntfy by intraday.py)

Nothing is bought. Paper mode.
"""

import datetime
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
PT = ZoneInfo("America/Los_Angeles")
REPORT = ROOT / "report" / "latest.txt"
HISTORY = ROOT / "report" / "history"
# trader/ is gitignored, so a later GitHub run cannot see files left only there.
# These copies live under report/ and are committed.
SAVED_WATCHLIST = ROOT / "report" / "watchlist.json"
SAVED_STATE = ROOT / "report" / "intraday-state.json"
LOCAL_WATCHLIST = ROOT / "trader" / "watchlist.json"
LOCAL_STATE = ROOT / "trader" / "intraday-state.json"


def sh(cmd, timeout=900):
    print("+ " + " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=ROOT)
    return r.returncode


def push_report(topic):
    if not topic or not REPORT.exists():
        print("morning report not pushed (no topic or no report)")
        return
    body = REPORT.read_text().encode("utf-8")
    req = urllib.request.Request(
        "https://ntfy.sh/" + topic, data=body, method="POST")
    req.add_header("Title", "Pre-market scan")
    req.add_header("Tags", "chart_with_upwards_trend")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            print("morning report pushed to ntfy (%s)" % r.status)
    except Exception as exc:
        print("ntfy push failed: %s" % exc, file=sys.stderr)


def publish_local(src, dest):
    if src.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        print("saved %s" % dest.relative_to(ROOT))


def restore_local(src, dest):
    if dest.exists() or not src.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    print("restored %s" % dest.relative_to(ROOT))


def main():
    now = datetime.datetime.now(PT)
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    print("live pass %s Pacific" % now.strftime("%Y-%m-%d %H:%M %Z"))

    weekday = now.weekday() < 5
    history = HISTORY / (now.strftime("%Y-%m-%d") + ".txt")
    if weekday and now.hour >= 5 and not history.exists():
        rc = sh([sys.executable, str(ROOT / "run_scan.py")])
        if rc != 0:
            print("morning scan failed (%s)" % rc, file=sys.stderr)
        elif history.exists() or REPORT.exists():
            push_report(topic)
        publish_local(LOCAL_WATCHLIST, SAVED_WATCHLIST)
    else:
        print("morning scan skipped")

    # Later runs start from a clean checkout. Put the saved watchlist and the
    # "already signaled" state back before the intraday pass, then save the
    # state again so the same ticker is not texted every 5 minutes.
    restore_local(SAVED_WATCHLIST, LOCAL_WATCHLIST)
    restore_local(SAVED_STATE, LOCAL_STATE)
    sh([sys.executable, str(ROOT / "intraday.py"), "--once"])
    publish_local(LOCAL_WATCHLIST, SAVED_WATCHLIST)
    publish_local(LOCAL_STATE, SAVED_STATE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
