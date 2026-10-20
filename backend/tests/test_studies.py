from datetime import datetime, timedelta, timezone

import pytest

from app.backtest.portfolio import LegTrade, PortfolioLimits, exposure_of, replay
from app.backtest.research import monte_carlo
from app.backtest.service import run_backtest
from app.backtest.studies import _first_touch, run_multi_market, run_portfolio, run_tick_check
from app.data.ingestion import ingest_bars
from app.markets.service import sync_instruments
from app.mt5.gateway import MT5Gateway

from .fake_mt5 import FakeMT5, symbol
from .test_backtest_service import to_rates
from .test_no_lookahead import random_walk, resample_h4

UTC = timezone.utc
START = datetime(2025, 1, 6, tzinfo=UTC)
END = START + timedelta(hours=3000)


# ---------------------------------------------------------------- Monte Carlo

def test_monte_carlo_is_reproducible_and_sane():
    rs = [1.5, -1, -1, 1.5, -1, 2, -1, -1, 1.5, -1] * 5
    a, b = monte_carlo(rs, 1.0), monte_carlo(rs, 1.0)
    assert a == b  # fixed seed
    assert a["max_drawdown_pct"]["p50"] <= a["max_drawdown_pct"]["p95"] <= a["max_drawdown_pct"]["p99"]
    assert a["final_return_pct"]["p5"] <= a["final_return_pct"]["p50"] <= a["final_return_pct"]["p95"]
    assert monte_carlo([1.0] * 20, 1.0)["probability_of_loss"] == 0
    assert monte_carlo([-1.0] * 20, 1.0)["probability_of_loss"] == 1
    assert monte_carlo([1.0] * 5, 1.0) is None  # too few trades


# ---------------------------------------------------------------- portfolio replay

T = datetime(2025, 3, 3, 8, tzinfo=UTC)
GOLD = {"symbol": "XAUUSDm", "category": "metals", "currency_base": "XAU", "currency_profit": "USD"}
EUR = {"symbol": "EURUSDm", "category": "forex", "currency_base": "EUR", "currency_profit": "USD"}
GBP = {"symbol": "GBPUSDm", "category": "forex", "currency_base": "GBP", "currency_profit": "USD"}
US30 = {"symbol": "US30m", "category": "indices", "currency_base": "USD", "currency_profit": "USD"}


def lt(spec, side, start_h, end_h, r):
    return LegTrade("leg", spec["symbol"], side, T + timedelta(hours=start_h), T + timedelta(hours=end_h), r,
                    exposure_of(spec, side))


def test_currency_exposure():
    assert exposure_of(GOLD, "buy") == {"XAU": 1, "USD": -1}
    assert exposure_of(EUR, "sell") == {"EUR": -1, "USD": 1}
    assert exposure_of(US30, "buy") == {"US30m": 1}


def test_correlation_limit_blocks_stacked_dollar_bets():
    trades = [lt(GOLD, "buy", 0, 5, 1), lt(EUR, "buy", 1, 5, 1), lt(GBP, "buy", 2, 5, 1),  # 3rd short-USD bet
              lt(GBP, "sell", 3, 5, 1)]  # opposite direction is allowed
    res = replay(trades, PortfolioLimits(max_currency_exposure=2, max_open_positions=5, max_total_open_risk_pct=5))
    assert res["accepted"] == 3 and res["rejected"] == {"currency exposure (correlation) limit": 1}


def test_open_position_and_total_risk_limits():
    trades = [lt(US30, "buy", 0, 5, 1), lt(GOLD, "buy", 1, 5, 1), lt(EUR, "sell", 2, 5, 1)]
    res = replay(trades, PortfolioLimits(max_open_positions=2, max_total_open_risk_pct=5))
    assert res["rejected"] == {"max open positions": 1}
    res = replay(trades, PortfolioLimits(max_open_positions=5, risk_per_trade_pct=0.5, max_total_open_risk_pct=1.0))
    assert res["rejected"] == {"max total open risk": 1}


def test_trade_opening_and_closing_in_the_same_bar_is_closed():
    trades = [lt(US30, "buy", 0, 0, -1), lt(GOLD, "buy", 0, 0, 1), lt(EUR, "buy", 1, 2, 1)]
    res = replay(trades, PortfolioLimits(max_open_positions=1, max_total_open_risk_pct=5))
    assert res["accepted"] == 2 and res["rejected"] == {"max open positions": 1}  # 2nd blocked only while 1st is open
    res = replay(trades, PortfolioLimits(max_open_positions=1, max_total_open_risk_pct=5))
    assert res["accepted"] + sum(res["rejected"].values()) == res["signals"]


def test_daily_loss_limit_and_compounding():
    trades = [lt(US30, "buy", 0, 1, -1), lt(GOLD, "buy", 2, 3, -1), lt(EUR, "buy", 4, 5, 2)]
    res = replay(trades, PortfolioLimits(risk_per_trade_pct=1.0, max_daily_loss_pct=1.5))
    assert res["accepted"] == 2 and res["rejected"] == {"daily loss limit": 1}
    assert res["final_equity"] == pytest.approx(10_000 * 0.99 * 0.99, abs=0.01)


# ---------------------------------------------------------------- service-level studies

@pytest.fixture
def multi(session, settings):
    specs = [symbol(), symbol(name="GBPUSDm", base="GBP", path="Forex\\Majors\\GBPUSDm"),
             symbol(name="XAUUSDm", base="XAU", path="Metals\\XAUUSDm", digits=3, point=0.001,
                    trade_tick_size=0.001, trade_tick_value=0.1, trade_calc_mode=3)]
    fake = FakeMT5(symbols=specs)
    gw = MT5Gateway(settings, module=fake)
    for k, s in enumerate(specs):
        h1 = random_walk(3000, 60, seed=20 + k)
        if s.name == "XAUUSDm":
            for c in ("open", "high", "low", "close"):
                h1[c] = h1[c] * 2000
        fake.set_bars(s.name, fake.TIMEFRAME_H1, to_rates(h1))
        fake.set_bars(s.name, fake.TIMEFRAME_H4, to_rates(resample_h4(h1).assign(tick_volume=1)))
    sync_instruments(session, gw)
    for s in specs:
        for tf in ("H1", "H4"):
            assert ingest_bars(session, gw, s.name, tf, START, END).status in ("ok", "warning")
    session.flush()
    return session, gw, fake


def test_multi_market_study(multi):
    session, _, _ = multi
    rep = run_multi_market(session, symbols=["EURUSDm", "GBPUSDm", "XAUUSDm", "NOPE"], timeframe="H1", htf="H4",
                           start=START, end=END, strategy_name="trend_continuation",
                           params={"breakout_lookback": 5}, min_trades=10)
    assert rep.status == "completed"
    rows = {r["symbol"]: r for r in rep.result["symbols"]}
    assert "error" in rows["NOPE"] and rows["EURUSDm"]["closed_trades"] > 0
    assert rep.result["verdict"] in ("CONSISTENT", "INCONSISTENT", "INSUFFICIENT")
    single = run_multi_market(session, symbols=["EURUSDm"], timeframe="H1", start=START, end=END,
                              strategy_name="range_breakout")
    assert single.status == "failed"


def test_portfolio_study(multi):
    session, _, _ = multi
    legs = [{"symbol": "EURUSDm", "timeframe": "H1", "strategy": "range_breakout", "params": {"max_range_atr": 8}},
            {"symbol": "XAUUSDm", "timeframe": "H1", "strategy": "liquidity_sweep"},
            {"symbol": "GBPUSDm", "timeframe": "H1", "htf": "H4", "strategy": "trend_continuation"},
            {"symbol": "NOPE", "timeframe": "H1", "strategy": "range_breakout"}]
    rep = run_portfolio(session, legs=legs, start=START, end=END,
                        limits=PortfolioLimits(max_open_positions=2, max_currency_exposure=1))
    assert rep.status == "completed", rep.error
    r = rep.result
    assert r["accepted"] + sum(r["rejected"].values()) == r["signals"]
    assert any("error" in leg for leg in r["legs"])
    assert r["rejected"], "tight limits should reject some trades"


def test_tick_check(multi):
    session, gw, fake = multi
    run = run_backtest(session, symbol="EURUSDm", timeframe="H1", htf="H4", start=START, end=END,
                       strategy_name="trend_continuation", params={"breakout_lookback": 5})
    from sqlalchemy import select

    from app.models import BacktestTrade
    t = session.scalars(select(BacktestTrade).where(BacktestTrade.run_id == run.id,
                                                    BacktestTrade.exit_reason == "stop loss")).first()
    bar = t.exit_time.replace(tzinfo=UTC) if t.exit_time.tzinfo is None else t.exit_time
    # ticks say the TARGET was reached first inside that bar
    if t.side == "buy":
        ticks = [(bar + timedelta(minutes=1), t.take_profit + 0.0001, t.take_profit + 0.0002),
                 (bar + timedelta(minutes=2), t.stop_loss - 0.0001, t.stop_loss)]
    else:
        ticks = [(bar + timedelta(minutes=1), t.take_profit - 0.0003, t.take_profit - 0.0001),
                 (bar + timedelta(minutes=2), t.stop_loss, t.stop_loss + 0.0001)]
    fake.set_ticks("EURUSDm", ticks)
    rep = run_tick_check(session, gw, backtest_run_id=run.id, max_trades=500)
    assert rep.status == "completed"
    c = rep.result["counts"]
    assert c["flipped_to_target"] == 1 and c["no_ticks_available"] == c["checked"] - 1
    assert rep.result["r_change_if_ticks_are_right"] > 0
    assert run_tick_check(session, gw, backtest_run_id=99999).status == "failed"


def test_first_touch():
    import pandas as pd
    ticks = pd.DataFrame({"bid": [1.0, 1.2, 0.8], "ask": [1.01, 1.21, 0.81]})
    assert _first_touch(ticks, "buy", sl=0.9, tp=1.1) == "target"
    assert _first_touch(ticks, "sell", sl=1.1, tp=0.9) == "stop"
    assert _first_touch(ticks.iloc[:1], "buy", sl=0.9, tp=1.1) is None


def test_research_journal_audit_settings_api(multi, settings):
    from fastapi.testclient import TestClient

    from app.api import main as api
    from app.api.deps import get_gateway
    from app.config import get_settings
    session, gw, _ = multi
    run = run_backtest(session, symbol="EURUSDm", timeframe="H1", htf="H4", start=START, end=END,
                       strategy_name="trend_continuation", params={"breakout_lookback": 5})
    session.commit()
    api.app.dependency_overrides[get_settings] = lambda: settings
    api.app.dependency_overrides[get_gateway] = lambda: gw
    auth = {"Authorization": "Bearer test-token-123"}
    with TestClient(api.app) as c:
        body = {"symbols": ["EURUSDm", "GBPUSDm"], "timeframe": "H1", "htf": "H4", "start": START.isoformat(),
                "end": END.isoformat(), "strategy": "trend_continuation", "min_trades": 5}
        r = c.post("/api/research/multi-market", json=body, headers=auth)
        assert r.status_code == 200 and r.json()["kind"] == "multi_market"
        assert c.post("/api/research/multi-market", json=dict(body, start="2025-01-06T00:00:00"),
                      headers=auth).status_code == 422
        assert c.post("/api/research/multi-market", json=dict(body, symbols=["EURUSDm"]), headers=auth).status_code == 422
        p = c.post("/api/research/portfolio", json={
            "legs": [{"symbol": "EURUSDm", "timeframe": "H1", "strategy": "range_breakout"},
                     {"symbol": "GBPUSDm", "timeframe": "H1", "strategy": "liquidity_sweep"}],
            "start": START.isoformat(), "end": END.isoformat(), "limits": {"max_open_positions": 2}}, headers=auth)
        assert p.status_code == 200 and p.json()["kind"] == "portfolio", p.text
        assert c.post("/api/research/portfolio", json={"legs": [], "start": START.isoformat(), "end": END.isoformat()},
                      headers=auth).status_code == 422
        tc = c.post(f"/api/research/tick-check/{run.id}", headers=auth)
        assert tc.status_code == 200 and tc.json()["kind"] == "tick_check"
        items = c.get("/api/research", headers=auth).json()["items"]
        assert {i["kind"] for i in items} >= {"multi_market", "portfolio", "tick_check"}
        assert c.get(f"/api/research/{items[0]['id']}", headers=auth).json()["result"] is not None

        csv_text = c.get(f"/api/backtests/{run.id}/journal", headers=auth)
        assert csv_text.status_code == 200 and csv_text.headers["content-type"].startswith("text/csv")
        lines = csv_text.text.strip().splitlines()
        assert lines[0].startswith("source,result_kind") and len(lines) == run.metrics["closed_trades"] + 1
        js = c.get(f"/api/backtests/{run.id}/journal?format=json", headers=auth).json()
        assert js["context"]["result_kind"] == "historical_backtest" and len(js["trades"]) == run.metrics["closed_trades"]

        audit = c.get("/api/audit?action=research", headers=auth).json()["items"]
        assert audit and all(a["action"].startswith("research") for a in audit)
        st = c.get("/api/settings", headers=auth).json()
        assert st["api_token"] == "set" and st["mt5"]["password"] == "set"
        assert "hunter2" not in str(st) and "test-token" not in str(st)
        assert c.get("/api/settings").status_code == 401
    api.app.dependency_overrides.clear()


def test_sqlite_backup_with_rotation(tmp_path):
    import sqlite3

    from app.backup import backup_database
    from contextlib import closing
    db = tmp_path / "t.db"
    with closing(sqlite3.connect(db)) as c:
        c.execute("create table x (v int)")
        c.execute("insert into x values (42)")
        c.commit()
    outs = []
    for k in range(4):
        import time
        outs.append(backup_database(f"sqlite:///{db}", tmp_path / "backups", keep=2))
        time.sleep(1.05)  # distinct timestamps
    kept = sorted((tmp_path / "backups").glob("trade1_*.db"))
    assert len(kept) == 2 and kept[-1] == outs[-1]
    with closing(sqlite3.connect(kept[-1])) as c:
        assert c.execute("select v from x").fetchone() == (42,)
    with pytest.raises(FileNotFoundError):
        backup_database(f"sqlite:///{tmp_path}/missing.db", tmp_path / "b")
