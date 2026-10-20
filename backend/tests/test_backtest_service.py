from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api import main as api
from app.api.deps import get_gateway
from app.backtest.service import run_backtest
from app.config import get_settings
from app.data.ingestion import ingest_bars
from app.markets.service import sync_instruments
from app.models import BacktestTrade, StrategyVersion
from app.mt5.gateway import MT5Gateway

from .test_no_lookahead import random_walk, resample_h4

START = datetime(2025, 1, 6, tzinfo=timezone.utc)
END = START + timedelta(hours=3000)
AUTH = {"Authorization": "Bearer test-token-123"}


def to_rates(df):
    out = df.copy()
    out["time"] = out["time"].map(lambda t: int(t.timestamp()))
    out["spread"] = 8
    out["real_volume"] = 0
    return out


@pytest.fixture
def seeded(session, gateway, fake):
    h1 = random_walk(3000, 60, seed=5)
    fake.set_bars("EURUSDm", fake.TIMEFRAME_H1, to_rates(h1))
    fake.set_bars("EURUSDm", fake.TIMEFRAME_H4, to_rates(resample_h4(h1).assign(tick_volume=1)))
    sync_instruments(session, gateway)
    assert ingest_bars(session, gateway, "EURUSDm", "H1", START, END).status in ("ok", "warning")
    assert ingest_bars(session, gateway, "EURUSDm", "H4", START, END).status in ("ok", "warning")
    session.flush()
    return session


def test_backtest_runs_and_persists(seeded):
    run = run_backtest(seeded, symbol="EURUSDm", timeframe="H1", htf="H4", start=START, end=END,
                       strategy_name="trend_continuation", params={"breakout_lookback": 5})
    assert run.status == "completed", run.error
    m = run.metrics
    assert m["closed_trades"] > 0
    stored = seeded.scalar(select(func.count()).select_from(BacktestTrade).where(BacktestTrade.run_id == run.id))
    assert stored == m["closed_trades"]
    assert "HISTORICAL BACKTEST" in m["label"] and m["notes"]
    assert run.data_fingerprint["primary"]["bars"] > 2900 and run.data_fingerprint["htf"]["bars"] > 0
    assert len(run.equity_curve) > 100

    again = run_backtest(seeded, symbol="EURUSDm", timeframe="H1", htf="H4", start=START, end=END,
                         strategy_name="trend_continuation", params={"breakout_lookback": 5})
    assert again.metrics["net_profit"] == m["net_profit"]
    assert again.strategy_version_id == run.strategy_version_id  # immutable version reused
    assert again.data_fingerprint == run.data_fingerprint
    assert seeded.scalar(select(func.count()).select_from(StrategyVersion)) == 1


def test_backtest_failures_are_recorded_not_raised(seeded):
    r = run_backtest(seeded, symbol="GBPUSDm", timeframe="H1", htf="H4", start=START, end=END,
                     strategy_name="trend_continuation")
    assert r.status == "failed" and "discover" in r.error
    r = run_backtest(seeded, symbol="EURUSDm", timeframe="M15", htf="H4", start=START, end=END,
                     strategy_name="trend_continuation")
    assert r.status == "failed" and "ingest" in r.error


def test_backtest_input_validation(seeded):
    with pytest.raises(ValueError):
        run_backtest(seeded, symbol="EURUSDm", timeframe="H4", htf="H1", start=START, end=END,
                     strategy_name="trend_continuation")
    with pytest.raises(ValueError):
        run_backtest(seeded, symbol="EURUSDm", timeframe="H1", htf=None, start=START, end=END,
                     strategy_name="trend_continuation")
    with pytest.raises(ValueError):
        run_backtest(seeded, symbol="EURUSDm", timeframe="H1", htf="H4", start=END, end=START,
                     strategy_name="trend_continuation")


def test_backtest_api(seeded, settings, fake):
    seeded.commit()
    gw = MT5Gateway(settings, module=fake)
    api.app.dependency_overrides[get_settings] = lambda: settings
    api.app.dependency_overrides[get_gateway] = lambda: gw
    body = {"symbol": "EURUSDm", "timeframe": "H1", "htf": "H4", "start": START.isoformat(),
            "end": END.isoformat(), "strategy": "trend_continuation", "params": {"breakout_lookback": 5},
            "config": {"risk": {"risk_per_trade_pct": 0.25}}}
    with TestClient(api.app) as c:
        assert c.post("/api/backtests", json=body).status_code == 401
        r = c.post("/api/backtests", json=body, headers=AUTH)
        assert r.status_code == 200 and r.json()["status"] == "completed"
        rid = r.json()["id"]
        assert c.get("/api/backtests", headers=AUTH).json()["items"][0]["id"] == rid
        detail = c.get(f"/api/backtests/{rid}", headers=AUTH).json()
        assert detail["equity_curve"] and detail["data_fingerprint"]["primary"]["bars"] > 0
        metrics = c.get(f"/api/backtests/{rid}/metrics", headers=AUTH).json()
        trades = c.get(f"/api/backtests/{rid}/trades", headers=AUTH).json()["items"]
        assert len(trades) == metrics["closed_trades"]
        assert c.get("/api/backtests/9999", headers=AUTH).status_code == 404

        strategies = c.get("/api/strategies", headers=AUTH).json()
        assert strategies["available"][0]["name"] == "trend_continuation"
        assert c.post("/api/strategies", json={"name": "trend_continuation", "params": {"fast_ema": 99}},
                      headers=AUTH).status_code == 422
        bad = dict(body, config={"risk": {"risk_per_trade_pct": 50}})
        assert c.post("/api/backtests", json=bad, headers=AUTH).status_code == 422
        bad = dict(body, start="2025-01-06T00:00:00")
        assert c.post("/api/backtests", json=bad, headers=AUTH).status_code == 422
    api.app.dependency_overrides.clear()
