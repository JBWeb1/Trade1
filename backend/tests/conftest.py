from __future__ import annotations

import pytest

from app import db as dbmod
from app.config import Settings
from app.models import Base
from app.mt5.gateway import MT5Gateway

from .fake_mt5 import FakeMT5


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
