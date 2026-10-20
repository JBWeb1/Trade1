"""Demo execution safety tests. Uses a fake MT5; no real broker is ever contacted."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import ExecutionMode, Settings
from app.execution import state
from app.execution.control import CONFIRMATION, enable_demo
from app.execution.gateway import DemoExecutionGateway
from app.markets.service import sync_instruments
from app.models import (AuditLog, BacktestRun, IngestionRun, ReconciliationEvent, StrategyVersion,
                        ValidationReport)
from app.mt5.gateway import MT5Gateway

from .fake_mt5 import account, symbol
from .fake_trading import FakeTradingMT5, Order

UTC = timezone.utc


@pytest.fixture
def demo_settings(settings):
    return settings.model_copy(update={"execution_mode": ExecutionMode.DEMO})


@pytest.fixture
def fake():
    return FakeTradingMT5(symbols=[symbol()])


@pytest.fixture
def ex(session, demo_settings, fake):
    gw = MT5Gateway(demo_settings, module=fake)
    sync_instruments(session, gw)
    state.put(session, state.DEMO_STATE, {"enabled": True, "login": 12345678, "server": "Exness-MT5Trial7"})
    return DemoExecutionGateway(demo_settings, gw)


def buy(ex, session, key="k1", volume=0.10, sl=1.0950, tp=1.1100):
    return ex.open_position(session, key=key, symbol="EURUSDm", side="buy", volume=volume,
                            stop_loss=sl, take_profit=tp)


# ------------------------------------------------------------------ happy path / idempotency

def test_fill_is_verified_against_the_open_position(ex, session, fake):
    i = buy(ex, session)
    assert i.status == "filled" and i.position_ticket == fake.positions[0].ticket
    assert fake.sent[0]["magic"] == ex.settings.demo_magic and fake.sent[0]["comment"] == f"T1#{i.id}"
    assert i.account["trade_mode"] == 0 and all(c["passed"] for c in i.checks)
    assert session.scalar(select(AuditLog).where(AuditLog.action == "order.filled")) is not None


def test_same_signal_never_sends_twice(ex, session, fake):
    a = buy(ex, session, key="dup")
    b = buy(ex, session, key="dup")
    assert a.id == b.id and len(fake.sent) == 1


# ------------------------------------------------------------------ live / non-demo protection

@pytest.mark.parametrize("acct", [account(trade_mode=2, server="Exness-MT5Real8"), account(trade_mode=2),
                                  account(trade_mode=1), account(server="Exness-MT5Real3")])
def test_non_demo_accounts_can_never_receive_orders(session, demo_settings, acct):
    fake = FakeTradingMT5(symbols=[symbol()], acct=acct)
    gw = MT5Gateway(demo_settings, module=fake)
    sync_instruments(session, gw)
    state.put(session, state.DEMO_STATE, {"enabled": True, "login": acct.login, "server": acct.server})
    i = buy(DemoExecutionGateway(demo_settings, gw), session)
    assert i.status == "rejected" and fake.sent == []
    assert "DEMO" in i.reason


def test_account_switch_at_send_time_is_refused_and_halts(ex, session, fake, monkeypatch):
    original = ex._order_checks

    def checks_then_switch(*a, **kw):  # pre-flight sees DEMO; the account changes right before sending
        out = original(*a, **kw)
        fake.account_switch_after = fake._account_calls
        return out
    monkeypatch.setattr(ex, "_order_checks", checks_then_switch)
    i = buy(ex, session)
    assert i.status == "rejected" and "not DEMO" in i.reason and fake.sent == []
    assert state.emergency_active(session)


def test_other_demo_account_than_enabled_is_refused(ex, session, fake):
    state.put(session, state.DEMO_STATE, {"enabled": True, "login": 999, "server": "Exness-MT5Trial7"})
    i = buy(ex, session)
    assert i.status == "rejected" and "same account" in i.reason and fake.sent == []


@pytest.mark.parametrize("mode", [ExecutionMode.BACKTEST, ExecutionMode.RESEARCH, ExecutionMode.PAPER])
def test_execution_mode_must_be_demo_on_the_server(session, settings, fake, mode):
    s = settings.model_copy(update={"execution_mode": mode})
    gw = MT5Gateway(s, module=fake)
    sync_instruments(session, gw)
    state.put(session, state.DEMO_STATE, {"enabled": True, "login": 12345678, "server": "Exness-MT5Trial7"})
    i = buy(DemoExecutionGateway(s, gw), session)
    assert i.status == "rejected" and fake.sent == []


def test_disabled_or_emergency_blocks_orders(ex, session, fake):
    state.put(session, state.DEMO_STATE, {"enabled": False, "login": 12345678, "server": "Exness-MT5Trial7"})
    assert buy(ex, session, key="a").status == "rejected"
    state.put(session, state.DEMO_STATE, {"enabled": True, "login": 12345678, "server": "Exness-MT5Trial7"})
    state.set_emergency(session, True, "test", "me")
    assert buy(ex, session, key="b").status == "rejected"
    assert fake.sent == []


# ------------------------------------------------------------------ uncertain outcomes: never retry

@pytest.mark.parametrize("mode", ["none", "raise", "fill_no_position"])
def test_unknown_outcome_is_uncertain_blocks_and_is_not_retried(ex, session, fake, mode):
    fake.send_mode = mode
    i = buy(ex, session, key="u1")
    assert i.status == "uncertain" and len(fake.sent) == 1
    assert session.scalar(select(ReconciliationEvent).where(ReconciliationEvent.resolved.is_(False))) is not None
    fake.send_mode = "fill"
    j = buy(ex, session, key="u2")  # a NEW signal is blocked until reconciliation
    assert j.status == "rejected" and "reconciliation" in j.reason and len(fake.sent) == 1
    assert buy(ex, session, key="u1").id == i.id and len(fake.sent) == 1  # the same one is never resent


def test_lost_reply_is_reconciled_from_broker_state(ex, session, fake):
    fake.send_mode = "fill_but_none"
    i = buy(ex, session)
    assert i.status == "filled"  # reply lost, but the position was found by its tag
    fake.positions = []  # first position closed by its target, say
    fake.send_mode = "none"
    j = buy(ex, session, key="k2", volume=0.2)  # this one really did not open
    assert j.status == "uncertain"
    out = ex.reconcile(session)
    assert j.id in out["resolved"] and j.status == "reconciled_not_filled"
    assert state.unresolved_reconciliation(session) == 0


def test_broker_rejection_is_final_and_not_retried(ex, session, fake):
    fake.send_mode = "reject"
    i = buy(ex, session)
    assert i.status == "rejected" and "10006" in i.reason and len(fake.sent) == 1
    assert state.unresolved_reconciliation(session) == 0


def test_missing_stop_loss_after_fill_raises_a_blocking_event(ex, session, fake):
    fake.send_mode = "fill_no_sl"
    i = buy(ex, session)
    assert i.status == "filled"
    ev = session.scalar(select(ReconciliationEvent).where(ReconciliationEvent.kind == "stop_or_target_missing"))
    assert ev is not None and not ev.resolved


# ------------------------------------------------------------------ pre-flight risk checks

def test_stale_quote_wide_spread_bad_volume_and_wrong_side_stops(ex, session, fake):
    fake.symbols["EURUSDm"] = fake.symbols["EURUSDm"]._replace(
        time=int((datetime.now(UTC) - timedelta(minutes=10)).timestamp()))
    assert "fresh quote" in buy(ex, session, key="s1").reason
    fake.symbols["EURUSDm"] = fake.symbols["EURUSDm"]._replace(time=int(datetime.now(UTC).timestamp()), ask=1.1050)
    assert "spread" in buy(ex, session, key="s2").reason
    fake.symbols["EURUSDm"] = fake.symbols["EURUSDm"]._replace(ask=1.1001)
    assert "volume" in buy(ex, session, key="s3", volume=0.015).reason
    assert "correct side" in buy(ex, session, key="s4", sl=1.1050).reason
    assert fake.sent == []


def test_position_and_loss_limits(ex, session, fake, demo_settings):
    assert buy(ex, session, key="p1").status == "filled"
    assert "no open position on this symbol" in buy(ex, session, key="p2").reason
    fake.positions = []
    state.put(session, state.EQUITY, {"day": datetime.now(UTC).date().isoformat(), "day_start_equity": 10_200,
                                      "peak_equity": 10_200})
    r = buy(ex, session, key="p3")  # equity 10,000 vs 10,200 at day start = -1.96%
    assert r.status == "rejected" and "daily loss limit" in r.reason


def test_flagged_instrument_is_refused(session, demo_settings):
    fake = FakeTradingMT5(symbols=[symbol(trade_tick_value=0)])
    gw = MT5Gateway(demo_settings, module=fake)
    sync_instruments(session, gw)
    state.put(session, state.DEMO_STATE, {"enabled": True, "login": 12345678, "server": "Exness-MT5Trial7"})
    i = buy(DemoExecutionGateway(demo_settings, gw), session)
    assert i.status == "rejected" and "metadata" in i.reason and fake.sent == []


# ------------------------------------------------------------------ close, emergency stop, reconcile

def test_close_position_and_emergency_stop(ex, session, fake):
    i = buy(ex, session)
    c = ex.close_position(session, key="c1", ticket=i.position_ticket, reason="trend reversal")
    assert c.status == "filled" and fake.positions == []
    fake.pending = [Order(77, "EURUSDm", 2, ex.settings.demo_magic, 0.1, 1.09, "T1"),
                    Order(78, "EURUSDm", 2, 999, 0.1, 1.09, "manual order - not ours")]
    out = ex.emergency_stop(session, "test", "me")
    assert out["cancelled_orders"] == [77] and [o.ticket for o in fake.pending] == [78]
    assert state.emergency_active(session)
    assert buy(ex, session, key="after").status == "rejected"


def test_reconcile_flags_positions_trade1_does_not_know(ex, session, fake):
    fake.positions.append(fake.positions[0]._replace(ticket=999, magic=ex.settings.demo_magic, comment="?")
                          if fake.positions else
                          __import__("tests.fake_trading", fromlist=["Position"]).Position(
                              999, 0, 0, ex.settings.demo_magic, 999, 0.1, 1.1, 1.09, 1.11, 1.1, 0, 0, "EURUSDm", "?"))
    out = ex.reconcile(session)
    assert out["unknown_positions"] == [999]
    ex.reconcile(session)  # does not duplicate the event
    assert state.unresolved_reconciliation(session) == 1


# ------------------------------------------------------------------ the enable gate

_n = iter(range(10**6))


def _passed_report(session, checks_ok=True, oos_uses=0):
    v = StrategyVersion(name="trend_continuation", version="1.1.0", params={}, params_hash=f"h{next(_n)}")
    session.add(v)
    session.flush()
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    bt = BacktestRun(strategy_version_id=v.id, symbol="EURUSDm", timeframe="H1", start=t0, end=t0, config={},
                     status="completed")
    session.add(bt)
    session.add(IngestionRun(symbol="EURUSDm", timeframe="H1", requested_start=t0, requested_end=t0, status="ok"))
    session.flush()
    checks = [{"check": "still profitable with stressed costs", "passed": checks_ok, "detail": "", "kind": "robustness"},
              {"check": "walk-forward consistency", "passed": True, "detail": "", "kind": "robustness"}]
    rep = ValidationReport(strategy_version_id=v.id, backtest_run_id=bt.id, symbol="EURUSDm", timeframe="H1",
                           start=t0, end=t0, config={}, oos_key="k", oos_prior_uses=oos_uses, status="completed",
                           verdict="PASSED" if checks_ok else "FAILED", report={"checks": checks, "segments": {}})
    session.add(rep)
    session.flush()
    return rep


def test_enable_gate(session, demo_settings, fake):
    gw = MT5Gateway(demo_settings, module=fake)
    sync_instruments(session, gw)
    bad = _passed_report(session, checks_ok=False)
    assert not enable_demo(session, demo_settings, gw, report_id=bad.id, confirmation=CONFIRMATION, actor="t")["enabled"]
    good = _passed_report(session)
    assert not enable_demo(session, demo_settings, gw, report_id=good.id, confirmation="yes", actor="t")["enabled"]
    backtest_mode = demo_settings.model_copy(update={"execution_mode": ExecutionMode.BACKTEST})
    assert not enable_demo(session, backtest_mode, gw, report_id=good.id, confirmation=CONFIRMATION, actor="t")["enabled"]
    reused = _passed_report(session, oos_uses=1)
    assert not enable_demo(session, demo_settings, gw, report_id=reused.id, confirmation=CONFIRMATION, actor="t")["enabled"]
    res = enable_demo(session, demo_settings, gw, report_id=good.id, confirmation=CONFIRMATION, actor="t")
    assert res["enabled"], [r for r in res["requirements"] if not r["passed"]]
    assert state.demo_state(session)["login"] == 12345678


def test_enable_gate_refuses_live_account(session, demo_settings):
    fake = FakeTradingMT5(symbols=[symbol()], acct=account(trade_mode=2, server="Exness-MT5Real8"))
    gw = MT5Gateway(demo_settings, module=fake)
    sync_instruments(session, gw)
    good = _passed_report(session)
    res = enable_demo(session, demo_settings, gw, report_id=good.id, confirmation=CONFIRMATION, actor="t")
    assert not res["enabled"]
    assert any(r["requirement"].startswith("terminal account") and not r["passed"] for r in res["requirements"])


# ------------------------------------------------------------------ API

def test_demo_api_routes(session, demo_settings, fake):
    from fastapi.testclient import TestClient

    from app.api import main as api
    from app.api.deps import get_gateway
    from app.config import get_settings
    gw = MT5Gateway(demo_settings, module=FakeTradingMT5(symbols=[symbol(trade_tick_value=0)]))
    sync_instruments(session, gw)
    session.commit()
    api.app.dependency_overrides[get_settings] = lambda: demo_settings
    api.app.dependency_overrides[get_gateway] = lambda: gw
    auth = {"Authorization": "Bearer test-token-123"}
    with TestClient(api.app) as c:
        st = c.get("/api/demo/status", headers=auth).json()
        assert st["execution_mode"] == "DEMO" and st["confirmation_text"] == CONFIRMATION
        r = c.post("/api/demo/enable", json={"report_id": 1, "confirmation": CONFIRMATION}, headers=auth)
        assert r.status_code == 409
        assert c.post("/api/demo/enable", json={"report_id": 1, "confirmation": CONFIRMATION}).status_code == 401
        e = c.post("/api/system/emergency-stop", json={"reason": "test"}, headers=auth)
        assert e.status_code == 200 and e.json()["active"] is True
        assert c.post("/api/system/emergency-reset", json={"confirmation": "no"}, headers=auth).status_code == 422
        assert c.post("/api/system/emergency-reset", json={"confirmation": "RESET EMERGENCY STOP"},
                      headers=auth).json()["active"] is False
    api.app.dependency_overrides.clear()


# ------------------------------------------------------------------ runner, daily report, alerts

def _recent_h1(fake, n=400):
    from .fake_mt5 import make_rates
    now = datetime.now(UTC)
    start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=n - 1)
    fake.set_bars("EURUSDm", fake.TIMEFRAME_H1, make_rates(start, n, 60))  # last bar is still forming


def test_runner_trades_each_closed_bar_once(ex, session, fake, monkeypatch):
    from app.backtest import strategies as reg
    from app.backtest.strategies.base import Strategy, StrategyParams
    from app.execution.runner import run_once

    class AlwaysBuy(Strategy):
        name, version, Params = "always_buy_test", "0", StrategyParams

        def warmup_bars(self):
            return 5

        def prepare(self, primary, htf, pm, hm):
            self.df = primary.reset_index(drop=True)
            return self.df

        def validate_market_conditions(self, i):
            return True, ""

        def calculate_entry(self, i):
            return "buy" if i == len(self.df) - 1 else None

        def calculate_stop_loss(self, i, side):
            return float(self.df["close"].iat[i]) - 0.0050

        def calculate_take_profit(self, i, side, stop):
            return float(self.df["close"].iat[i]) + 0.0100

    monkeypatch.setitem(reg.REGISTRY, "always_buy_test", AlwaysBuy)
    _recent_h1(fake)
    state.put(session, state.DEMO_STATE, {"enabled": True, "login": 12345678, "server": "Exness-MT5Trial7",
                                          "strategy": "always_buy_test", "params": {}, "strategy_version_id": None,
                                          "symbol": "EURUSDm", "timeframe": "H1", "htf": None, "risk_config": {}})
    first = run_once(session, ex.settings, ex.gw)
    assert first["action"] == "open" and first["status"] == "filled", first
    second = run_once(session, ex.settings, ex.gw)
    assert second["reason"] == "no new closed bar" and len(fake.sent) == 1
    # the forming bar was never used: the signal bar closed at or before now
    bar = datetime.fromisoformat(first["bar"])
    assert bar + timedelta(hours=1) <= datetime.now(UTC)
    st = state.demo_state(session)
    assert st["last_cycle"]["action"] == "open"


def test_runner_idles_when_disabled_or_halted(ex, session):
    from app.execution.runner import run_once
    state.put(session, state.DEMO_STATE, {"enabled": False})
    assert run_once(session, ex.settings, ex.gw)["action"] == "idle"
    state.put(session, state.DEMO_STATE, {"enabled": True})
    state.set_emergency(session, True, "x", "me")
    assert run_once(session, ex.settings, ex.gw)["reason"] == "emergency stop active"


def test_daily_report_compares_with_validation(ex, session, fake):
    from app.execution.report import build_daily_report, format_report
    i = buy(ex, session)
    i.signal = {"planned_risk_money": 50.0}
    now = datetime.now(UTC)
    fake.deals = [{"magic": ex.settings.demo_magic, "position_id": i.position_ticket, "symbol": "EURUSDm",
                   "entry": 0, "profit": 0, "commission": -0.5, "swap": 0, "fee": 0},
                  {"magic": ex.settings.demo_magic, "position_id": i.position_ticket, "symbol": "EURUSDm",
                   "entry": 1, "profit": 75.5, "commission": -0.5, "swap": 0, "fee": 0},
                  {"magic": 1, "position_id": 1, "symbol": "EURUSDm", "entry": 1, "profit": -999}]  # not ours
    state.put(session, state.DEMO_STATE, {"enabled": True, "login": 12345678, "server": "Exness-MT5Trial7",
                                          "symbol": "EURUSDm", "strategy": "x", "enabled_at": (now - timedelta(days=3)).isoformat(),
                                          "expectation": {"closed_trades": 30, "win_rate": 0.4, "expectancy_r": 0.2,
                                                          "first_bar_close": (now - timedelta(days=60)).isoformat(),
                                                          "last_bar_close": now.isoformat()}})
    r = build_daily_report(session, ex.settings, ex.gw, now)
    assert r["today"]["closed_trades"] == 1 and r["today"]["net"] == 74.5
    assert r["since_enabled"]["expectancy_r"] == pytest.approx(74.5 / 50)
    assert r["expected_from_validation"]["trades_by_now"] == 1.5
    text = format_report(r)
    assert "expected" in text and "EURUSDm" in text


def test_telegram_alerts_are_optional_and_never_raise(settings):
    import httpx

    from app.alerts import notify
    assert notify(settings, "hello") is False  # not configured
    from pydantic import SecretStr
    s = settings.model_copy(update={"telegram_bot_token": SecretStr("123:SECRET-TOKEN"), "telegram_chat_id": "42"})
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True})
    assert notify(s, "filled", "fill", transport=httpx.MockTransport(handler)) is True
    assert seen[0].url.path == "/bot123:SECRET-TOKEN/sendMessage" and b"DEMO" in seen[0].content
    assert notify(s, "x", transport=httpx.MockTransport(lambda r: httpx.Response(500))) is False

    def boom(request):
        raise httpx.ConnectError("down")
    assert notify(s, "x", transport=httpx.MockTransport(boom)) is False
    from app.logging_setup import redact
    assert "SECRET-TOKEN" not in redact("url https://api.telegram.org/bot123:SECRET-TOKEN/sendMessage")
