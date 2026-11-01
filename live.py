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
import subprocess
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
PT = ZoneInfo("America/Los_Angeles")
REPORT = ROOT / "report" / "latest.txt"
HISTORY = ROOT / "report" / "history"


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
    else:
        print("morning scan skipped")

    # Intraday no-ops outside the session. It pushes its own ntfy on a signal.
    # Do not --commit here; the workflow publishes once.
    sh([sys.executable, str(ROOT / "intraday.py"), "--once"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
