"""Persistent execution state (enable flag, emergency stop, daily equity anchors)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AppSetting, ReconciliationEvent, utcnow

DEMO_STATE = "demo.state"
EMERGENCY = "system.emergency_stop"
EQUITY = "demo.equity"


def get(session: Session, key: str) -> dict:
    row = session.get(AppSetting, key)
    return dict(row.value) if row else {}


def put(session: Session, key: str, value: dict) -> None:
    row = session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    session.flush()


def demo_state(session: Session) -> dict:
    return get(session, DEMO_STATE)


def emergency_active(session: Session) -> bool:
    return bool(get(session, EMERGENCY).get("active"))


def set_emergency(session: Session, active: bool, reason: str, actor: str) -> None:
    put(session, EMERGENCY, {"active": active, "reason": reason, "actor": actor, "at": utcnow().isoformat()})


def unresolved_reconciliation(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(ReconciliationEvent)
                          .where(ReconciliationEvent.resolved.is_(False))) or 0


def update_equity_anchors(session: Session, equity: float, now: datetime | None = None) -> dict:
    """Track the day's starting equity (UTC day) and the running peak for loss limits."""
    now = now or datetime.now(timezone.utc)
    st = get(session, EQUITY)
    today = now.date().isoformat()
    if st.get("day") != today:
        st["day"], st["day_start_equity"] = today, equity
    st["peak_equity"] = max(float(st.get("peak_equity", equity)), equity)
    st["last_equity"] = equity
    put(session, EQUITY, st)
    return st
