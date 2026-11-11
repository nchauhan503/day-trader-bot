#!/usr/bin/env python3
"""One self-contained HTML page built from the ledger: the equity curve, the
win/loss count, the R-multiple distribution, alert price vs next-bar fill,
and the trade log.

signals.write_scorecard() calls render(rows) after every buy and sell, so the
page is always current. Run `python3 dashboard.py` to rebuild it by hand.

No dependencies, no JavaScript, no network: every chart is inline SVG, and
the page is committed to report/ by the same publish step as the scorecard.

render() must never raise -- write_scorecard() catches, but a chart bug
should not get the chance to disturb an alert. The output path follows
signals.SCORECARD, so replay_check.py (which redirects the ledger, the
scorecard and the state to a temp folder) cannot overwrite the real page
with replayed trades.
"""

import datetime
import json
import math
from pathlib import Path

import signals

ROOT = Path(__file__).resolve().parent

# Yardstick, not a promise: the held-out third of the backtest, same rules,
# same basket, next-bar fills. See lab.py and the commit that pinned it.
BACKTEST = {
    "n": 50, "pnl": 573.0, "winpct": 54.0, "avgR": 0.35,
    "window": "2026-10-08 to 2026-11-05",
}

UP, DN, ACC, MUT = "#3fb950", "#f85149", "#58a6ff", "#9198a1"


# ---------------------------------------------------------------- helpers

def _esc(x):
    return (str(x).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _usd(v, signed=False):
    s = "${:,.2f}".format(abs(v))
    if v < 0:
        return "-" + s
    return ("+" + s) if signed else s


def _pnl(r):
    v = r.get("pnl")
    return float(v) if isinstance(v, (int, float)) else 0.0


def _pnl_alert(r):
    v = r.get("pnl_alert")
    return float(v) if isinstance(v, (int, float)) else _pnl(r)


def _r_of(r):
    v = r.get("r")
    return float(v) if isinstance(v, (int, float)) else None


def _cls(v, signed=False):
    if v > 0:
        return "up"
    if v < 0:
        return "dn"
    return "mut" if not signed else "mut"


def _stamp():
    try:
        return signals.now_et().strftime("%Y-%m-%d %H:%M ET")
    except Exception:
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def _out_path():
    """Where to write. Follows the scorecard, which replay_check redirects
    into a temp folder -- so a replay never touches the published page."""
    try:
        return Path(signals.SCORECARD).parent / "dashboard.html"
    except Exception:
        return ROOT / "report" / "dashboard.html"


def _ticks(lo, hi, want=4):
    """Round numbers between lo and hi, for the y axis."""
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / max(want, 1)
    mag = 10.0 ** math.floor(math.log10(raw))
    step = None
    for m in (1, 2, 2.5, 5, 10):
        if raw / mag <= m + 1e-9:
            step = m * mag
            break
    if step is None:
        step = 10 * mag
    out = []
    v = math.ceil(lo / step) * step
    while v <= hi + 1e-9:
        out.append(round(v, 6))
        v += step
    return out


# ----------------------------------------------------------------- charts

def _equity(closed, w=940, h=300):
    """Cumulative P&L trade by trade: honest fills solid, alert price dashed."""
    pl, pr, pt, pb = 68, 18, 22, 36
    ys_f, ys_a = [0.0], [0.0]
    for r in closed:
        ys_f.append(ys_f[-1] + _pnl(r))
        ys_a.append(ys_a[-1] + _pnl_alert(r))
    n = len(closed)
    if not n:
        return ""

    lo = min(ys_f + ys_a + [0.0])
    hi = max(ys_f + ys_a + [0.0])
    pad = (hi - lo) * 0.12 or 1.0
    lo, hi = lo - pad, hi + pad
    span = hi - lo or 1.0
    plot_w = w - pl - pr
    plot_h = h - pt - pb

    def X(i):
        return pl + (i / n) * plot_w

    def Y(v):
        return pt + (hi - v) / span * plot_h

    s = ['<svg viewBox="0 0 %d %d" role="img" aria-label="equity curve">' % (w, h)]
    for t in _ticks(lo, hi, 4):
        y = Y(t)
        zero = abs(t) < 1e-9
        s.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" '
                 'stroke-width="%s"/>' % (pl, y, w - pr, y,
                                          "#30363d" if not zero else "#484f58",
                                          1 if not zero else 1.4))
        s.append('<text x="%d" y="%.1f" fill="%s" font-size="11" '
                 'text-anchor="end" dy="4">%s</text>'
                 % (pl - 8, y, MUT, _usd(t)))

    # area under the honest curve, anchored on the zero line
    area = " ".join("M %.1f %.1f" % (X(i), Y(v)) for i, v in enumerate(ys_f))
    area += " L %.1f %.1f L %.1f %.1f Z" % (X(n), Y(0), X(0), Y(0))
    s.append('<path d="%s" fill="%s" fill-opacity="0.10"/>' % (area, ACC))

    for vals, colour, dash, width in (
            (ys_a, MUT, "5 4", 1.8),
            (ys_f, ACC, None, 2.6)):
        d = " ".join(("M" if i == 0 else "L") + " %.1f %.1f"
                     % (X(i), Y(v)) for i, v in enumerate(vals))
        extra = ' stroke-dasharray="%s"' % dash if dash else ""
        s.append('<path d="%s" fill="none" stroke="%s" stroke-width="%.1f"%s '
                 'stroke-linejoin="round"/>' % (d, colour, width, extra))

    if n <= 40:
        for i, v in enumerate(ys_f):
            s.append('<circle cx="%.1f" cy="%.1f" r="3" fill="%s"/>'
                     % (X(i), Y(v), ACC))

    first = str(closed[0].get("date") or "")[5:]
    last = str(closed[-1].get("date") or "")[5:]
    s.append('<text x="%d" y="%d" fill="%s" font-size="11">%s</text>'
             % (pl, h - 12, MUT, _esc(first)))
    s.append('<text x="%d" y="%d" fill="%s" font-size="11" '
             'text-anchor="end">%s</text>' % (w - pr, h - 12, MUT, _esc(last)))
    s.append('<text x="%d" y="%d" fill="%s" font-size="11" '
             'text-anchor="middle">trade %d of %d</text>'
             % (pl + plot_w / 2, h - 12, MUT, n, n))

    # legend, top right
    lx = w - pr - 168
    s.append('<line x1="%d" y1="14" x2="%d" y2="14" stroke="%s" '
             'stroke-width="2.6"/>' % (lx, lx + 18, ACC))
    s.append('<text x="%d" y="18" fill="%s" font-size="11">next-bar fill</text>'
             % (lx + 24, MUT))
    s.append('<line x1="%d" y1="14" x2="%d" y2="14" stroke="%s" '
             'stroke-width="1.8" stroke-dasharray="5 4"/>'
             % (lx + 118, lx + 136, MUT))
    s.append('<text x="%d" y="18" fill="%s" font-size="11">alert</text>'
             % (lx + 142, MUT))
    s.append("</svg>")
    return "".join(s)


def _rbars(closed, w=940, h=250):
    """Histogram of R multiples. Red below zero, green at or above it."""
    rs = [_r_of(r) for r in closed]
    rs = [v for v in rs if v is not None]
    if not rs:
        return '<p class="mut">No R multiples recorded yet.</p>'

    pl, pr, pt, pb = 44, 18, 26, 40
    step = 0.5
    for _ in range(6):
        start = math.floor(min(min(rs), 0.0) / step) * step
        end = math.ceil(max(max(rs), 0.0) / step) * step
        if end <= start:
            end = start + step
        nb = int(round((end - start) / step))
        if nb <= 16:
            break
        step *= 2
    counts = [0] * nb
    for v in rs:
        i = int(math.floor((v - start) / step))
        counts[max(0, min(nb - 1, i))] += 1

    plot_w = w - pl - pr
    plot_h = h - pt - pb
    bw = plot_w / nb
    top = max(counts) or 1

    s = ['<svg viewBox="0 0 %d %d" role="img" aria-label="R multiple '
         'distribution">' % (w, h)]
    for i, c in enumerate(counts):
        if not c:
            continue
        bh = c / top * plot_h
        x = pl + i * bw + 3
        y = pt + plot_h - bh
        right = start + (i + 1) * step
        colour = DN if right <= 0 else UP
        s.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="2" '
                 'fill="%s"/>' % (x, y, bw - 6, bh, colour))
        s.append('<text x="%.1f" y="%.1f" fill="%s" font-size="11" '
                 'text-anchor="middle">%d</text>'
                 % (x + (bw - 6) / 2, y - 6, MUT, c))

    # x axis: bin edges, and a line at zero
    for i in range(nb + 1):
        v = start + i * step
        x = pl + i * bw
        anchor = "middle"
        s.append('<text x="%.1f" y="%d" fill="%s" font-size="10.5" '
                 'text-anchor="%s">%s</text>'
                 % (x, pt + plot_h + 16, MUT, anchor, _edge(v)))
    if start < 0 < end:
        x0 = pl + (0 - start) / step * bw
        s.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#484f58" '
                 'stroke-width="1.4" stroke-dasharray="4 4"/>'
                 % (x0, pt - 6, x0, pt + plot_h))
        s.append('<text x="%.1f" y="%d" fill="%s" font-size="10.5" '
                 'text-anchor="middle">0</text>' % (x0, pt - 10, MUT))
    s.append('<text x="%d" y="%d" fill="%s" font-size="11">%d trade%s, '
             'step %sR</text>' % (pl, h - 8, MUT, len(rs),
                                  "" if len(rs) == 1 else "s", _edge(step)))
    s.append("</svg>")
    return "".join(s)


def _edge(v):
    out = ("%.4f" % v).rstrip("0").rstrip(".")
    return out if out not in ("", "-0") else "0"


# ------------------------------------------------------------------ parts

def _header(ts, n_open):
    return (
        '<h1>Day trader &mdash; paper record</h1>'
        '<div class="sub">Fixed basket of 20 liquid names &middot; rules '
        'picked on 60 sessions by lab.py &middot; generated %s</div>'
        '<div class="grid">%s</div>' % (ts, n_open)
    )


def _cards(closed, opened):
    n = len(closed)
    wins = [r for r in closed if _pnl(r) > 0]
    losses = [r for r in closed if _pnl(r) <= 0]
    rate = (100.0 * len(wins) / n) if n else 0.0
    pnl = sum(_pnl(r) for r in closed)
    alert = sum(_pnl_alert(r) for r in closed)
    cost = alert - pnl
    rs = [_r_of(r) for r in closed]
    rs = [v for v in rs if v is not None]
    avg_r = (sum(rs) / len(rs)) if rs else 0.0

    def card(k, v, s, cls=""):
        return ('<div class="card"><div class="k">%s</div>'
                '<div class="v %s">%s</div><div class="s">%s</div></div>'
                % (k, cls, v, s))

    parts = []
    parts.append(card("Closed trades", "%d" % n,
                      "%d win / %d loss" % (len(wins), len(losses))
                      if n else "waiting for the first signal"))
    parts.append(card("Win rate",
                      "%.1f%%" % rate if n else "&mdash;",
                      "backtest held out: %.1f%%" % BACKTEST["winpct"]
                      if n else "backtest held out: %.1f%%" % BACKTEST["winpct"],
                      _cls(rate - 50) if n else "mut"))
    parts.append(card("P&amp;L at fills", _usd(pnl, True) if n else "&mdash;",
                      "alert price: %s" % _usd(alert, True) if n else
                      "entry: next bar open + slippage",
                      _cls(pnl) if n else "mut"))
    parts.append(card("Slippage cost", _usd(-cost, True) if n else "&mdash;",
                      "what the alert price leaves out"
                      if n else "%.2f/share each side" % signals.SLIP,
                      _cls(-cost) if n else "mut"))
    parts.append(card("Average R", ("%+.2f" % avg_r) if rs else "&mdash;",
                      "backtest held out: %+.2fR" % BACKTEST["avgR"],
                      _cls(avg_r) if rs else "mut"))
    parts.append(card("Still open", "%d" % len(opened),
                      "a position waiting for its exit"
                      if opened else "flat"))
    return "".join(parts)


def _fills(closed):
    head = ("<tr><th>Date</th><th>Symbol</th><th class='r'>Sh</th>"
            "<th class='r'>Entry alert</th><th class='r'>Entry fill</th>"
            "<th class='r'>Exit alert</th><th class='r'>Exit fill</th>"
            "<th class='r'>P&amp;L at alert</th><th class='r'>P&amp;L at fill</th>"
            "<th class='r'>Cost</th></tr>")
    body = []
    tot_a = tot_f = 0.0
    for r in closed:
        ea, ef = r.get("entry"), r.get("entry_fill")
        xa, xf = r.get("exit"), r.get("exit_fill")
        pa, pf = _pnl_alert(r), _pnl(r)
        tot_a += pa
        tot_f += pf
        body.append(
            "<tr><td>%s</td><td>%s</td><td class='r num'>%s</td>"
            "<td class='r num'>%s</td><td class='r num'>%s</td>"
            "<td class='r num'>%s</td><td class='r num'>%s</td>"
            "<td class='r num %s'>%s</td><td class='r num %s'>%s</td>"
            "<td class='r num %s'>%s</td></tr>"
            % (_esc(r.get("date")), _esc(r.get("symbol")), r.get("shares"),
               _usd(ea) if ea is not None else "&mdash;",
               _usd(ef) if ef is not None else "&mdash;",
               _usd(xa) if xa is not None else "&mdash;",
               _usd(xf) if xf is not None else "&mdash;",
               _cls(pa, True), _usd(pa, True),
               _cls(pf, True), _usd(pf, True),
               _cls(pa - pf, True), _usd(pa - pf, True)))
    total = ("<tr style='font-weight:650'><td colspan='7'>Total, %d trade%s</td>"
             "<td class='r num %s'>%s</td><td class='r num %s'>%s</td>"
             "<td class='r num %s'>%s</td></tr>"
             % (len(closed), "" if len(closed) == 1 else "s",
                _cls(tot_a, True), _usd(tot_a, True),
                _cls(tot_f, True), _usd(tot_f, True),
                _cls(tot_a - tot_f, True), _usd(tot_a - tot_f, True)))
    note = ('<p class="sub" style="margin:10px 0 0">Alert prices are what the '
            'phone showed. Fills price the entry at the open of the bar after '
            'the signal and the exit at the alert price, each with %s a share '
            'of slippage -- the same model the backtest uses. <b>Cost</b> is '
            'the difference: what the optimistic number leaves out.</p>'
            % _usd(signals.SLIP))
    return ('<div class="scroll"><table>%s%s%s</table></div>%s'
            % (head, "".join(body), total, note))


def _log(rows):
    head = ("<tr><th>Date</th><th>Symbol</th><th class='r'>Shares</th>"
            "<th class='r'>Entry</th><th class='r'>Exit</th>"
            "<th class='r'>P&amp;L</th><th class='r'>R</th><th>Why</th>"
            "<th>Status</th></tr>")
    body = []
    for r in rows:
        open_row = r.get("exit") is None
        pnl = _pnl(r)
        rv = _r_of(r)
        ea, ef = r.get("entry"), r.get("entry_fill")
        xa, xf = r.get("exit"), r.get("exit_fill")
        entry = ("%s<div class='mut num' style='font-size:11px'>fill %s</div>"
                 % (_usd(ea), _usd(ef))) if ef is not None else _usd(ea)
        exitc = ("%s<div class='mut num' style='font-size:11px'>fill %s</div>"
                 % (_usd(xa), _usd(xf))) if xf is not None else (
                     "<span class='badge'>open</span>" if open_row
                     else _usd(xa))
        if open_row:
            pnlc, rc = "&mdash;", "&mdash;"
        else:
            pnlc = '<span class="%s num">%s</span>' % (_cls(pnl, True),
                                                       _usd(pnl, True))
            rc = ('<span class="%s num">%+.2f</span>' % (_cls(rv), rv)
                  if rv is not None else "&mdash;")
        body.append(
            "<tr><td>%s</td><td>%s</td><td class='r num'>%s</td>"
            "<td class='r num'>%s</td><td class='r num'>%s</td>"
            "<td class='r'>%s</td><td class='r'>%s</td><td>%s</td>"
            "<td>%s</td></tr>"
            % (_esc(r.get("date")), _esc(r.get("symbol")), r.get("shares"),
               entry, exitc, pnlc, rc, _esc(r.get("reason") or ""),
               '<span class="badge">open</span>' if open_row else "closed"))
    return '<div class="scroll"><table>%s%s</table></div>' % (head,
                                                             "".join(body))


def _empty(rows, opened):
    if not rows:
        return (
            '<div class="panel">'
            '<div style="font-size:17px;font-weight:650">No trades yet</div>'
            '<p class="sub" style="margin:9px 0 0">Nothing has triggered. '
            'Over the 60 sessions the rules were measured on, this basket '
            'produced 135 trades -- about 2 a day -- and 3 of those sessions '
            'had none at all. A quiet morning is normal; a full week with '
            'nothing would be worth investigating.</p>'
            '<p class="sub" style="margin:7px 0 0">This page updates the '
            'moment a buy or a sell is recorded.</p></div>')
    if not opened:
        return ""
    return (
        '<div class="panel"><div style="font-size:15px;font-weight:650">'
        '%d position%s still open</div>'
        '<p class="sub" style="margin:7px 0 0">Nothing has closed yet, so '
        'there is no equity curve to draw. The trade log below shows what '
        'is held.</p></div>'
        % (len(opened), "" if len(opened) == 1 else "s"))


def _footer(ts):
    return (
        "<footer><ul style='margin:0;padding-left:18px'>"
        "<li><b>Paper only.</b> Nothing here is an order. Every signal "
        "needs your yes.</li>"
        "<li>Fills are modelled, not broker fills: entry at the next bar's "
        "open, exit at the alert price, %s a share of slippage on each "
        "side -- identical to the backtest, so the two can be compared "
        "honestly.</li>"
        "<li>Yardstick: the held-out backtest (%s) took %d trades for %s "
        "at %.1f%% and %+.2fR. It is one past window, not a forecast.</li>"
        "<li>Source: report/ledger.json &middot; generated %s</li>"
        "</ul></footer>"
        % (_usd(signals.SLIP), BACKTEST["window"], BACKTEST["n"],
           _usd(BACKTEST["pnl"], True), BACKTEST["winpct"], BACKTEST["avgR"],
           ts))


HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Day trader &mdash; paper record</title>
<style>
:root{--bg:#0d1117;--panel:#161b22;--line:#2d333b;--fg:#e6edf3;--mut:#9198a1;
--up:#3fb950;--dn:#f85149;--acc:#58a6ff;--warn:#d29922}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:22px 18px 64px}
.banner{display:flex;gap:9px;align-items:center;background:#2a2000;
border:1px solid #6b4e00;color:#f0c040;border-radius:8px;padding:9px 14px;
font-weight:650;font-size:13px;letter-spacing:.02em;margin-bottom:20px}
h1{font-size:21px;margin:0 0 5px}
h2{font-size:12.5px;text-transform:uppercase;letter-spacing:.08em;
color:var(--mut);margin:32px 0 11px;padding-bottom:7px;
border-bottom:1px solid var(--line)}
.sub{color:var(--mut);font-size:12.5px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));
gap:11px;margin-top:17px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:9px;
padding:12px 14px}
.k{font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;color:var(--mut)}
.v{font-size:23px;font-weight:650;margin-top:3px;font-variant-numeric:tabular-nums}
.s{font-size:11.5px;color:var(--mut);margin-top:4px}
.up{color:var(--up)}.dn{color:var(--dn)}.mut{color:var(--mut)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:9px;
padding:15px 17px}
svg{display:block;width:100%;height:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th{font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);
text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);
white-space:nowrap}
td{padding:7px 9px;border-bottom:1px solid #21262d;white-space:nowrap}
tr:last-child td{border-bottom:none}
.r{text-align:right}
.num{font-variant-numeric:tabular-nums}
.scroll{overflow-x:auto}
.badge{display:inline-block;font-size:10.5px;padding:1px 8px;border-radius:20px;
border:1px solid var(--warn);color:var(--warn)}
footer{margin-top:36px;padding-top:16px;border-top:1px solid var(--line);
color:var(--mut);font-size:12.5px}
footer li{margin:6px 0}
</style></head><body><div class="wrap">
<div class="banner">&#9679; PAPER ONLY &mdash; no order is ever placed. Every
signal needs your yes.</div>
"""

TAIL = "</div></body></html>"


# ------------------------------------------------------------------ entry

def _page(rows, closed, opened):
    ts = _stamp()
    parts = [HEAD, _header(ts, ""), _cards(closed, opened)]
    if closed:
        parts.append('<h2>Equity curve</h2><div class="panel">%s</div>'
                     % _equity(closed))
        parts.append('<h2>R-multiple distribution</h2><div class="panel">%s</div>'
                     % _rbars(closed))
        parts.append('<h2>Alert price vs next-bar fill</h2>%s' % _fills(closed))
    if rows:
        parts.append('<h2>Trade log</h2>%s' % _log(rows))
    empty = _empty(rows, opened)
    if empty:
        parts.append('<h2>Status</h2>%s' % empty)
    parts.append(_footer(ts))
    parts.append(TAIL)
    return "".join(parts)


def render(rows):
    """Write report/dashboard.html from the ledger. Returns the path.

    Never raises: a broken chart must not be able to disturb an alert.
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    closed = [r for r in rows if r.get("exit") is not None]
    closed.sort(key=lambda r: (str(r.get("date") or ""),
                               str(r.get("bought_at") or "")))
    opened = [r for r in rows if r.get("exit") is None]
    out = _out_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_page(rows, closed, opened))
    return out


def main():
    try:
        path = Path(signals.LEDGER)
        rows = json.loads(path.read_text()) if path.exists() else []
        if not isinstance(rows, list):
            rows = []
        out = render(rows)
        closed = [r for r in rows if isinstance(r, dict)
                  and r.get("exit") is not None]
        print("wrote %s (%d trade%s, %d closed)"
              % (out, len(rows), "" if len(rows) == 1 else "s", len(closed)))
    except Exception as exc:
        print("dashboard failed: %s" % exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
