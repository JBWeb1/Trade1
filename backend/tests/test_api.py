from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api import main as api
from app.api.deps import get_gateway
from app.config import Settings, get_settings
from app.mt5.gateway import MT5Gateway

from .fake_mt5 import FakeMT5, account, make_rates

MON = datetime(2025, 3, 3, tzinfo=timezone.utc)
AUTH = {"Authorization": "Bearer test-token-123"}


@pytest.fixture
def client(settings, engine, fake):
    gw = MT5Gateway(settings, module=fake)
    api.app.dependency_overrides[get_settings] = lambda: settings
    api.app.dependency_overrides[get_gateway] = lambda: gw
    with TestClient(api.app) as c:
        yield c
    api.app.dependency_overrides.clear()


def test_health_is_public_and_reports_mode(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["database"] is True and body["execution_mode"] == "BACKTEST"


@pytest.mark.parametrize("path", ["/api/mt5/status", "/api/mt5/account", "/api/markets",
                                  "/api/market-data/runs", "/api/diagnostics"])
def test_protected_routes_require_token(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get(path, headers=AUTH).status_code == 200


def test_routes_fail_closed_without_configured_token(client, settings):
    no_token = Settings(_env_file=None, database_url=settings.database_url)
    api.app.dependency_overrides[get_settings] = lambda: no_token
    assert client.get("/api/markets", headers=AUTH).status_code == 503


def test_account_endpoint_reports_verification_without_personal_data(client):
    body = client.get("/api/mt5/account", headers=AUTH).json()
    assert body["verification"]["verified_demo"] is True
    assert "name" not in body["account"]
    assert "hunter2-secret" not in str(body)


def test_live_account_shows_not_verified(settings, engine):
    gw = MT5Gateway(settings, module=FakeMT5(acct=account(trade_mode=2, server="Exness-MT5Real8")))
    api.app.dependency_overrides[get_settings] = lambda: settings
    api.app.dependency_overrides[get_gateway] = lambda: gw
    with TestClient(api.app) as c:
        v = c.get("/api/mt5/status", headers=AUTH).json()["verification"]
    api.app.dependency_overrides.clear()
    assert v["verified_demo"] is False and v["account_mode"] == "REAL"


def test_sync_list_and_detail(client):
    assert client.get("/api/markets/EURUSDm", headers=AUTH).status_code == 404
    assert client.post("/api/markets/sync", headers=AUTH).json()["discovered"] == 1
    items = client.get("/api/markets?category=forex", headers=AUTH).json()["items"]
    assert [i["symbol"] for i in items] == ["EURUSDm"]
    assert client.get("/api/markets?category=crypto", headers=AUTH).json()["items"] == []
    assert client.get("/api/markets/EURUSDm", headers=AUTH).json()["items"][0]["metadata_ok"]


def test_ingest_and_read_back(client, fake):
    fake.set_bars("EURUSDm", fake.TIMEFRAME_H1, make_rates(MON, 48, 60))
    body = {"symbol": "EURUSDm", "timeframe": "H1", "start": MON.isoformat(),
            "end": (MON + timedelta(days=2)).isoformat()}
    r = client.post("/api/market-data/ingest", json=body, headers=AUTH)
    assert r.status_code == 200 and r.json()["bars_inserted"] == 48
    data = client.get("/api/market-data", params={"symbol": "EURUSDm", "timeframe": "H1"},
                      headers=AUTH).json()
    assert data["count"] == 48 and data["bars"][0]["time"].startswith("2025-03-03T00:00:00")
    cov = client.get("/api/market-data/coverage", headers=AUTH).json()["items"]
    assert cov[0]["bars"] == 48


@pytest.mark.parametrize("body", [
    {"symbol": "EURUSDm", "timeframe": "D1", "start": "2025-01-01T00:00:00Z", "end": "2025-01-02T00:00:00Z"},
    {"symbol": "EURUSDm", "timeframe": "H1", "start": "2025-01-01T00:00:00", "end": "2025-01-02T00:00:00"},
    {"symbol": "EURUSDm", "timeframe": "H1", "start": "2025-01-02T00:00:00Z", "end": "2025-01-01T00:00:00Z"},
    {"symbol": "", "timeframe": "H1", "start": "2025-01-01T00:00:00Z", "end": "2025-01-02T00:00:00Z"},
    {"symbol": "EURUSDm", "timeframe": "M1", "start": "2020-01-01T00:00:00Z", "end": "2025-01-02T00:00:00Z"},
])
def test_ingest_validation(client, body):
    assert client.post("/api/market-data/ingest", json=body, headers=AUTH).status_code == 422


def test_no_route_can_send_orders():
    for route in api.app.routes:
        path = getattr(route, "path", "")
        assert "order" not in path.lower() and "trade" not in path.lower(), path


def test_application_code_never_calls_order_send():
    """Until the Phase E demo gateway exists, no module may reference order_send."""
    import pathlib
    root = pathlib.Path(api.__file__).resolve().parents[1]
    offenders = [str(p) for p in root.rglob("*.py") if "order_send" in p.read_text()]
    assert offenders == []
