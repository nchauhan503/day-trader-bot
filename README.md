# day-trader-bot

Runs the pre-market US stock scan every day at **5:00 AM Pacific** on GitHub
Actions (so it works with your laptop off), writes a short report, and gives
your iPhone a URL that a Shortcut turns into an iMessage.

Built on [Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill)
(MIT, vendored into `trader-scanner/` and `trader-news/`). Free data: Yahoo
Finance charts, Nasdaq Trader symbol lists, SEC EDGAR. No API keys.

## What it does each morning

1. Downloads Nasdaq Trader's symbol directories (~5,900 US common stocks/ADRs).
2. Scans daily closes for: 20-day closing-high breakouts, bull flags, bullish
   reclaims of the 20-day average, oversold (RSI < 30), oversold bounces.
3. Groups hits by setup, most-traded first, and adds the latest headline for
   the top names.
4. Writes `report/latest.txt` (and a dated copy in `report/history/`) and
   commits it.

Example output:

```
Pre-market scan - Mon Oct 26, 5:00 AM PT
Closes through 2026-10-23 | 1453 scanned | 448 setups

Breakouts (216): XOM $183.21, CVX $233.21, NOW $158.96
Oversold (119): GEV $815.00, LRCX $230.10, AMAT $377.24
Bull flags (81): WMT $119.89, V $407.92, MA $623.60
...
Free delayed data. Setups, not advice.
```

## Setup (one time)

### 1. Push this folder to a public GitHub repo

```sh
cd ~/Documents/day-trader-bot
git remote add origin https://github.com/<your-user>/day-trader-bot.git
git push -u origin main
```

The repo must be **public**: the iPhone Shortcut fetches
`report/latest.txt` with no credentials. The report contains only public market
data - no personal information is committed.

### 2. Optional: include SEC filings

Repo → Settings → Secrets and variables → Actions → **Secrets** → New repository
secret: `SEC_USER_AGENT` = `Your Name you@example.com`. Without it, headlines
come from Yahoo only. (A secret, not a variable - variables are publicly
readable in a public repo.)

### 3. Test it

Actions tab → **premarket-scan** → Run workflow. It takes ~4 minutes and
commits the first report.

### 4. iPhone Shortcut

Shortcuts app → **Automation** tab → **+**:

1. **Time of Day** → 5:00 AM, Daily, *Run Immediately* (turn off
   "Ask Before Running").
2. Add action **Get Contents of URL**
   - URL: `https://raw.githubusercontent.com/<your-user>/day-trader-bot/main/report/latest.txt`
   - Method: GET
3. Add action **Send Message**
   - Recipients: yourself (your own contact card / number)
   - Message: the `Contents of URL` variable
4. (Optional) add **Show Notification** with the same variable.

The automation runs on the phone itself, so the iMessage is sent even when the
Mac is asleep, closed, or off.

## Run it locally

```sh
python3 run_scan.py --force          # ignore the 5am gate
python3 run_scan.py --force --no-news --limit 300   # quick test
python3 run_scan.py                  # only proceeds at 5am Pacific
```

Local runs need `trader/config.json` for SEC:

```json
{ "sec_user_agent": "Your Name you@example.com" }
```

`trader/` is gitignored; the workflow builds it from the repo variable.

## Notes and limits

- **Timing**: cron fires at 12:00 and 13:00 UTC; the script exits unless the
  Pacific hour really is 5, which covers daylight saving without edits.
- **Data**: free, delayed, unofficial. Setups are candidates to look at, not
  buy signals, and nothing here places an order.
- **Throttling**: if a runner gets rate-limited by Yahoo, the report says
  `WARNING: thin data (...)`. Re-run the workflow or wait a day.
- GitHub Actions free tier covers 2,000 minutes/month; this uses ~150.
