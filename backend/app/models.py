"""Database models for Phase B (foundation + historical data).

Later phases add strategy, backtest, signal, risk and demo-execution tables in
their own migrations.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JsonType = JSON().with_variant(JSONB(), "postgresql")
# SQLite only auto-increments INTEGER PRIMARY KEY
BigId = BigInteger().with_variant(Integer(), "sqlite")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JsonType)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 onupdate=utcnow)


class Instrument(Base):
    __tablename__ = "instruments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    broker_server: Mapped[str] = mapped_column(String(100))
    symbol: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32), index=True)
    subcategory: Mapped[str | None] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(String(255))
    spec: Mapped[dict] = mapped_column(JsonType)
    issues: Mapped[list] = mapped_column(JsonType, default=list)
    metadata_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 onupdate=utcnow)
    __table_args__ = (UniqueConstraint("broker_server", "symbol", name="uq_instrument_server_symbol"),)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(64))
    timeframe: Mapped[str] = mapped_column(String(8))
    requested_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    requested_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|ok|warning|failed
    source: Mapped[dict] = mapped_column(JsonType, default=dict)
    bars_received: Mapped[int] = mapped_column(Integer, default=0)
    bars_valid: Mapped[int] = mapped_column(Integer, default=0)
    bars_inserted: Mapped[int] = mapped_column(Integer, default=0)
    bars_already_present: Mapped[int] = mapped_column(Integer, default=0)
    first_bar: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_bar: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quality: Mapped[dict] = mapped_column(JsonType, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_ingestion_symbol_tf", "symbol", "timeframe", "started_at"),)


class Bar(Base):
    """Bid-based OHLC bars as delivered by MT5, open time in UTC."""

    __tablename__ = "bars"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(64))
    timeframe: Mapped[str] = mapped_column(String(8))
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    tick_volume: Mapped[int] = mapped_column(BigInteger, default=0)
    real_volume: Mapped[int] = mapped_column(BigInteger, default=0)
    spread_points: Mapped[int] = mapped_column(Integer, default=0)
    ingestion_run_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_runs.id"))
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "time", name="uq_bar_symbol_tf_time"),)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), index=True)
    details: Mapped[dict] = mapped_column(JsonType, default=dict)


class HealthEvent(Base):
    __tablename__ = "health_events"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    component: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    details: Mapped[dict] = mapped_column(JsonType, default=dict)


class EconomicEventRow(Base):
    """Scheduled economic-calendar events from an optional news provider."""

    __tablename__ = "economic_events"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32))
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    currency: Mapped[str] = mapped_column(String(8), index=True)
    title: Mapped[str] = mapped_column(String(200))
    impact: Mapped[str] = mapped_column(String(16))
    forecast: Mapped[str | None] = mapped_column(String(32))
    previous: Mapped[str | None] = mapped_column(String(32))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("provider", "event_time", "currency", "title",
                                       name="uq_event_provider_time_ccy_title"),)



class StrategyVersion(Base):
    """Immutable: a (name, version, params) combination is stored once and never edited."""

    __tablename__ = "strategy_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(16))
    params: Mapped[dict] = mapped_column(JsonType)
    params_hash: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("name", "version", "params_hash", name="uq_strategy_version"),)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_version_id: Mapped[int] = mapped_column(ForeignKey("strategy_versions.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    timeframe: Mapped[str] = mapped_column(String(8))
    htf: Mapped[str | None] = mapped_column(String(8))
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    config: Mapped[dict] = mapped_column(JsonType)
    data_fingerprint: Mapped[dict] = mapped_column(JsonType, default=dict)
    result_kind: Mapped[str] = mapped_column(String(32), default="historical_backtest")
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|completed|failed
    error: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict] = mapped_column(JsonType, default=dict)
    equity_curve: Mapped[list] = mapped_column(JsonType, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id", ondelete="CASCADE"), index=True)
    side: Mapped[str] = mapped_column(String(4))
    signal_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    take_profit: Mapped[float] = mapped_column(Float)
    exit_reason: Mapped[str] = mapped_column(String(64))
    gross_pnl: Mapped[float] = mapped_column(Float)
    commission: Mapped[float] = mapped_column(Float)
    swap: Mapped[float] = mapped_column(Float)
    spread_cost: Mapped[float] = mapped_column(Float)
    slippage_cost: Mapped[float] = mapped_column(Float)
    net_pnl: Mapped[float] = mapped_column(Float)
    planned_risk_money: Mapped[float] = mapped_column(Float)
    bars_held: Mapped[int] = mapped_column(Integer)
    ambiguous_bar: Mapped[bool] = mapped_column(Boolean, default=False)
    regime: Mapped[str | None] = mapped_column(String(16))
    rationale: Mapped[str] = mapped_column(Text)


class ValidationReport(Base):
    __tablename__ = "validation_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    strategy_version_id: Mapped[int] = mapped_column(ForeignKey("strategy_versions.id"), index=True)
    backtest_run_id: Mapped[int | None] = mapped_column(ForeignKey("backtest_runs.id"))
    symbol: Mapped[str] = mapped_column(String(64))
    timeframe: Mapped[str] = mapped_column(String(8))
    htf: Mapped[str | None] = mapped_column(String(8))
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    config: Mapped[dict] = mapped_column(JsonType)
    data_fingerprint: Mapped[dict] = mapped_column(JsonType, default=dict)
    # strategy name|symbol|timeframe|oos start|oos end - shared by every parameter set
    oos_key: Mapped[str] = mapped_column(String(255), index=True)
    oos_prior_uses: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|completed|failed
    verdict: Mapped[str | None] = mapped_column(String(16))  # PASSED|FAILED|INCONCLUSIVE
    reasons: Mapped[list] = mapped_column(JsonType, default=list)
    report: Mapped[dict] = mapped_column(JsonType, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class ResearchReport(Base):
    """Multi-market, tick-check and portfolio studies."""

    __tablename__ = "research_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # multi_market | tick_check | portfolio
    status: Mapped[str] = mapped_column(String(16), default="running")
    summary: Mapped[str | None] = mapped_column(String(255))
    params: Mapped[dict] = mapped_column(JsonType, default=dict)
    result: Mapped[dict] = mapped_column(JsonType, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
