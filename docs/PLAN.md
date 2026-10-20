# Trade1 Trading AI — Implementation Plan and Status

## Phase A — Inspection (done)

**Starting point:** a single-file-style Python bot (`trade1/`, commit `0dbaf88`) with a Quiver Quant
client, a liquidity-sweep strategy, a simple risk sizer and an MT5 broker class. It had no database,
API, UI, backtester or validation, and no demo-account check. Its `mode: live` path could send orders
to any logged-in account.

**Decisions:**
* The old package was removed: its live-order path breaks the "technically incapable of live
  trading" rule. It remains in git history.
* Reused ideas, re-implemented with tests: MT5 symbol-spec handling, risk sizing from stop distance
  (Phase C), the liquidity-sweep logic (a candidate strategy for Phase C).
* Quiver Quant was not carried over. Its endpoints were written from memory and never verified
  against the official docs. If wanted, it returns as an optional adapter after verification, and only
  for US-stock CFDs.

**Blockers found:**
* The `MetaTrader5` package exists only for Windows. Development and tests here use a mocked
  module that mirrors the documented return types. Real-terminal checks must run on your machine.
* This development sandbox cannot reach forexfactory.com, so the news feed format is validated
  defensively and must be confirmed with `python -m app.cli news-check`.

## Phase B — Foundation + historical data (done)

* Settings with `SecretStr` secrets; `ExecutionMode` enum with **no LIVE member**; default BACKTEST.
* JSON logging with secret redaction.
* SQLAlchemy models plus Alembic migrations `0001` (instruments, ingestion runs, bars, audit, health,
  settings) and `0002` (economic events). Tested on PostgreSQL 16: upgrade, downgrade, upgrade, and no drift.
* `MT5Gateway`: read-only, lock-serialised. Implements initialize/shutdown, connection check, terminal
  and account info, demo verification, symbols, ticks, bars, historical ticks, positions, orders,
  history orders and deals, and margin and profit calculators. There is no order function.
* Market discovery: classification, full specs and validation flags. Flagged symbols get
  `metadata_ok = false`, which Phase E will use to block demo orders.
* Ingestion: chunked, quality-checked, idempotent, every run audited.
* API routes (bearer auth, fail closed) and CLI.
* Optional Forex Factory calendar adapter and news blackout filter.

## Phase C — Backtesting (next)

1. Multi-timeframe bar alignment: a higher-timeframe bar becomes visible only after it **closes**
   (H4 bar opened 08:00 → usable from 12:00). Tests prove no look-ahead.
2. Event-driven engine on stored bid bars. Ask = bid + recorded spread (or a configured spread),
   commission per lot, and configurable slippage. SL/TP checked intrabar. When both are hit in one
   bar, the **stop is assumed first** (conservative). A gap through the stop fills at the bar's open.
   Volume is rounded to step and min/max, margin is checked, and swap is applied per rollover. Results
   are always labelled "bar-based simulation".
3. Strategy interface (`generate_signal`, `calculate_entry/stop_loss/take_profit/exit`,
   `validate_market_conditions`, `get_strategy_metadata`), with immutable versioned parameter sets.
4. Candidates: trend continuation, breakout, pullback, plus a regime filter. Optional: liquidity sweep.
5. Metrics: trades, win rate, average win and loss, expectancy after costs, profit factor, net return,
   max drawdown and its duration, Sharpe (assumptions stated), losing streaks, exposure, costs, and
   breakdowns by symbol, timeframe, regime and month.
6. Validation: chronological development/validation/final out-of-sample split, walk-forward runs,
   parameter sensitivity, and verdicts PASSED / FAILED / INCONCLUSIVE with reasons. The final
   out-of-sample set is locked after first use.
7. The news blackout is applied only where calendar coverage exists, and coverage is reported.

## Phase D — UI

Next.js + TypeScript + Tailwind dark terminal UI, using only real backend data and showing empty
states where nothing exists yet.

## Phase E — Demo-only execution

A demo execution gateway, the only module allowed to call `order_send`. It re-verifies the demo
account on **every** order, runs the validation gate, risk checks, idempotency keys, reconciliation
before any retry, and an emergency stop. The safety test that currently forbids `order_send`
everywhere becomes "only in the demo gateway, and only after verification".

## Phase F — Final verification

Full test suite, frontend build, migrations against your database, security review, and a
documented list of remaining limitations.
