# Trade1 Trading AI

A research-first algorithmic trading platform for an **Exness MetaTrader 5 demo account**:
historical data → backtesting → out-of-sample validation → (later) demo-only automation.

> **Status: Phase B (foundation + historical data pipeline) is done.** There is no backtest
> engine, strategy, UI or order execution yet. **Live trading does not exist in this release**,
> and no code path can send a broker order. See [docs/PLAN.md](docs/PLAN.md) for the phase plan.

## What works now

| Area | Status |
|---|---|
| MT5 connection (read-only gateway, serialised calls, UTC conversion) | ✅ |
| Demo-account verification from the terminal's own `trade_mode` (fails closed) | ✅ |
| Market discovery: every symbol your account exposes, categorised, with full contract specs and metadata-validation flags | ✅ |
| Historical bars for M1 / M15 / H1 / H4: chunked download, quality checks, idempotent storage, per-run audit record | ✅ |
| Diagnostics: connection, demo verification, server-clock offset, flagged symbols, problem downloads | ✅ |
| Optional Forex Factory calendar: import, validation, news-blackout check | ✅ (format to be confirmed on your machine) |
| PostgreSQL schema with Alembic migrations | ✅ |
| Backtesting, strategies, validation, risk engine, UI, demo execution | ⏳ later phases |

## Quick install on Windows (double-click)

Trade1 is not a plugin that goes *inside* MetaTrader. It is a separate program that runs next to the
MT5 terminal and talks to it through MetaQuotes' official `MetaTrader5` Python package.

1. Download this repository: on GitHub, choose the `claude/admiring-pasteur-eqo6ed` branch, then
   **Code → Download ZIP**, and unzip it to e.g. `C:\Trade1`.
2. Double-click **`scripts\setup_windows.bat`**. It finds or installs Python 3.12, creates
   `backend\.venv`, installs the dependencies, creates `backend\.env` with a local SQLite database and
   a random API token, creates the tables, and runs the tests.
3. Open MetaTrader 5 and log in to your Exness **demo** account.
4. Double-click **`scripts\run_checks.bat`**. It runs the demo check, market discovery and two history
   downloads, and saves everything to `trade1_check_report.txt`. It places no orders.

SQLite is enough for getting started. Switch `TRADE1_DATABASE_URL` to PostgreSQL (below) for large
M1 histories.

## Requirements

* Windows 10/11 with the **MetaTrader 5 terminal from Exness**, logged in to your demo account.
* Python 3.12 or 3.13, 64-bit. The `MetaTrader5` package must install on your Python version.
  If `pip install MetaTrader5` fails, use Python 3.12.
* PostgreSQL 16 (or 15+).

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
