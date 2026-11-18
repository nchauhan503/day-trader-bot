#!/usr/bin/env python3
"""Point-in-time fundamentals from SEC XBRL company facts.

Every reported value carries the date it was filed. as_of() answers "what did
an investor know on this date", using only facts filed on or before it. That
is what makes a fundamental backtest honest: a later restatement must not leak
into an earlier signal.

    python3 fund_pit.py fetch      # download facts for the universe (cached)
    python3 fund_pit.py coverage   # which names have usable quarterly data
"""

import datetime
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "data" / "fund"
CONFIG = ROOT / "trader" / "config.json"

# S&P 100, from memory. Verify against the current index before relying on
# it: membership changes over time, so this is today's list applied backwards.
UNIVERSE = """
AAPL ABBV ABT ACN ADBE AIG AMD AMGN AMT AMZN AVGO AXP BA BAC BK BKNG BLK BMY
BRK-B C CAT CHTR CMCSA COF COP COST CRM CSCO CVS CVX DE DHR DIS DUK EMR F FDX
GD GE GILD GM GOOGL GS HD HON IBM INTC INTU ISRG JNJ JPM KHC KO LIN LLY LMT
LOW MA MCD MDLZ MDT MET META MMM MO MRK MS MSFT MU NEE NFLX NKE NOW NVDA ORCL
PEP PFE PG PLTR PM PYPL QCOM RTX SBUX SCHW SO SPG T TGT TMO TMUS TSLA TXN UBER
UNH UNP UPS USB V VZ WFC WMT XOM
""".split()

# Concepts we read. The first one a company reports wins for each metric.
CONCEPTS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "SalesRevenueNet"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
    "eps_diluted": ["EarningsPerShareDiluted"],
}

# Only quarterly and annual reports. A 10-Q covers ~3 months, a 10-K ~12.
FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A"}


def _cfg_ua():
    try:
        return json.loads(CONFIG.read_text())["sec_user_agent"]
    except Exception:
        return "day-trader-bot research (set sec_user_agent in trader/config.json)"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": _cfg_ua()})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def cik_map():
    t = _get("https://www.sec.gov/files/company_tickers.json")
    return {v["ticker"].upper(): v["cik_str"] for v in t.values()}


def fetch_all(names=None, pause=0.2):
    CACHE.mkdir(parents=True, exist_ok=True)
    ciks = cik_map()
    out = {}
    for tk in (names or UNIVERSE):
        sec_name = tk.replace("-", ".")  # BRK-B is listed as BRK.B in SEC data
        cik = ciks.get(sec_name) or ciks.get(tk)
        if cik is None:
            out[tk] = "no CIK"
            continue
        path = CACHE / ("%s.json" % tk)
        try:
            doc = _get("https://data.sec.gov/api/xbrl/companyfacts/CIK%010d.json" % cik)
        except Exception as exc:
            out[tk] = "fetch failed: %s" % exc
            continue
        path.write_text(json.dumps(_extract(doc)))
        out[tk] = "ok"
        time.sleep(pause)
    return out


def _extract(doc):
    """Keep only the facts a point-in-time read needs."""
    gaap = (doc.get("facts") or {}).get("us-gaap") or {}
    keep = {}
    for metric, names in CONCEPTS.items():
        for concept in names:
            node = gaap.get(concept)
            if not node:
                continue
            units = node.get("units") or {}
            unit = "USD" if "USD" in units else ("USD/shares" if "USD/shares" in units else None)
            if unit is None:
                continue
            rows = []
            for f in units[unit]:
                if f.get("form") not in FORMS or not f.get("filed"):
                    continue
                rows.append({"start": f.get("start"), "end": f.get("end"),
                             "val": f.get("val"), "filed": f["filed"],
                             "form": f["form"], "fp": f.get("fp")})
            if rows:
                keep[metric] = rows
                break
    return keep


def load(tk):
    path = CACHE / ("%s.json" % tk)
    return json.loads(path.read_text()) if path.exists() else {}


def _days(a, b):
    return (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days


def quarters_as_of(data, metric, asof):
    """Quarter-length values for `metric` that were on file on `asof`.

    A quarter is a period of roughly three months. If the same period was
    filed more than once, the newest filing on or before `asof` wins, which is
    exactly what an investor could see that day.
    """
    best = {}
    for f in data.get(metric, []):
        if f["filed"] > asof or not f["start"] or not f["end"]:
            continue
        if not (75 <= _days(f["start"], f["end"]) <= 100):
            continue
        key = f["end"]
        if key not in best or f["filed"] > best[key]["filed"]:
            best[key] = f
    return [best[k] for k in sorted(best)]


def yoy_growth(data, metric, asof):
    """Latest quarter vs the same quarter a year earlier, both as of `asof`."""
    qs = quarters_as_of(data, metric, asof)
    if len(qs) < 5 or qs[-1]["val"] in (None, 0):
        return None
    latest = qs[-1]
    end = datetime.date.fromisoformat(latest["end"])
    prior = [q for q in qs[:-1]
             if abs(_days(q["end"], str(end - datetime.timedelta(days=365)))) <= 10]
    # A growth rate off a zero or negative base is meaningless (a loss to a
    # bigger loss reads as "growth"), so it is treated as unknown.
    if not prior or prior[-1]["val"] is None or prior[-1]["val"] <= 0:
        return None
    return latest["val"] / prior[-1]["val"] - 1.0


def margin_now(data, asof):
    """Operating margin of the latest quarter, as of `asof`."""
    op = quarters_as_of(data, "operating_income", asof)
    rev = quarters_as_of(data, "revenue", asof)
    if not op or not rev:
        return None
    rv = {q["end"]: q["val"] for q in rev}
    if op[-1]["end"] not in rv or not rv[op[-1]["end"]]:
        return None
    return op[-1]["val"] / rv[op[-1]["end"]]


def coverage():
    rows = []
    for tk in UNIVERSE:
        d = load(tk)
        rows.append((tk, sorted(d), len(d.get("revenue", []))))
    return rows


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "coverage"
    if cmd == "fetch":
        res = fetch_all()
        bad = {k: v for k, v in res.items() if v != "ok"}
        print("fetched %d, problems: %s" % (len(res) - len(bad), bad or "none"))
    elif cmd == "coverage":
        for tk, metrics, nrev in coverage():
            print("%-6s %-60s revenue facts: %d" % (tk, ",".join(metrics) or "-", nrev))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
