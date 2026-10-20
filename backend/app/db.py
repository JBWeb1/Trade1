from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def init_engine(url: str, **kwargs) -> Engine:
    global _engine, _factory
    if url.startswith("sqlite"):
        # Windows SQLite builds may allow only 999 variables per statement; keep ORM bulk
        # inserts (e.g. backtest trades, ~25 columns) under that.
        kwargs.setdefault("insertmanyvalues_page_size", 30)
    _engine = create_engine(url, pool_pre_ping=True, future=True, **kwargs)
    _factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        from .config import get_settings
        init_engine(get_settings().database_url)
    return _engine  # type: ignore[return-value]


@contextmanager
def session_scope() -> Iterator[Session]:
    """One transaction: commit on success, roll back on any error."""
    get_engine()
    session = _factory()  # type: ignore[misc]
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as s:
        yield s
