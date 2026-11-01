"""Delivery helpers: iMessage (Mac) + ntfy.sh (phone)."""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request


def imessage(text, to):
    """Send via Messages.app on macOS. Returns True on success."""
    if not to:
        return False
    # osascript is only available on macOS.
    if sys.platform != "darwin":
        return False
    safe = text.replace("\\", "\\\\").replace('"', '\\"')
    script = ('tell application "Messages"\n'
              '  send "%s" to buddy "%s"\n'
              'end tell' % (safe, to))
    try:
        subprocess.run(["osascript", "-e", script], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20)
        return True
    except Exception as e:
        print("imessage failed: %s" % e, file=sys.stderr)
        return False


def ntfy(topic, title, body, priority="default"):
    """Push to ntfy.sh. No auth needed for public topics."""
    if not topic:
        return False
    url = "https://ntfy.sh/%s" % topic
    data = body.encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Title", title)
    req.add_header("Priority", priority)
    req.add_header("Tags", "chart_with_upwards_trend")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status in (200, 201, 202)
    except Exception as e:
        print("ntfy failed: %s" % e, file=sys.stderr)
        return False


def deliver(text, imessage_to, ntfy_topic):
    """Try both channels; return (imessage_ok, ntfy_ok)."""
    im = imessage(text, imessage_to)
    nt = ntfy(ntfy_topic, "Trade signal", text) if ntfy_topic else False
    return im, nt
