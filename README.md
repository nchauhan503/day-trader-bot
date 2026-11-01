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

## Punctual trigger (needed for a real 5:00 AM text)

GitHub's `schedule` is best-effort; on this repo it has fired 5-6 hours late,
which turns "5am" into "maybe 11am". The workflow above is only a backstop. To
get a punctual 5:00 AM run, trigger the job from outside GitHub.

**Option A - cron-job.org (no phone involved)**

1. Create a fine-grained PAT: github.com/settings/personal-access-tokens/new
   - Repository access: only `day-trader-bot`
   - Permissions: **Actions: Read and write**
2. Create a free job at cron-job.org, schedule 5:00 daily, timezone
   `America/Los_Angeles`, request:
   - Method: `POST`
   - URL: `https://api.github.com/repos/nchauhan503/day-trader-bot/actions/workflows/scan.yml/dispatches`
   - Headers:
     `Authorization: Bearer <PAT>`
     `Accept: application/vnd.github+json`
     `Content-Type: application/json`
   - Body: `{"ref":"main","inputs":{"force":"true"}}`

**Option B - the iPhone Shortcut does the triggering**

Add a second automation at 5:00 that sends the same POST above (PAT in the
`Authorization` header), and move the existing fetch-and-text automation to
5:06 so the scan has time to finish. Keeps the token on the phone, no
third-party account, but depends on the phone being awake.

Either way, `force: true` is correct: the external trigger fires once per day.

## Intraday monitor (paper trading)

`intraday.py` watches the daily watchlist bar-by-bar during market hours and
emits a complete trade ticket (entry, stop, target, size, R:R) the moment an
opening-range breakout triggers. Paper mode by default - it never places an
order; every signal still needs your yes through `trader-broker`.

```sh
python3 intraday.py --once                       # one pass (market must be open)
python3 intraday.py --loop --poll 60             # poll every 60s until the close
python3 intraday.py --once --now "2026-10-30 10:15" --symbols GEV   # replay
python3 intraday.py --once --commit              # also commit signals to git
```

Risk layer (enforced before any ticket is emitted):
- max 3 trades/day, $150 daily risk, $50 max loss per trade
- no new entries after 11:00 AM ET
- no duplicate signal for the same ticker on the same day
- kill switch: create `trader/kill` and every pass becomes a no-op

Delivery: iMessage (Mac, via osascript) when the Mac is awake, plus a push to
ntfy.sh for the phone. The ntfy topic is auto-generated on first run and saved
to `trader/config.json`; subscribe to it in the ntfy app.

### Intraday cron (cloud, every 5 min during market hours)

The `intraday-monitor` workflow runs `intraday.py --once --commit`. Trigger it
from cron-job.org every 5 minutes, 6:30 AM-1:00 PM PT (9:30 AM-4:00 PM ET),
timezone `America/Los_Angeles`:

- Method `POST`
- URL `https://api.github.com/repos/nchauhan503/day-trader-bot/actions/workflows/intraday.yml/dispatches`
- Headers: `Authorization: Bearer <PAT>`, `Accept: application/vnd.github+json`,
  `Content-Type: application/json`
- Body `{"ref":"main","inputs":{"force":"true"}}`

The script itself checks market hours and exits early outside them, so a fixed
5-minute cadence is safe.

### Mac daemon (iMessage, market hours only)

```sh
python3 intraday.py --loop --poll 60
```
Run it in a terminal during the session, or via a launchd job at 6:25 AM PT.
iMessage only fires when the Mac is awake; ntfy covers the gaps.

### Subscribe to intraday pushes (ntfy)

1. Install the free **ntfy** app on your iPhone.
2. Subscribe to the topic printed on first run (saved in `trader/config.json`
   as `ntfy_topic`, currently `daytrader-f9d008c360d4`).
3. Test: `curl -d "hello" ntfy.sh/daytrader-f9d008c360d4` - a push should
   arrive within a second.


## Notes and limits

- **Timing**: GitHub's cron is best-effort and has been observed firing 5-6
  hours late on this repo. The workflow therefore runs as a backstop only;
  `run_scan.py` takes the first run of the Pacific day and skips the rest, so a
  late run still delivers a report (late rather than never). For a punctual
  5:00 AM trigger, point an external scheduler at the `workflow_dispatch` API
  (see "Punctual trigger" below).
- **Data**: free, delayed, unofficial. Setups are candidates to look at, not
  buy signals, and nothing here places an order.
- **Throttling**: if a runner gets rate-limited by Yahoo, the report says
  `WARNING: thin data (...)`. Re-run the workflow or wait a day.
- GitHub Actions free tier covers 2,000 minutes/month; this uses ~150.
