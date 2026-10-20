# Trade1 Trading AI

A research-first algorithmic trading platform for an **Exness MetaTrader 5 demo account**:
historical data → backtesting → out-of-sample validation → (later) demo-only automation.

> **Status: phases B–E are built**: data, backtesting and validation, the dashboard, and demo-only
> execution. Demo trading stays locked until a strategy PASSES validation on your real data and the
> terminal account is verified DEMO. **There is no live trading in this release.**
> **Live trading does not exist in this release**, and no code path can send a broker order.
> See [docs/PLAN.md](docs/PLAN.md) for the phase plan.

## What works now

| Area | Status |
|---|---|
| MT5 connection (read-only gateway, serialised calls, UTC conversion) | ✅ |
| Demo-account verification from the terminal's own `trade_mode` (fails closed) | ✅ |
| Market discovery: every symbol your account exposes, categorised, with full contract specs and metadata-validation flags | ✅ |
| Historical bars for M1 / M15 / H1 / H4: chunked download, quality checks, forming bar dropped, idempotent storage, per-run audit record | ✅ |
| Backtest engine: bar-based, spread/commission/slippage/swap, gaps, conservative intrabar rule, volume and margin checks, risk lockouts | ✅ |
| Multi-timeframe alignment without look-ahead (tested) | ✅ |
| Strategies (hypotheses to test, not proven): `trend_continuation`, `range_breakout`, `liquidity_sweep`, `trend_pullback`, `session_open_breakout` (London / New York, DST-aware), `asian_range_breakout` | ✅ |
| Honest-results tools: Monte Carlo, cost stress test, tick-data check of exits, multi-market test | ✅ |
| Portfolio backtest with combined limits and a currency-correlation limit | ✅ |
| Volatility-based sizing and news blackout in backtests (optional) | ✅ |
| Trade journal export (CSV / JSON), audit log and settings view, backups, Windows auto-start | ✅ |
| Market-regime filter (trending / ranging / high volatility) and per-regime results | ✅ |
| Validation: development / validation / untouched out-of-sample split, walk-forward, parameter sensitivity, sample-size rules, PASSED / FAILED / INCONCLUSIVE | ✅ |
| Metrics, stored runs and trades, reproducible strategy versions | ✅ |
| Diagnostics, optional Forex Factory news blackout | ✅ |
| Dashboard (Next.js): overview, markets + price charts, data quality, strategies, backtests with equity/drawdown/trade journal, validation reports, news, system health, demo gate | ✅ |
| Demo-only execution: gated enable, per-order safety checks, idempotent orders, reconciliation, emergency stop | ✅ (tested with a simulated MT5; first real run is yours) |
| Forward-test runner, daily report vs validation, Telegram alerts, VPS guide | ✅ |

## Getting started on your PC (about 5 minutes)

Trade1 does **not** go inside MetaTrader. It is a separate Python program that runs next to the
MT5 terminal and reads data from it through MetaQuotes' official `MetaTrader5` package. There is
nothing to add to MT5 itself. It cannot place trades: there is no live mode, and it checks that the
account is a demo before doing anything.

1. **Put the folder outside OneDrive** (recommended), e.g. `C:\Trade1`. Setup creates thousands of small
   files, and OneDrive would try to sync all of them. (To get the code: on GitHub choose the
   `claude/admiring-pasteur-eqo6ed` branch → **Code → Download ZIP**, and unzip it.)
2. Double-click **`scripts\setup_windows.bat`**. If it asks to install Python 3.12, type **y** and press
   Enter. Wait until it shows **SETUP COMPLETE**. It creates `backend\.venv`, installs dependencies,
   writes `backend\.env` (local SQLite database, random API token), creates the tables and runs the tests.
3. Keep **MT5 open and logged in to your Exness demo** (a server such as `Exness-MT5Trial10`).
4. Double-click **`scripts\run_checks.bat`**. When asked for a symbol, type the exact Market Watch name,
   e.g. **`XAUUSDm`** (gold). It checks the demo account, lists your markets and downloads history. It
   places no orders. The output is saved to `trade1_check_report.txt`.
5. Double-click **`scripts\run_backtest.bat`**. It downloads history for a symbol and timeframe, then
   backtests **and validates** all three strategies. The output is saved to `trade1_backtest_<date>.txt`.

**Set the demo balance to what you would really trade with.** On a $10 balance, almost every trade is
rejected, because the broker's minimum lot (0.01) already risks more than 0.25–1% of $10. Trade1
rejects those trades rather than over-risking. In MT5 you can open a new demo account with a realistic
balance.

6. Double-click **`scripts\start_trade1.bat`** to open the **dashboard** at http://127.0.0.1:3000. It starts
   the backend and dashboard in two windows (close them to stop). From the dashboard you can download
   history, run backtests and validate them without the command line.

Share the two report files with Claude to review the results. Backtest results are historical,
in-sample simulations (validation comes next). They are not a forecast.

SQLite is enough for getting started. Switch `TRADE1_DATABASE_URL` to PostgreSQL (below) for large
M1 histories.

## Requirements

* Windows 10/11 with the **MetaTrader 5 terminal from Exness**, logged in to your demo account.
* Python 3.12 or 3.13, 64-bit. The `MetaTrader5` package must install on your Python version.
  If `pip install MetaTrader5` fails, use Python 3.12.
* PostgreSQL 16 (or 15+), optional: SQLite is used by default.
* Node.js 22 LTS for the dashboard. `setup_windows.bat` offers to install it.

## Setup (Windows, PowerShell)

```powershell
cd backend
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env          # edit: database URL, API token
# create the database once (psql or pgAdmin):  CREATE DATABASE trade1;
alembic upgrade head
pytest                          # must pass before you continue
```

`requirements.txt` pins the versions tested here. If installing `MetaTrader5` changes your
numpy version, run `pytest` again and report any failures.

## Step 1 — Confirm the terminal is on the Exness demo account

1. In MT5, open **File → Login to Trade Account**. The server name should contain **Trial**
   (Exness demo servers look like `Exness-MT5Trial…`; real ones look like `Exness-MT5Real…`).
2. The bottom-right corner of the terminal must show a ping (e.g. `35 ms`), not "No connection".
3. Run:

```powershell
python -m app.cli mt5-check
```

This reads `account_info().trade_mode` from the terminal itself. It prints
`RESULT: VERIFIED DEMO ACCOUNT` only if the terminal reports **DEMO**, is connected, the server
name does not indicate a real-money server, and (if you set `TRADE1_MT5_LOGIN`) the login matches.
Anything else prints `NOT VERIFIED AS DEMO` with the reasons.

It also estimates the broker's server-clock offset from live ticks. Exness servers are expected
to run on UTC+0. If the estimate differs, set `TRADE1_MT5_SERVER_UTC_OFFSET_HOURS`. The estimate
only works while some market is open.

## Step 2 — Discover markets and download history

```powershell
python -m app.cli discover          # stores every symbol your account offers
python -m app.cli ingest --symbol EURUSDm --timeframe H1 --start 2024-01-01 --end 2025-01-01
python -m app.cli coverage          # what is stored, per symbol and timeframe
```

Use the exact symbol names `discover` reports (Exness adds suffixes such as `m`).
Every download stores an *ingestion run* recording bars received, usable, newly inserted and
already present, plus duplicates, invalid OHLC, misaligned timestamps, gaps (weekend / session
break / unexpected), late history start and stale data. Re-running a download never creates
duplicate bars. Empty responses are recorded as failures; no bars are ever invented.

## Step 3 — Run the API

```powershell
uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

Every route except `/api/health` requires `Authorization: Bearer <TRADE1_API_TOKEN>`.
If no token is configured, protected routes return 503 (fail closed).
Interactive docs: http://127.0.0.1:8000/docs

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness, DB status, execution mode |
| GET | `/api/mt5/status` | Connection + demo verification |
| GET | `/api/mt5/account` | Account summary (no personal name) + verification |
| POST | `/api/markets/sync` | Re-discover symbols |
| GET | `/api/markets?category=&search=&metadata_ok=` | Filter instruments |
| GET | `/api/markets/{symbol}` | Full contract spec and validation issues |
| POST | `/api/market-data/ingest` | Download history (UTC timestamps required) |
| GET | `/api/market-data?symbol=&timeframe=&start=&end=` | Stored bars |
| GET | `/api/market-data/runs`, `/api/market-data/coverage` | Download audit and coverage |
| GET | `/api/diagnostics` | Plain explanations of problems |
| POST | `/api/news/refresh` | Fetch the calendar feed (rate-limited) |
| GET | `/api/news/events?start=&end=&currency=&impact=` | Stored events + coverage |
| GET | `/api/news/blackout?symbol=&at=` | Would a new entry be blocked by news? |
| GET/POST | `/api/strategies`, GET `/api/strategies/{id}` | Available strategies, immutable parameter versions |
| POST | `/api/backtests` | Run a backtest on stored data |
| GET | `/api/backtests`, `/api/backtests/{id}`, `/{id}/metrics`, `/{id}/trades` | Results, equity curve, trades |
| POST | `/api/validation/{backtest_id}` | Validate a backtest's strategy, symbol, range and costs |
| POST | `/api/research/multi-market`, `/api/research/portfolio`, `/api/research/tick-check/{backtest_id}` | Research studies |
| GET | `/api/research`, `/api/research/{id}` | Study results |
| GET | `/api/backtests/{id}/journal?format=csv\|json` | Trade journal export |
| GET | `/api/audit`, `/api/settings` | Audit log; effective settings (secrets shown only as set / not set) |
| GET | `/api/validation`, `/api/validation/{id}` | Validation reports and verdicts |

## Dashboard

`frontend/` is a Next.js 16 + TypeScript + Tailwind app. It listens on **127.0.0.1 only**.

* The browser never sees the API token. The dashboard's server reads `TRADE1_API_TOKEN` from
  `backend/.env` and adds it when calling the backend.
* The dashboard's proxy forwards only an allow-list of read routes plus the reviewed actions (discover,
  download, backtest, validate, news refresh). It refuses requests from other websites.
* Every number comes from stored backend data. Pages show empty states instead of sample figures.
  Backtest pages carry a "historical simulation" label.

Development: `cd frontend && npm ci && npm run dev` (with the backend running on port 8000).

## Backtesting

```powershell
python -m app.cli backtest --symbol XAUUSDm --timeframe H1 --htf H4 --start 2023-01-01 --end 2025-01-01 `
    --balance 10000 --risk 0.25 --commission 0 --slippage 0
```

You need the primary and the higher-timeframe history stored first (`ingest`), plus `discover`
for the contract specification. Execution model, stated in every report:

* Bars are MT5 **bid** bars. Buys fill at ask = bid + spread; spread per bar is the recorded value
  (often the bar's minimum, so you can add `--min-spread` / `--spread-multiplier`).
* Decisions at a bar's close, fills at the **next bar's open**. A higher-timeframe bar is used only
  after it has closed.
* Stop and target checked against high/low. If both fall inside one bar, the **stop is assumed first**.
  A bar opening beyond the stop fills at that open (gap). Targets fill at the target price.
* Size = `--risk` % of equity at the stop including commission, rounded **down** to the volume step.
  If the broker minimum would exceed the risk, the trade is rejected, not enlarged. Margin is checked.
* Swap is charged per night when the symbol's swap mode is points (triple on Wednesday); otherwise
  the report says it is not modelled. Exness swap-free accounts: treat swap as 0.
* Daily-loss lockout, equity-drawdown lockout, and a cooldown after consecutive losses.
* Every report includes the sample-size warning, rejected signals by reason, costs and a data fingerprint.

## Validation

```powershell
python -m app.cli validate --symbol XAUUSDm --timeframe H1 --htf H4 --start 2023-01-01 --end 2025-01-01 --strategy trend_continuation
```

| Step | What it does |
|---|---|
| Chronological split | 60% development, 20% validation, 20% final out-of-sample (by bar count, never shuffled) |
| Walk-forward | Development+validation cut into 6 blocks. For each of 5 folds, parameters are picked on the earlier blocks from a small grid, then tested on the next block |
| Sensitivity | Each tunable parameter moved ±20%, one at a time. A real edge should survive small changes |
| Sample rules | ≥200 trades total, ≥30 in validation, ≥30 out-of-sample (engineering defaults, configurable) |
| Protected out-of-sample | The final block is meant to be seen once. Every re-use (e.g. after changing parameters) is counted, and a re-used block can never produce PASSED |

**Verdict:** FAILED if any performance or robustness check fails (a small sample cannot rescue a losing or
fragile strategy). INCONCLUSIVE if only sample-size or protocol checks fail. PASSED only if every check
passes. Even PASSED only means "survived these tests on this data". The next step is 2–3 months of
forward testing on demo, compared against the backtest. **Expect most strategies to fail.** That is the
system stopping you from losing money.

## Research tools

| Tool | Command | What it tells you |
|---|---|---|
| Monte Carlo | in every backtest and validation report | Reshuffles the trades 2,000 times: the drawdown and losing streak to plan for (95th percentile), not the one history produced. Validation fails if the 95th-percentile drawdown exceeds 25% |
| Cost stress | in every validation report | Re-runs development+validation with spread ×2 plus slippage equal to the median spread. Validation fails if the edge disappears |
| Multi-market | `python -m app.cli multi-market --symbols XAUUSDm,EURUSDm,GBPUSDm --timeframe H1 --htf H4 --start ... --end ... --strategy ...` | Same parameters on every symbol. CONSISTENT only if at least two thirds are positive after costs |
| Tick check | `python -m app.cli tick-check --backtest-id N` (MT5 must be running) | Re-plays each stop/target exit against MT5 tick data where available. Reports trades the bar model got wrong |
| Portfolio | `python -m app.cli portfolio --legs legs.json --start ... --end ...` | Legs on one account with max open positions, total open risk, daily loss, drawdown lockout and the **currency-correlation limit** (gold long + EURUSD long = two short-USD bets) |

All of these are also on the dashboard's **Research studies** page. Backtests accept
`volatility_sizing` (positions shrink when ATR is above its median) and `news_blackout`
(only applied in weeks with stored calendar data; the report says how many signals were not covered).

## Running it every day

* `scripts\install_autostart.bat` registers three tasks for your Windows user: the dashboard at logon,
  a database backup at 23:30 (to `backups\`, keeping 14), and a news-calendar fetch at 06:05.
  `uninstall_autostart.bat` removes them. Set MT5 to start with Windows too.
* Manual backup: `python -m app.cli backup`.
* Trade journal: on a backtest page, **Export journal (CSV)**, or `GET /api/backtests/{id}/journal?format=csv|json`.

## Demo trading (forward testing)

Demo trading is locked by default. To unlock it, **all** of these must hold, and they are checked on the server:

1. A strategy has a **PASSED** validation report whose out-of-sample data was used only once, including
   the cost stress and walk-forward checks.
2. `TRADE1_EXECUTION_MODE=DEMO` in `backend\.env`. Only the server sets this; the browser cannot.
3. MT5 is connected and the terminal reports a **DEMO** account. Trade1 remembers this login and server.
4. The instrument's specification is valid, risk limits are set, there are no open reconciliation
   issues, and the emergency stop is off.
5. You select the report on **Demo automation** and type the confirmation sentence.

Then start `scripts\run_demo.bat`. On every newly closed bar it runs the same strategy code as the
backtest. Every order goes through the **demo execution gateway**, the only code in Trade1 that may
call MT5's `order_send`. A test enforces this. For every order it:

* re-checks everything above, plus a fresh quote, spread, stops level, volume, open positions,
  daily loss, drawdown and free margin;
* re-reads the account's trade mode inside the MT5 lock right before sending. Anything but DEMO, or a
  different account, is refused and triggers the emergency stop;
* uses an idempotency key (strategy, symbol, bar, side), so a signal can never produce two orders;
* records "filled" only after finding the position in MT5 with its stop-loss and take-profit. No reply, a
  timeout or a missing position means **uncertain**: no retry, and new entries are blocked until
  reconciliation (automatic when MT5's state is unambiguous, otherwise you resolve it with a note).

The **EMERGENCY STOP** button (dashboard, or `python -m app.cli emergency-stop`) disables entries first,
then cancels Trade1's pending demo orders. Open positions keep their broker-side stop-loss and take-profit.
`daily-report` compares demo results with what validation predicted. See [docs/VPS.md](docs/VPS.md)
for running 24/5 and Telegram alerts.

## Optional: Forex Factory economic calendar

Trade1 can read the calendar **export** that Forex Factory publishes for the current week. It does
not scrape the calendar web page. It is used as a **news blackout**: no new entries from 30 minutes
before to 30 minutes after a high-impact event in a currency the symbol is exposed to.
For example, EURUSD is exposed to EUR and USD events, XAUUSD to USD, and a USD-quoted index to USD.
By default the blackout applies to forex, metals, energy and indices, but not crypto.

1. On https://www.forexfactory.com/calendar, find the calendar export and copy the **JSON** link
   for "this week".
2. Put it in `.env` as `TRADE1_NEWS_FEED_URL=...`.
3. Run `python -m app.cli news-check`. It fetches and validates the feed without saving and
   prints `FEED FORMAT MATCHES`, or the exact fields or records it rejected.
4. Collect regularly with `python -m app.cli news-fetch`. Refreshes are limited to once per 60 minutes.
   You can also import a saved file: `python -m app.cli news-import --file calendar.json`.

**Limitations:**
* The export only covers the current week, so news history starts on the day you begin
  collecting. For older periods, backtests will report the news filter as **unavailable**,
  never as "no news" (see `coverage` in `/api/news/events`).
* Only the schedule is stored. Released "actual" values are not used, which avoids look-ahead bias.
* The feed format could not be checked from the development sandbox (no network access to
  Forex Factory). The adapter validates every record strictly and rejects anything that does
  not match, so a format change shows up as an error, not as wrong data.
* Review Forex Factory's terms of use for your use case.

## Tests

```powershell
pytest                                   # unit tests, mocked MT5, SQLite; never touches a broker
$env:TRADE1_TEST_PG_URL="postgresql+psycopg://trade1:PASS@localhost:5432/trade1_test"
pytest tests/test_postgres.py            # runs migrations + ingestion on a throwaway PostgreSQL DB
```

The safety tests include: only a terminal-reported DEMO account verifies; REAL, CONTEST,
unknown modes, real-money server names, disconnected terminals and login mismatches are all
rejected; no API route handles orders; and no application module references `order_send`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `MetaTrader5 Python package is not installed` | You're not on Windows, or `pip install MetaTrader5` failed. Use 64-bit Python 3.12. |
| `MT5 initialize failed … IPC` | Start the MT5 terminal first. Set `TRADE1_MT5_PATH` if you have several terminals. |
| `account_info failed` | The terminal isn't logged in. Use File → Login to Trade Account. |
| Ingestion `failed`, "no bars" | The symbol has no history for that range, or the terminal's **Tools → Options → Charts → Max bars in chart** limit is too low. |
| "history starts after the requested start" | The broker serves less history than you asked for. The run records exactly what was received. |
| Timestamps look shifted | Run `mt5-check` while markets are open and set `TRADE1_MT5_SERVER_UTC_OFFSET_HOURS`. |
