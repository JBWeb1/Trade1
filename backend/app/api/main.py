"""FastAPI application. Phase B routes: health, MT5 status, markets, market data.

There is no route that sends broker orders.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .. import __version__
from ..config import Settings, get_settings
from ..data.ingestion import coverage, ingest_bars, load_bars
from ..db import get_session
from ..logging_setup import configure_logging
from ..integrations.news import forexfactory
from ..integrations.news.filter import NewsBlackout
from ..integrations.news.service import NewsDisabled, RefreshTooSoon, refresh_from_feed
from ..integrations.news.store import coverage as news_coverage, load_events
from ..markets.service import sync_instruments
from ..models import AuditLog, IngestionRun, Instrument
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
