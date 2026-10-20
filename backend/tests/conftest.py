from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app import db as dbmod
from app.config import Settings
from app.models import Base
from app.mt5.gateway import MT5Gateway

from .fake_mt5 import FakeMT5


@event.listens_for(Engine, "connect")
def _emulate_windows_sqlite_limits(dbapi_conn, _record):
    """Python on Windows ships SQLite builds with as few as 999 variables per statement
    (Linux builds often allow 250,000). Test with the strict limit so bulk inserts that
    only work on Linux fail here too."""
    if isinstance(dbapi_conn, sqlite3.Connection):
        dbapi_conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, database_url=f"sqlite:///{tmp_path}/t.db",
                    api_token="test-token-123", mt5_password="hunter2-secret")


@pytest.fixture
def engine(settings):
    eng = dbmod.init_engine(settings.database_url)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    with dbmod.session_scope() as s:
        yield s


@pytest.fixture
def fake():
    return FakeMT5()


@pytest.fixture
def gateway(settings, fake):
    return MT5Gateway(settings, module=fake)
