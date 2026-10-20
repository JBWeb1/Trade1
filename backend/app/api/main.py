"""FastAPI application. Phase B routes: health, MT5 status, markets, market data.

There is no route that sends broker orders.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .. import __version__
from ..backtest.engine import BacktestConfig
from ..backtest.service import get_or_create_version, run_backtest, run_validation_report
from ..backtest.validation import ValidationPolicy
from ..backtest.strategies import REGISTRY, get_strategy
from ..config import Settings, get_settings
from ..data.ingestion import coverage, ingest_bars, load_bars
from ..db import get_session
from ..logging_setup import configure_logging
from ..integrations.news import forexfactory
from ..integrations.news.filter import NewsBlackout
from ..integrations.news.service import NewsDisabled, RefreshTooSoon, refresh_from_feed
from ..integrations.news.store import coverage as news_coverage, load_events
from ..markets.service import sync_instruments
from ..models import (AuditLog, BacktestRun, BacktestTrade, IngestionRun, Instrument, OrderIntent,
                      ReconciliationEvent, ResearchReport, StrategyVersion, ValidationReport, utcnow)
from ..mt5.constants import Timeframe
from ..mt5.gateway import MT5Error, MT5Gateway
from .deps import get_gateway, require_token

@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging(get_settings().log_level)
    yield
    get_gateway().shutdown_terminal()


app = FastAPI(title="Trade1 Trading AI", version=__version__, lifespan=lifespan)
Auth = Annotated[str, Depends(require_token)]
DB = Annotated[Session, Depends(get_session)]
GW = Annotated[MT5Gateway, Depends(get_gateway)]

MAX_RANGE_DAYS = {Timeframe.M1: 120, Timeframe.M15: 1100, Timeframe.H1: 3700, Timeframe.H4: 7300}


def _mt5_unavailable(exc: MT5Error) -> HTTPException:
    return HTTPException(503, f"MT5 unavailable: {exc}")


@app.get("/api/health")
def health(db: DB, settings: Settings = Depends(get_settings)) -> dict:
    """Unauthenticated liveness check. Reveals no account data."""
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "database": db_ok, "version": __version__,
            "execution_mode": settings.execution_mode.value, "live_trading": "not implemented"}


@app.get("/api/mt5/status")
def mt5_status(_: Auth, gw: GW) -> dict:
    status = gw.check_connection()
    status["verification"] = gw.verify_demo_account().as_dict()
    return status


@app.get("/api/mt5/account")
def mt5_account(_: Auth, gw: GW) -> dict:
    try:
        acct = gw.get_account_info()
    except MT5Error as exc:
        raise _mt5_unavailable(exc)
    return {"account": acct, "verification": gw.verify_demo_account().as_dict()}


@app.post("/api/markets/sync")
def markets_sync(_: Auth, db: DB, gw: GW) -> dict:
    try:
        return sync_instruments(db, gw)
    except MT5Error as exc:
        raise _mt5_unavailable(exc)


@app.get("/api/markets")
def markets(_: Auth, db: DB,
            category: str | None = Query(None, max_length=32),
            search: str | None = Query(None, max_length=64),
            metadata_ok: bool | None = None,
            limit: int = Query(500, ge=1, le=5000), offset: int = Query(0, ge=0)) -> dict:
    q = select(Instrument)
    if category:
        q = q.where(Instrument.category == category)
    if search:
        q = q.where(Instrument.symbol.ilike(f"%{search}%"))
    if metadata_ok is not None:
        q = q.where(Instrument.metadata_ok == metadata_ok)
    rows = db.scalars(q.order_by(Instrument.category, Instrument.symbol).limit(limit).offset(offset)).all()
    return {"items": [{"symbol": r.symbol, "server": r.broker_server, "category": r.category,
                       "subcategory": r.subcategory, "description": r.description,
                       "metadata_ok": r.metadata_ok, "issues": r.issues,
                       "updated_at": r.updated_at} for r in rows]}


@app.get("/api/markets/{symbol}")
def market_detail(symbol: str, _: Auth, db: DB) -> dict:
    rows = db.scalars(select(Instrument).where(Instrument.symbol == symbol)).all()
    if not rows:
        raise HTTPException(404, f"{symbol} has not been discovered; run POST /api/markets/sync")
    return {"items": [{"server": r.broker_server, "spec": r.spec, "issues": r.issues,
                       "metadata_ok": r.metadata_ok} for r in rows]}


class IngestRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=64)
    timeframe: Timeframe
    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamps must include a timezone (use UTC, e.g. 2025-01-01T00:00:00Z)")
        return v.astimezone(timezone.utc)


@app.post("/api/market-data/ingest")
def market_data_ingest(req: IngestRequest, _: Auth, db: DB, gw: GW) -> dict:
    if req.end <= req.start:
        raise HTTPException(422, "end must be after start")
    if (req.end - req.start).days > MAX_RANGE_DAYS[req.timeframe]:
        raise HTTPException(422, f"range too large for {req.timeframe.value}; split the request")
    run = ingest_bars(db, gw, req.symbol, req.timeframe, req.start, req.end)
    db.add(AuditLog(actor="api-user", action="market_data.ingest",
                    details={"run_id": run.id, "symbol": req.symbol, "timeframe": req.timeframe.value,
                             "status": run.status}))
    return _run_dict(run)


def _run_dict(r: IngestionRun) -> dict:
    return {k: getattr(r, k) for k in (
        "id", "symbol", "timeframe", "requested_start", "requested_end", "started_at", "finished_at",
        "status", "source", "bars_received", "bars_valid", "bars_inserted", "bars_already_present",
        "first_bar", "last_bar", "quality", "error")}


@app.get("/api/market-data/runs")
def market_data_runs(_: Auth, db: DB, limit: int = Query(50, ge=1, le=500)) -> dict:
    rows = db.scalars(select(IngestionRun).order_by(IngestionRun.id.desc()).limit(limit)).all()
    return {"items": [_run_dict(r) for r in rows]}


@app.get("/api/market-data/coverage")
def market_data_coverage(_: Auth, db: DB) -> dict:
    return {"items": coverage(db)}


@app.get("/api/market-data")
def market_data(_: Auth, db: DB, symbol: str = Query(min_length=1, max_length=64),
                timeframe: Timeframe = Query(),
                start: datetime | None = None, end: datetime | None = None,
                limit: int = Query(5000, ge=1, le=100_000)) -> dict:
    for v in (start, end):
        if v is not None and v.tzinfo is None:
            raise HTTPException(422, "timestamps must include a timezone")
    df = load_bars(db, symbol, timeframe, start, end).tail(limit)
    return {"symbol": symbol, "timeframe": timeframe.value, "price_basis": "bid", "count": len(df),
            "bars": df.assign(time=df["time"].map(lambda t: t.isoformat())).to_dict("records")
            if len(df) else []}


@app.get("/api/diagnostics")
def diagnostics(_: Auth, db: DB, gw: GW, settings: Settings = Depends(get_settings)) -> dict:
    """Plain explanations of connection, history and data-quality problems."""
    problems: list[str] = []
    conn = gw.check_connection()
    if not conn.get("connected"):
        problems.append(f"MT5 not connected: {conn.get('error', 'terminal reports disconnected')}. "
                        "Open the MT5 terminal, log in to your Exness demo account and retry.")
    elif not conn.get("terminal_trade_allowed"):
        problems.append("Algo Trading is off in the terminal (only matters for Phase E demo orders).")
    ver = gw.verify_demo_account()
    problems += [f"Demo verification: {r}" for r in ver.reasons]

    offset = None
    if conn.get("connected"):
        try:
            from ..mt5.clock import estimate_server_offset_hours
            offset = estimate_server_offset_hours(gw)
        except MT5Error:
            pass
    if offset is not None and abs(offset - settings.mt5_server_utc_offset_hours) >= 1:
        problems.append(f"Server clock looks like UTC{offset:+.0f}h but TRADE1_MT5_SERVER_UTC_OFFSET_HOURS="
                        f"{settings.mt5_server_utc_offset_hours}. Timestamps would be shifted.")

    flagged = db.scalars(select(Instrument).where(Instrument.metadata_ok.is_(False)).limit(50)).all()
    bad_runs = db.scalars(select(IngestionRun).where(IngestionRun.status.in_(("failed", "warning")))
                          .order_by(IngestionRun.id.desc()).limit(20)).all()
    return {
        "connection": conn,
        "verification": ver.as_dict(),
        "estimated_server_utc_offset_hours": offset,
        "configured_server_utc_offset_hours": settings.mt5_server_utc_offset_hours,
        "problems": problems,
        "flagged_instruments": [{"symbol": i.symbol, "issues": i.issues} for i in flagged],
        "recent_problem_ingestions": [
            {"id": r.id, "symbol": r.symbol, "timeframe": r.timeframe, "status": r.status,
             "error": r.error, "notes": (r.quality or {}).get("notes", [])} for r in bad_runs],
    }


# ---------------------------------------------------------------- news (optional integration)

def _aware_or_422(*values: datetime | None) -> None:
    for v in values:
        if v is not None and v.tzinfo is None:
            raise HTTPException(422, "timestamps must include a timezone")


@app.post("/api/news/refresh")
def news_refresh(_: Auth, db: DB, settings: Settings = Depends(get_settings)) -> dict:
    try:
        return refresh_from_feed(db, settings)
    except NewsDisabled as exc:
        raise HTTPException(409, str(exc))
    except RefreshTooSoon as exc:
        raise HTTPException(429, str(exc))
    except forexfactory.FeedSchemaError as exc:
        raise HTTPException(502, f"news feed rejected: {exc}")


@app.get("/api/news/events")
def news_events(_: Auth, db: DB, start: datetime, end: datetime,
                currency: str | None = Query(None, min_length=3, max_length=3),
                impact: str | None = Query(None, pattern="^(high|medium|low|holiday)$")) -> dict:
    _aware_or_422(start, end)
    events = load_events(db, start, end, currency, impact)
    return {"coverage": news_coverage(db, start, end),
            "items": [e.__dict__ for e in events]}


@app.get("/api/news/blackout")
def news_blackout(_: Auth, db: DB, symbol: str = Query(min_length=1, max_length=64),
                  at: datetime | None = None, settings: Settings = Depends(get_settings)) -> dict:
    at = at or datetime.now(timezone.utc)
    _aware_or_422(at)
    inst = db.scalars(select(Instrument).where(Instrument.symbol == symbol)).first()
    if inst is None:
        raise HTTPException(404, f"{symbol} has not been discovered")
    window = timedelta(minutes=max(settings.news_blackout_before_min, settings.news_blackout_after_min))
    events = load_events(db, at - window, at + window)
    cov = news_coverage(db, at, at)
    f = NewsBlackout(events, settings.news_blackout_before_min, settings.news_blackout_after_min,
                     settings.news_impacts, settings.news_categories)
    d = f.check(inst.spec, at)
    return {"symbol": symbol, "at": at, "blocked": d.blocked, "reason": d.reason,
            "applicable": d.applicable, "calendar_data_available": cov["weeks_with_events"] > 0}


# ---------------------------------------------------------------- strategies and backtests

@app.get("/api/strategies")
def strategies(_: Auth, db: DB) -> dict:
    versions = db.scalars(select(StrategyVersion).order_by(StrategyVersion.id.desc()).limit(200)).all()
    return {"available": [{"name": n, "version": c.version, "description": c.description,
                           "params_schema": c.Params.model_json_schema()} for n, c in REGISTRY.items()],
            "versions": [{"id": v.id, "name": v.name, "version": v.version, "params": v.params,
                          "params_hash": v.params_hash, "created_at": v.created_at} for v in versions]}


class StrategyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    params: dict = Field(default_factory=dict)


@app.post("/api/strategies")
def strategy_create(req: StrategyCreate, _: Auth, db: DB) -> dict:
    try:
        v = get_or_create_version(db, get_strategy(req.name, req.params))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"id": v.id, "name": v.name, "version": v.version, "params": v.params, "params_hash": v.params_hash}


@app.get("/api/strategies/{version_id}")
def strategy_detail(version_id: int, _: Auth, db: DB) -> dict:
    v = db.get(StrategyVersion, version_id)
    if v is None:
        raise HTTPException(404, "strategy version not found")
    runs = db.scalars(select(BacktestRun.id).where(BacktestRun.strategy_version_id == v.id)).all()
    return {"id": v.id, "name": v.name, "version": v.version, "params": v.params,
            "params_hash": v.params_hash, "backtest_run_ids": runs}


class BacktestRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=64)
    timeframe: Timeframe
    htf: Timeframe | None = None
    start: datetime
    end: datetime
    strategy: str = Field(min_length=1, max_length=64)
    params: dict = Field(default_factory=dict)
    config: BacktestConfig = BacktestConfig()

    @field_validator("start", "end")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamps must include a timezone")
        return v.astimezone(timezone.utc)


def _run_summary(r: BacktestRun) -> dict:
    m = r.metrics or {}
    return {"id": r.id, "status": r.status, "error": r.error, "result_kind": r.result_kind,
            "symbol": r.symbol, "timeframe": r.timeframe, "htf": r.htf, "start": r.start, "end": r.end,
            "strategy_version_id": r.strategy_version_id, "started_at": r.started_at,
            "finished_at": r.finished_at,
            "headline": {k: m.get(k) for k in ("closed_trades", "net_return_pct", "win_rate",
                                                "expectancy_per_trade", "profit_factor",
                                                "max_drawdown_pct", "sample_warning")}}


@app.post("/api/backtests")
def backtest_create(req: BacktestRequest, _: Auth, db: DB) -> dict:
    try:
        run = run_backtest(db, symbol=req.symbol, timeframe=req.timeframe.value,
                           htf=req.htf.value if req.htf else None, start=req.start, end=req.end,
                           strategy_name=req.strategy, params=req.params, config=req.config)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return _run_summary(run)


@app.get("/api/backtests")
def backtest_list(_: Auth, db: DB, limit: int = Query(50, ge=1, le=500)) -> dict:
    rows = db.scalars(select(BacktestRun).order_by(BacktestRun.id.desc()).limit(limit)).all()
    return {"items": [_run_summary(r) for r in rows]}


def _get_run(db: Session, run_id: int) -> BacktestRun:
    r = db.get(BacktestRun, run_id)
    if r is None:
        raise HTTPException(404, "backtest not found")
    return r


@app.get("/api/backtests/{run_id}")
def backtest_detail(run_id: int, _: Auth, db: DB) -> dict:
    r = _get_run(db, run_id)
    return {**_run_summary(r), "config": r.config, "data_fingerprint": r.data_fingerprint,
            "equity_curve": r.equity_curve}


@app.get("/api/backtests/{run_id}/metrics")
def backtest_metrics(run_id: int, _: Auth, db: DB) -> dict:
    return _get_run(db, run_id).metrics or {}


@app.get("/api/backtests/{run_id}/trades")
def backtest_trades(run_id: int, _: Auth, db: DB, limit: int = Query(1000, ge=1, le=20000),
                    offset: int = Query(0, ge=0)) -> dict:
    _get_run(db, run_id)
    rows = db.scalars(select(BacktestTrade).where(BacktestTrade.run_id == run_id)
                      .order_by(BacktestTrade.entry_time).limit(limit).offset(offset)).all()
    cols = [c.name for c in BacktestTrade.__table__.columns]
    return {"items": [{c: getattr(t, c) for c in cols} for t in rows]}


# ---------------------------------------------------------------- validation

class ValidationRequest(BaseModel):
    policy: ValidationPolicy = ValidationPolicy()


def _report_summary(r: ValidationReport) -> dict:
    return {"id": r.id, "status": r.status, "verdict": r.verdict, "reasons": r.reasons, "error": r.error,
            "symbol": r.symbol, "timeframe": r.timeframe, "htf": r.htf, "start": r.start, "end": r.end,
            "strategy_version_id": r.strategy_version_id, "backtest_run_id": r.backtest_run_id,
            "oos_prior_uses": r.oos_prior_uses, "created_at": r.created_at}


@app.post("/api/validation/{backtest_id}")
def validation_create(backtest_id: int, _: Auth, db: DB, req: ValidationRequest | None = None) -> dict:
    """Validate the strategy version, symbol, timeframes, range and costs of an existing backtest."""
    run = _get_run(db, backtest_id)
    if run.status != "completed":
        raise HTTPException(409, "only completed backtests can be validated")
    version = db.get(StrategyVersion, run.strategy_version_id)

    def utc(dt: datetime) -> datetime:  # SQLite returns naive datetimes; stored values are UTC
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

    try:
        rep = run_validation_report(
            db, symbol=run.symbol, timeframe=run.timeframe, htf=run.htf, start=utc(run.start),
            end=utc(run.end), strategy_name=version.name, params=version.params,
            config=BacktestConfig(**run.config), policy=(req or ValidationRequest()).policy,
            backtest_run_id=run.id)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {**_report_summary(rep), "report": rep.report}


@app.get("/api/validation")
def validation_list(_: Auth, db: DB, limit: int = Query(50, ge=1, le=500)) -> dict:
    rows = db.scalars(select(ValidationReport).order_by(ValidationReport.id.desc()).limit(limit)).all()
    return {"items": [_report_summary(r) for r in rows]}


@app.get("/api/validation/{report_id}")
def validation_detail(report_id: int, _: Auth, db: DB) -> dict:
    r = db.get(ValidationReport, report_id)
    if r is None:
        raise HTTPException(404, "validation report not found")
    return {**_report_summary(r), "report": r.report, "data_fingerprint": r.data_fingerprint}


# ---------------------------------------------------------------- research studies

def _aware_utc(v: datetime) -> datetime:
    if v.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return v.astimezone(timezone.utc)


class MultiMarketRequest(BaseModel):
    symbols: list[str] = Field(min_length=2, max_length=30)
    timeframe: Timeframe
    htf: Timeframe | None = None
    start: datetime
    end: datetime
    strategy: str = Field(min_length=1, max_length=64)
    params: dict = Field(default_factory=dict)
    config: BacktestConfig = BacktestConfig()
    min_trades: int = Field(30, ge=1, le=10_000)

    _utc = field_validator("start", "end")(classmethod(lambda cls, v: _aware_utc(v)))


class PortfolioLeg(BaseModel):
    symbol: str = Field(min_length=1, max_length=64)
    timeframe: Timeframe
    htf: Timeframe | None = None
    strategy: str = Field(min_length=1, max_length=64)
    params: dict = Field(default_factory=dict)


class PortfolioRequest(BaseModel):
    legs: list[PortfolioLeg] = Field(min_length=1, max_length=20)
    start: datetime
    end: datetime
    config: BacktestConfig = BacktestConfig()
    limits: dict = Field(default_factory=dict)

    _utc = field_validator("start", "end")(classmethod(lambda cls, v: _aware_utc(v)))


def _research_dict(r: ResearchReport, full: bool = False) -> dict:
    d = {"id": r.id, "kind": r.kind, "status": r.status, "summary": r.summary, "error": r.error,
         "created_at": r.created_at}
    if full:
        d.update(params=r.params, result=r.result)
    return d


@app.post("/api/research/multi-market")
def research_multi_market(req: MultiMarketRequest, _: Auth, db: DB) -> dict:
    from ..backtest.studies import run_multi_market
    try:
        rep = run_multi_market(db, symbols=req.symbols, timeframe=req.timeframe.value,
                               htf=req.htf.value if req.htf else None, start=req.start, end=req.end,
                               strategy_name=req.strategy, params=req.params, config=req.config,
                               min_trades=req.min_trades)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return _research_dict(rep, full=True)


@app.post("/api/research/portfolio")
def research_portfolio(req: PortfolioRequest, _: Auth, db: DB) -> dict:
    from ..backtest.portfolio import PortfolioLimits
    from ..backtest.studies import run_portfolio
    try:
        limits = PortfolioLimits(**{"starting_balance": req.config.starting_balance,
                                    "risk_per_trade_pct": req.config.risk.risk_per_trade_pct, **req.limits})
        rep = run_portfolio(db, legs=[{**leg.model_dump(), "timeframe": leg.timeframe.value,
                                       "htf": leg.htf.value if leg.htf else None} for leg in req.legs],
                            start=req.start, end=req.end, config=req.config, limits=limits)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return _research_dict(rep, full=True)


@app.post("/api/research/tick-check/{backtest_id}")
def research_tick_check(backtest_id: int, _: Auth, db: DB, gw: GW,
                        max_trades: int = Query(300, ge=1, le=5000), only_ambiguous: bool = False) -> dict:
    from ..backtest.studies import run_tick_check
    _get_run(db, backtest_id)
    rep = run_tick_check(db, gw, backtest_run_id=backtest_id, max_trades=max_trades, only_ambiguous=only_ambiguous)
    return _research_dict(rep, full=True)


@app.get("/api/research")
def research_list(_: Auth, db: DB, kind: str | None = Query(None, max_length=32),
                  limit: int = Query(100, ge=1, le=500)) -> dict:
    q = select(ResearchReport)
    if kind:
        q = q.where(ResearchReport.kind == kind)
    rows = db.scalars(q.order_by(ResearchReport.id.desc()).limit(limit)).all()
    return {"items": [_research_dict(r) for r in rows]}


@app.get("/api/research/{report_id}")
def research_detail(report_id: int, _: Auth, db: DB) -> dict:
    r = db.get(ResearchReport, report_id)
    if r is None:
        raise HTTPException(404, "research report not found")
    return _research_dict(r, full=True)


# ---------------------------------------------------------------- journal export, audit, settings

JOURNAL_COLUMNS = ["id", "side", "signal_time", "entry_time", "exit_time", "entry_price", "exit_price", "volume",
                   "stop_loss", "take_profit", "exit_reason", "gross_pnl", "commission", "swap", "spread_cost",
                   "slippage_cost", "net_pnl", "planned_risk_money", "bars_held", "ambiguous_bar", "regime",
                   "rationale"]


@app.get("/api/backtests/{run_id}/journal")
def backtest_journal(run_id: int, _: Auth, db: DB, format: str = Query("csv", pattern="^(csv|json)$")):
    """Trade journal export (one row per simulated trade, with context) for external journal apps."""
    import csv
    import io

    run = _get_run(db, run_id)
    version = db.get(StrategyVersion, run.strategy_version_id)
    trades = db.scalars(select(BacktestTrade).where(BacktestTrade.run_id == run_id)
                        .order_by(BacktestTrade.entry_time)).all()
    context = {"source": "Trade1 Trading AI", "result_kind": run.result_kind, "backtest_run_id": run.id,
               "symbol": run.symbol, "timeframe": run.timeframe, "strategy": version.name,
               "strategy_version": version.version, "params_hash": version.params_hash,
               "account_currency": (run.config or {}).get("account_currency")}
    rows = [{c: getattr(t, c) for c in JOURNAL_COLUMNS} for t in trades]
    if format == "json":
        return {"context": context, "trades": rows}
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(context) + JOURNAL_COLUMNS)
    w.writeheader()
    for r in rows:
        w.writerow({**context, **{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.items()}})
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="trade1_backtest_{run_id}_journal.csv"'})


@app.get("/api/audit")
def audit_log(_: Auth, db: DB, action: str | None = Query(None, max_length=64),
              limit: int = Query(200, ge=1, le=2000)) -> dict:
    q = select(AuditLog)
    if action:
        q = q.where(AuditLog.action.like(f"{action}%"))
    rows = db.scalars(q.order_by(AuditLog.id.desc()).limit(limit)).all()
    return {"items": [{"id": r.id, "ts": r.ts, "actor": r.actor, "action": r.action, "details": r.details}
                      for r in rows]}


@app.get("/api/settings")
def settings_view(_: Auth, settings: Settings = Depends(get_settings)) -> dict:
    """Effective settings. Secrets are reported only as set / not set."""
    return {
        "execution_mode": settings.execution_mode.value,
        "environment": settings.env,
        "database": settings.database_url.split("://", 1)[0],
        "api_token": "set" if settings.api_token else "NOT SET",
        "mt5": {"path": settings.mt5_path or "(terminal default)", "login": "set" if settings.mt5_login else
                "(use the terminal's logged-in account)", "password": "set" if settings.mt5_password else "not set",
                "server": settings.mt5_server or "(terminal default)",
                "server_utc_offset_hours": settings.mt5_server_utc_offset_hours},
        "news": {"feed_url": "set" if settings.news_feed_url else "not set",
                 "min_refresh_minutes": settings.news_min_refresh_minutes,
                 "blackout_before_min": settings.news_blackout_before_min,
                 "blackout_after_min": settings.news_blackout_after_min,
                 "impacts": list(settings.news_impacts), "categories": list(settings.news_categories)},
        "how_to_change": "edit backend\\.env and restart the backend (start_trade1.bat)",
    }


# ---------------------------------------------------------------- demo execution (Phase E)
# None of these routes accepts order details: orders only come from the runner's
# strategy signals, through the demo execution gateway and its checks.

def _intent_dict(i: OrderIntent) -> dict:
    return {k: getattr(i, k) for k in ("id", "created_at", "kind", "symbol", "side", "volume", "stop_loss",
                                       "take_profit", "requested_price", "fill_price", "slippage_points",
                                       "position_ticket", "status", "reason", "idempotency_key", "checks")}


@app.get("/api/demo/status")
def demo_status(_: Auth, db: DB, gw: GW, settings: Settings = Depends(get_settings)) -> dict:
    from ..execution import state
    from ..execution.control import CONFIRMATION, gate_requirements
    st = state.demo_state(db)
    report = db.get(ValidationReport, st["report_id"]) if st.get("report_id") else None
    passed = db.scalars(select(ValidationReport).where(ValidationReport.verdict == "PASSED",
                                                       ValidationReport.oos_prior_uses == 0)
                        .order_by(ValidationReport.id.desc())).all()
    try:
        positions = [p for p in gw.get_open_positions() if p.get("magic") == settings.demo_magic]
    except MT5Error as exc:
        positions = []
        st["positions_error"] = str(exc)
    events = db.scalars(select(ReconciliationEvent).order_by(ReconciliationEvent.id.desc()).limit(50)).all()
    intents = db.scalars(select(OrderIntent).order_by(OrderIntent.id.desc()).limit(50)).all()
    return {
        "execution_mode": settings.execution_mode.value,
        "state": {k: v for k, v in st.items() if k != "params"},
        "emergency_stop": state.get(db, state.EMERGENCY),
        "requirements": gate_requirements(db, settings, gw, report or (passed[0] if passed else None)),
        "passed_reports": [{"id": r.id, "symbol": r.symbol, "timeframe": r.timeframe, "created_at": r.created_at,
                            "strategy_version_id": r.strategy_version_id} for r in passed],
        "confirmation_text": CONFIRMATION,
        "positions": [{k: p.get(k) for k in ("ticket", "symbol", "type", "volume", "price_open", "sl", "tp",
                                             "profit", "swap", "comment")} for p in positions],
        "orders": [_intent_dict(i) for i in intents],
        "reconciliation": [{"id": e.id, "created_at": e.created_at, "kind": e.kind, "details": e.details,
                            "resolved": e.resolved, "resolution": e.resolution} for e in events],
        "limits": {k: getattr(settings, k) for k in (
            "demo_risk_per_trade_pct", "demo_max_daily_loss_pct", "demo_max_drawdown_pct", "demo_max_open_positions",
            "demo_max_spread_multiple", "demo_max_slippage_points", "demo_max_tick_age_seconds")},
    }


class DemoEnableRequest(BaseModel):
    report_id: int = Field(ge=1)
    confirmation: str = Field(max_length=200)


@app.post("/api/demo/enable")
def demo_enable(req: DemoEnableRequest, actor: Auth, db: DB, gw: GW,
                settings: Settings = Depends(get_settings)) -> dict:
    from ..execution.control import enable_demo
    res = enable_demo(db, settings, gw, report_id=req.report_id, confirmation=req.confirmation, actor=actor)
    if not res["enabled"]:
        db.commit()  # keep the audit record of the refusal
        raise HTTPException(409, {"message": "demo trading NOT enabled: requirements failed",
                                  "requirements": res["requirements"]})
    return res


@app.post("/api/demo/disable")
def demo_disable(actor: Auth, db: DB) -> dict:
    from ..execution.control import disable_demo
    return disable_demo(db, actor)


class EmergencyRequest(BaseModel):
    reason: str = Field("manual emergency stop", max_length=300)


@app.post("/api/system/emergency-stop")
def emergency_stop(actor: Auth, db: DB, gw: GW, req: EmergencyRequest | None = None,
                   settings: Settings = Depends(get_settings)) -> dict:
    """Always allowed. Disables new entries first, then cancels Trade1's pending demo orders."""
    from ..execution.control import disable_demo
    from ..execution.gateway import DemoExecutionGateway
    disable_demo(db, actor, "emergency stop")
    return DemoExecutionGateway(settings, gw).emergency_stop(db, (req or EmergencyRequest()).reason, actor)


class ResetRequest(BaseModel):
    confirmation: str = Field(max_length=200)


@app.post("/api/system/emergency-reset")
def emergency_reset(req: ResetRequest, actor: Auth, db: DB) -> dict:
    from ..execution import state
    if req.confirmation != "RESET EMERGENCY STOP":
        raise HTTPException(422, 'type exactly: "RESET EMERGENCY STOP"')
    state.set_emergency(db, False, "reset by user", actor)
    db.add(AuditLog(actor=actor, action="system.emergency_reset", details={}))
    return {"active": False, "note": "demo trading stays disabled until you enable it again"}


@app.post("/api/demo/reconcile")
def demo_reconcile(_: Auth, db: DB, gw: GW, settings: Settings = Depends(get_settings)) -> dict:
    from ..execution.gateway import DemoExecutionGateway
    try:
        return DemoExecutionGateway(settings, gw).reconcile(db)
    except MT5Error as exc:
        raise _mt5_unavailable(exc)


class ResolveRequest(BaseModel):
    resolution: str = Field(min_length=10, max_length=1000)


@app.post("/api/demo/reconciliation/{event_id}/resolve")
def demo_resolve(event_id: int, req: ResolveRequest, actor: Auth, db: DB) -> dict:
    ev = db.get(ReconciliationEvent, event_id)
    if ev is None:
        raise HTTPException(404, "event not found")
    ev.resolved, ev.resolved_at, ev.resolution = True, utcnow(), f"{actor}: {req.resolution}"
    db.add(AuditLog(actor=actor, action="reconciliation.resolved", details={"event_id": event_id,
                                                                           "resolution": req.resolution}))
    return {"id": ev.id, "resolved": True}
