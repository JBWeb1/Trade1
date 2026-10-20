"""Persist calendar events with retrieval timestamps; report history coverage."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from ...models import AuditLog, EconomicEventRow, utcnow
from .forexfactory import EconomicEvent, ParseReport


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _key(provider, t, ccy, title):
    return provider, _aware(t), ccy, title


def save_events(session: Session, events: list[EconomicEvent], report: ParseReport,
                source: str) -> dict:
    """Upsert events. `first_seen_at` is kept on updates: it records when this
    system first knew about an event, which bounds what a backtest may use."""
    now = utcnow()
    existing = set()
    if events:
        lo = min(e.event_time for e in events)
        hi = max(e.event_time for e in events)
        existing = {_key(*r) for r in session.execute(
            select(EconomicEventRow.provider, EconomicEventRow.event_time, EconomicEventRow.currency,
                   EconomicEventRow.title).where(EconomicEventRow.event_time.between(lo, hi)))}
    ins = {"postgresql": postgresql.insert, "sqlite": sqlite.insert}[session.get_bind().dialect.name]
    for e in events:
        stmt = ins(EconomicEventRow).values(
            provider=e.provider, event_time=e.event_time, currency=e.currency, title=e.title,
            impact=e.impact, forecast=e.forecast, previous=e.previous,
            first_seen_at=now, last_seen_at=now)
        session.execute(stmt.on_conflict_do_update(
            index_elements=["provider", "event_time", "currency", "title"],
            set_={"impact": e.impact, "forecast": e.forecast, "previous": e.previous,
                  "last_seen_at": now}))
    new = sum(1 for e in events if _key(e.provider, e.event_time, e.currency, e.title) not in existing)
    summary = {"source": source, "received": report.received, "accepted": report.accepted,
               "rejected": report.rejected, "new_events": new,
               "rejection_reasons": report.reasons[:5], "retrieved_at": now.isoformat()}
    session.add(AuditLog(actor="system", action="news.import", details=summary))
    return summary


def load_events(session: Session, start: datetime, end: datetime,
                currency: str | None = None, impact: str | None = None) -> list[EconomicEvent]:
    q = select(EconomicEventRow).where(EconomicEventRow.event_time.between(start, end))
    if currency:
        q = q.where(EconomicEventRow.currency == currency.upper())
    if impact:
        q = q.where(EconomicEventRow.impact == impact)
    return [EconomicEvent(r.provider, _aware(r.event_time), r.currency, r.title, r.impact,
                          r.forecast, r.previous)
            for r in session.scalars(q.order_by(EconomicEventRow.event_time))]


def coverage(session: Session, start: datetime, end: datetime) -> dict:
    """Which ISO weeks of [start, end] have stored events. A backtest must treat
    uncovered weeks as 'news filter unavailable', never as 'no news'."""
    times = session.scalars(select(EconomicEventRow.event_time)
                            .where(EconomicEventRow.event_time.between(start, end))).all()
    covered = {_aware(t).isocalendar()[:2] for t in times}
    weeks = set()
    cur = start
    while cur <= end:
        weeks.add(cur.isocalendar()[:2])
        cur += timedelta(days=7)
    weeks.add(end.isocalendar()[:2])
    hit = len(weeks & covered)
    return {"weeks_in_range": len(weeks), "weeks_with_events": hit,
            "fraction_covered": round(hit / len(weeks), 3) if weeks else 0.0}
