from datetime import datetime, timedelta, timezone

import pytest

from trade1.broker import PaperBroker
from trade1.config import Settings, SymbolMap
from trade1.engine import TradingEngine
from trade1.models import Bar, Side, Signal, SymbolInfo
from trade1.quiver import QuiverBias, _congress_direction, _insider_direction, score_rows
from trade1.risk import RiskConfig, RiskManager
from trade1.strategy import LiquiditySweepStrategy, StrategyConfig, swing_lows

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_bars(closes, wick=0.5):
    bars = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        bars.append(Bar(T0 + timedelta(hours=i), o, max(o, c) + wick, min(o, c) - wick, c, 1000))
    return bars


def sweep_low_series():
    """Range around 100 with a clear swing low at 97, then a bar that wicks to 95 and closes 99."""
    bars = make_bars([100 + (i % 4) * 0.5 for i in range(70)])
    dip = [99.0, 98.5, 98.0, 97.0, 98.0, 98.5, 99.0]  # swing low 97 at index 53
    for k, lo in enumerate(dip):
        i = 50 + k
        bars[i] = Bar(bars[i].time, lo + 0.8, lo + 1.2, lo, lo + 0.6, 1000)
    sweep = Bar(T0 + timedelta(hours=70), 99.6, 99.9, 95.0, 99.0, 1500)
    return bars + [sweep]


def test_swing_lows_found():
    bars = sweep_low_series()[:-1]
    lows = [lvl for _, lvl in swing_lows(bars, 3)]
    assert any(abs(l - 97.0) < 1e-9 for l in lows)


def test_bullish_sweep_signal():
    strat = LiquiditySweepStrategy(StrategyConfig(bias_mode="ignore"))
    sig = strat.evaluate("AAPL", sweep_low_series())
    assert sig is not None
    assert sig.side is Side.BUY
    assert sig.stop < 95.0 < sig.entry < sig.target


def test_bias_require_blocks_and_allows():
    strat = LiquiditySweepStrategy(StrategyConfig(bias_mode="require", bias_threshold=0.2))
    bars = sweep_low_series()
    assert strat.evaluate("AAPL", bars, bias=0.0) is None
    assert strat.evaluate("AAPL", bars, bias=-0.5) is None
    assert strat.evaluate("AAPL", bars, bias=0.5).side is Side.BUY


def test_bearish_sweep_signal():
    # mirror the bullish series around 100
    bull = sweep_low_series()
    bear = [Bar(b.time, 200 - b.open, 200 - b.low, 200 - b.high, 200 - b.close, b.volume) for b in bull]
    sig = LiquiditySweepStrategy(StrategyConfig(bias_mode="ignore")).evaluate("X", bear)
    assert sig is not None and sig.side is Side.SELL
    assert sig.target < sig.entry < sig.stop


def test_no_signal_in_flat_market():
    bars = make_bars([100 + (i % 4) * 0.5 for i in range(80)])
    assert LiquiditySweepStrategy(StrategyConfig(bias_mode="ignore")).evaluate("X", bars) is None


def test_position_size_respects_risk_and_steps():
    rm = RiskManager(RiskConfig(risk_per_trade_pct=1.0))
    info = SymbolInfo("AAPL", 0.01, 0.01, 0.01, 0.1, 100, 0.1)  # 1 lot = 1 share
    sig = Signal("AAPL", Side.BUY, 100.0, 98.0, 104.0)
    vol = rm.position_size(sig, info, 10_000)  # risk $100 / $2 per share = 50
    assert vol == pytest.approx(50.0)
    # too small -> 0 rather than over-risking
    assert rm.position_size(sig, SymbolInfo("X", 0.01, 0.01, 0.01, 100, 1000, 1), 10_000) == 0


def test_daily_loss_and_limits():
    rm = RiskManager(RiskConfig(max_daily_loss_pct=2.0, max_open_positions=1))
    rm.start_of_day(T0.date(), 10_000)
    info = SymbolInfo("A", 0.01, 0.01, 0.01, 0.01, 100, 0.01)
    sig = Signal("A", Side.BUY, 100, 99, 102)
    assert rm.can_open(sig, [], 10_000, 0.02, info)[0]
    assert not rm.can_open(sig, [], 9_790, 0.02, info)[0]
    assert not rm.can_open(sig, [], 10_000, 1.0, info)[0]  # 100-point spread


def test_quiver_scoring():
    now = datetime.now(timezone.utc)
    recent = now.strftime("%Y-%m-%d")
    old = (now - timedelta(days=90)).strftime("%Y-%m-%d")
    congress = [
        {"Ticker": "AAPL", "Transaction": "Purchase", "TransactionDate": recent},
        {"Ticker": "AAPL", "Transaction": "Purchase", "TransactionDate": recent},
        {"Ticker": "AAPL", "Transaction": "Sale (Full)", "TransactionDate": recent},
        {"Ticker": "AAPL", "Transaction": "Sale", "TransactionDate": old},
    ]
    insiders = [{"Ticker": "MSFT", "TransactionCode": "S", "Date": now.isoformat()}]

    class FakeClient:
        def congress_trading(self):
            return congress

        def insider_trading(self):
            return insiders

    bias = QuiverBias(FakeClient(), lookback_days=30)
    scores = bias.refresh(force=True)
    assert scores["AAPL"].buys == 2 and scores["AAPL"].sells == 1
    assert scores["AAPL"].score == pytest.approx(1 / 3)
    assert scores["MSFT"].score == -1
    assert bias.get("TSLA").score == 0
    assert _congress_direction({"Transaction": "Exchange"}) == 0
    assert _insider_direction({"TransactionCode": "A"}) == 0
    assert score_rows([{"Ticker": ""}], _congress_direction, ("Date",), now) == {}


def test_engine_places_paper_trade_once_per_bar():
    s = Settings(symbols=[SymbolMap("AAPL", "AAPL")],
                 strategy=StrategyConfig(bias_mode="require"))
    broker = PaperBroker(10_000, default_info=SymbolInfo("AAPL", 0.01, 0.01, 0.01, 0.01, 100, 0.01))
    broker.load_bars("AAPL", sweep_low_series())

    class FixedBias:
        def refresh(self, force=False):
            return {}

        def get(self, t):
            from trade1.quiver import BiasScore
            return BiasScore(t, 0.8, 4, 0)

    eng = TradingEngine(s, broker, FixedBias())
    eng.step()
    eng.step()  # same bar -> no duplicate
    assert len(broker.orders) == 1
    sig, vol = broker.orders[0]
    assert sig.side is Side.BUY and vol > 0


def test_paper_position_closes_at_target():
    broker = PaperBroker(10_000, default_info=SymbolInfo("A", 0.01, 0.01, 0.01, 0.01, 100, 0.01))
    broker.load_bars("A", make_bars([100]))
    broker.market_order(Signal("A", Side.BUY, 100, 98, 104), 10)
    broker.load_bars("A", make_bars([100, 105]))
    assert broker.positions() == []
    assert broker.equity() == pytest.approx(10_040)  # (104-100) * 10 shares
