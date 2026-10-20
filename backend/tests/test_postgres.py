"""Runs only when TRADE1_TEST_PG_URL points at a disposable PostgreSQL database."""
import os
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app import db as dbmod
from app.data.ingestion import ingest_bars, load_bars

from .fake_mt5 import make_rates

PG = os.getenv("TRADE1_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not PG, reason="TRADE1_TEST_PG_URL not set")
MON = datetime(2025, 3, 3, tzinfo=timezone.utc)


@pytest.fixture
def pg():
    eng = create_engine(PG)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
    cfg.set_main_option("sqlalchemy.url", PG)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    dbmod.init_engine(PG)
    yield
    eng.dispose()


def test_migrations_and_idempotent_ingest_on_postgres(pg, gateway, fake):
    fake.set_bars("EURUSDm", fake.TIMEFRAME_H1, make_rates(MON, 72, 60))
    for expected_new in (72, 0):
        with dbmod.session_scope() as s:
            run = ingest_bars(s, gateway, "EURUSDm", "H1", MON, MON + timedelta(days=3))
            assert run.bars_inserted == expected_new
    with dbmod.session_scope() as s:
        df = load_bars(s, "EURUSDm", "H1")
    assert len(df) == 72 and df["time"].iloc[0] == MON
