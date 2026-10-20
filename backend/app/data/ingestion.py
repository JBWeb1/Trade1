"""Download MT5 history, quality-check it and store it idempotently."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from ..models import Bar, IngestionRun, utcnow
from ..mt5.constants import TIMEFRAMES, Timeframe
from ..mt5.gateway import MT5Error, MT5Gateway
from .quality import clean_and_check

log = logging.getLogger(__name__)

BARS_PER_REQUEST = 50_000
INSERT_BATCH = 5_000


def _chunks(start: datetime, end: datetime, tf_minutes: int):
    span = timedelta(minutes=tf_minutes * BARS_PER_REQUEST)
    cur = start
    while cur <= end:
        nxt = min(cur + span, end)
        yield cur, nxt
        cur = nxt + timedelta(seconds=1)


# SQLite builds on Windows may allow only 999 bound variables per statement.
SQLITE_MAX_VARIABLES = 999


def _insert_ignore(session: Session, rows: list[dict]) -> int:
    """INSERT ... ON CONFLICT DO NOTHING. Returns the number of new rows."""
    if not rows:
        return 0
    dialect = session.get_bind().dialect.name
    ins = {"postgresql": postgresql.insert, "sqlite": sqlite.insert}.get(dialect)
    if ins is None:
        raise RuntimeError(f"unsupported database dialect {dialect}")
    batch = INSERT_BATCH if dialect != "sqlite" else max(1, SQLITE_MAX_VARIABLES // len(rows[0]))
    inserted = 0
    for i in range(0, len(rows), batch):
        stmt = ins(Bar).values(rows[i:i + batch]).on_conflict_do_nothing(
            index_elements=["symbol", "timeframe", "time"]).returning(Bar.id)
        # count RETURNING rows: driver rowcount is -1 for batched inserts on psycopg
        inserted += len(session.execute(stmt).all())
    return inserted


def ingest_bars(session: Session, gateway: MT5Gateway, symbol: str, timeframe: Timeframe | str,
                start: datetime, end: datetime) -> IngestionRun:
    """Fetch [start, end] (UTC) and store usable bars. Always records an IngestionRun,
    including failures, so every download is auditable."""
    tf = Timeframe(timeframe)
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware UTC")
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    if end <= start:
        raise ValueError("end must be after start")
    end = min(end, datetime.now(timezone.utc))

    run = IngestionRun(symbol=symbol, timeframe=tf.value, requested_start=start, requested_end=end,
                       status="running")
    session.add(run)
    session.flush()

    try:
        gateway.get_symbol_info(symbol)  # raises if the broker does not list it
        acct = gateway.get_account_info()
        term = gateway.get_terminal_info()
        run.source = {
            "provider": "MetaTrader5",
            "server": acct.get("server"),
            "company": acct.get("company") or term.get("company"),
            "terminal_build": term.get("build"),
            "terminal_maxbars": term.get("maxbars"),
            "server_utc_offset_hours": gateway.settings.mt5_server_utc_offset_hours,
            "price_basis": "bid",
            "retrieved_at": utcnow().isoformat(),
        }
        minutes = TIMEFRAMES[tf].minutes
        frames = [gateway.get_historical_bars(symbol, tf, a, b) for a, b in _chunks(start, end, minutes)]
        frames = [f for f in frames if not f.empty]
        raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if not raw.empty:
            # drop the bar that is still forming: it closes after the retrieval time
            closes = pd.to_datetime(raw["time"], utc=True) + pd.Timedelta(minutes=minutes)
            forming = closes > pd.Timestamp(utcnow())
            run.source["forming_bars_dropped"] = int(forming.sum())
            raw = raw[~forming]
        df, report = clean_and_check(raw, minutes, start, end,
                                     gateway.settings.mt5_server_utc_offset_hours)
        run.bars_received = report.bars_received
        run.bars_valid = report.usable_bars
        run.quality = report.as_dict()

        rows = [{
            "symbol": symbol, "timeframe": tf.value, "time": r.time.to_pydatetime(),
            "open": float(r.open), "high": float(r.high), "low": float(r.low), "close": float(r.close),
            "tick_volume": int(r.tick_volume), "real_volume": int(r.real_volume),
            "spread_points": int(r.spread), "ingestion_run_id": run.id,
        } for r in df.itertuples(index=False)]
        run.bars_inserted = _insert_ignore(session, rows)
        run.bars_already_present = len(rows) - run.bars_inserted
        if len(df):
            run.first_bar = df["time"].iloc[0].to_pydatetime()
            run.last_bar = df["time"].iloc[-1].to_pydatetime()
        run.status = report.status
    except (MT5Error, ValueError) as exc:
        run.status = "failed"
        run.error = str(exc)
        log.warning("ingestion %s %s failed: %s", symbol, tf.value, exc)
    run.finished_at = utcnow()
    session.flush()
    return run


def load_bars(session: Session, symbol: str, timeframe: Timeframe | str,
              start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
    q = select(Bar.time, Bar.open, Bar.high, Bar.low, Bar.close, Bar.tick_volume,
               Bar.spread_points).where(Bar.symbol == symbol, Bar.timeframe == Timeframe(timeframe).value)
    if start:
        q = q.where(Bar.time >= start)
    if end:
        q = q.where(Bar.time <= end)
    rows = session.execute(q.order_by(Bar.time)).all()
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "tick_volume",
                                     "spread_points"])
    if not df.empty:
        df["time"] = pd.to_datetime(df["time"], utc=True)
    return df


def coverage(session: Session) -> list[dict]:
    q = (select(Bar.symbol, Bar.timeframe, func.count(), func.min(Bar.time), func.max(Bar.time))
         .group_by(Bar.symbol, Bar.timeframe).order_by(Bar.symbol, Bar.timeframe))
    return [{"symbol": s, "timeframe": tf, "bars": n, "first": a, "last": b}
            for s, tf, n, a, b in session.execute(q).all()]
