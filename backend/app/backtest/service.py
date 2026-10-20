"""Run a backtest from stored data and persist everything needed to reproduce it."""
from __future__ import annotations

import hashlib
from datetime import datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..data.ingestion import load_bars
from ..models import AuditLog, BacktestRun, BacktestTrade, Instrument, StrategyVersion, utcnow
from ..mt5.constants import TIMEFRAMES, Timeframe
from .engine import BacktestConfig, BacktestEngine, SpecError
from .metrics import compute_metrics, downsample_equity
from .strategies import get_strategy

RESULT_LABEL = ("HISTORICAL BACKTEST - bar-based simulation on MT5 bid bars. In-sample unless a "
                "validation report says otherwise. Past results do not predict future performance.")


class BacktestError(ValueError):
    pass


def fingerprint(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"bars": 0}
    h = hashlib.sha256(pd.util.hash_pandas_object(df[["time", "open", "high", "low", "close"]],
                                                  index=False).values.tobytes()).hexdigest()[:16]
    return {"bars": len(df), "first": df["time"].iloc[0].isoformat(),
            "last": df["time"].iloc[-1].isoformat(), "sha256_16": h}


def get_or_create_version(session: Session, strategy) -> StrategyVersion:
    meta = strategy.get_strategy_metadata()
    row = session.scalars(select(StrategyVersion).where(
        StrategyVersion.name == meta["name"], StrategyVersion.version == meta["version"],
        StrategyVersion.params_hash == meta["params_hash"])).first()
    if row is None:
        row = StrategyVersion(name=meta["name"], version=meta["version"], params=meta["params"],
                              params_hash=meta["params_hash"])
        session.add(row)
        session.flush()
    return row


def instrument_spec(session: Session, symbol: str) -> dict:
    inst = session.scalars(select(Instrument).where(Instrument.symbol == symbol)
                           .order_by(Instrument.updated_at.desc())).first()
    if inst is None:
        raise BacktestError(f"{symbol} has no stored contract specification; run `discover` first")
    return dict(inst.spec)


def run_backtest(session: Session, *, symbol: str, timeframe: str, start: datetime, end: datetime,
                 strategy_name: str, params: dict | None = None, htf: str | None = None,
                 config: BacktestConfig | None = None) -> BacktestRun:
    tf = Timeframe(timeframe)
    htf_tf = Timeframe(htf) if htf else None
    if htf_tf and TIMEFRAMES[htf_tf].minutes < TIMEFRAMES[tf].minutes:
        raise BacktestError("higher timeframe must not be shorter than the primary timeframe")
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise BacktestError("start/end must be timezone-aware UTC and end after start")
    strategy = get_strategy(strategy_name, params)
    if strategy.uses_higher_timeframe and getattr(strategy.params, "use_htf_filter", False) and not htf_tf:
        raise BacktestError(f"{strategy_name} needs a higher timeframe (e.g. --htf H4) or use_htf_filter=false")
    config = config or BacktestConfig()
    version = get_or_create_version(session, strategy)

    run = BacktestRun(strategy_version_id=version.id, symbol=symbol, timeframe=tf.value,
                      htf=htf_tf.value if htf_tf else None, start=start, end=end,
                      config=config.model_dump(), status="running")
    session.add(run)
    session.flush()
    try:
        spec = instrument_spec(session, symbol)
        primary = load_bars(session, symbol, tf, start, end)
        if len(primary) < strategy.warmup_bars() + 50:
            raise BacktestError(f"only {len(primary)} {tf.value} bars stored for {symbol} in range; "
                                f"download history with `ingest` first")
        htf_df = None
        if htf_tf:
            # include warm-up history before `start` for the HTF indicator
            htf_df = load_bars(session, symbol, htf_tf, None, end)
            if htf_df.empty:
                raise BacktestError(f"no {htf_tf.value} bars stored for {symbol}; ingest them first")
        run.data_fingerprint = {"primary": fingerprint(primary),
                                "htf": fingerprint(htf_df) if htf_df is not None else None,
                                "spec_symbol": spec.get("symbol"),
                                "spec_issues": spec.get("issues", [])}
        df = strategy.prepare(primary, htf_df, TIMEFRAMES[tf].minutes,
                              TIMEFRAMES[htf_tf].minutes if htf_tf else None)
        if config.account_currency != (spec.get("account_currency") or config.account_currency):
            raise BacktestError("account currency differs from the currency the spec was captured in")
        result = BacktestEngine(strategy, spec, config, TIMEFRAMES[tf].minutes).run(df)
        metrics = compute_metrics(result, config.starting_balance)
        metrics["label"] = RESULT_LABEL
        metrics["strategy"] = strategy.get_strategy_metadata()
        run.metrics = metrics
        run.equity_curve = downsample_equity(result.equity)
        for t in result.trades:
            session.add(BacktestTrade(
                run_id=run.id, side=t.side, signal_time=t.signal_time, entry_time=t.entry_time,
                exit_time=t.exit_time, entry_price=t.entry_price, exit_price=t.exit_price,
                volume=t.volume, stop_loss=t.stop_loss, take_profit=t.take_profit,
                exit_reason=t.exit_reason, gross_pnl=t.gross_pnl, commission=t.commission,
                swap=t.swap, spread_cost=t.spread_cost, slippage_cost=t.slippage_cost,
                net_pnl=t.net_pnl, planned_risk_money=t.planned_risk_money, bars_held=t.bars_held,
                ambiguous_bar=t.ambiguous_bar, rationale=t.rationale))
        run.status = "completed"
    except (BacktestError, SpecError, ValueError) as exc:
        run.status = "failed"
        run.error = str(exc)
    run.finished_at = utcnow()
    session.add(AuditLog(actor="system", action="backtest.run",
                         details={"run_id": run.id, "status": run.status, "symbol": symbol,
                                  "strategy": strategy.get_strategy_metadata()["params_hash"]}))
    session.flush()
    return run
