"""Execution-model tests with hand-built bars and a scripted strategy.

Spec: point = tick_size = 0.0001, tick_value = 1.0 per lot  ->  1 pip = $1/lot.
"""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app.backtest.engine import BacktestConfig, BacktestEngine, CostModel, RiskModel, SpecError
from app.backtest.metrics import compute_metrics
from app.backtest.strategies.base import SignalIntent, Strategy, StrategyParams

T0 = datetime(2025, 3, 3, tzinfo=timezone.utc)  # Monday
SPEC = {"point": 0.0001, "tick_size": 0.0001, "tick_value": 1.0, "volume_min": 0.01,
        "volume_max": 100.0, "volume_step": 0.01, "contract_size": 100000, "swap_mode": "POINTS",
        "swap_long": -10.0, "swap_short": 2.0, "stops_level": 0}


def bars(ohlc, spread=0, start=T0, minutes=60):
    return pd.DataFrame([{"time": start + timedelta(minutes=minutes * k), "open": o, "high": h, "low": l,
                          "close": c, "tick_volume": 1, "spread_points": spread}
                         for k, (o, h, l, c) in enumerate(ohlc)])


class Scripted(Strategy):
    """Emits pre-set signals at given bar indices (test helper)."""
    name, version, Params = "scripted", "0", StrategyParams

    def __init__(self, signals: dict[int, tuple[str, float, float]], exits: dict[int, str] | None = None):
        super().__init__()
        self.signals, self.exits = signals, exits or {}

    def warmup_bars(self):
        return 0

    def prepare(self, primary, htf, pm, hm):
        self.df = primary.reset_index(drop=True)
        return self.df

    def validate_market_conditions(self, i):
        return True, ""

    def calculate_entry(self, i):
        return self.signals[i][0] if i in self.signals else None

    def calculate_stop_loss(self, i, side):
        return self.signals[i][1]

    def calculate_take_profit(self, i, side, stop):
        return self.signals[i][2]

    def calculate_exit(self, i, side):
        return self.exits.get(i)


def run(ohlc, signals, spread=0, exits=None, spec=None, **cfg):
    df = bars(ohlc, spread)
    s = Scripted(signals, exits)
    s.prepare(df, None, 60, None)
    config = BacktestConfig(**cfg)
    res = BacktestEngine(s, spec or SPEC, config, 60).run(df)
    return res, compute_metrics(res, config.starting_balance)


FLAT = (1.1000, 1.1005, 1.0995, 1.1000)


def test_long_fills_next_open_at_ask_and_hits_target():
    # signal at close of bar 0; bar 1 opens 1.1000 (ask 1.1002 with 2-pt spread)
    ohlc = [FLAT, FLAT, (1.1000, 1.1030, 1.0999, 1.1025), FLAT]
    res, m = run(ohlc, {0: ("buy", 1.0980, 1.1022)}, spread=2,
                 risk=RiskModel(risk_per_trade_pct=1.0, min_reward_risk=0.5))
    t = res.trades[0]
    assert t.entry_time == T0 + timedelta(hours=1)
    assert t.entry_price == pytest.approx(1.1002)
    assert t.exit_reason == "take profit" and t.exit_price == pytest.approx(1.1022)
    # risk $100 / 22 pips per lot -> 4.54 lots
    assert t.volume == pytest.approx(4.54)
    assert t.net_pnl == pytest.approx(20 * 4.54, rel=1e-6)
    assert t.spread_cost == pytest.approx(2 * 4.54, rel=1e-6)
    assert m["closed_trades"] == 1 and m["win_rate"] == 1.0


def test_stop_assumed_first_when_bar_hits_both():
    ohlc = [FLAT, FLAT, (1.1000, 1.1100, 1.0900, 1.1000)]
    res, _ = run(ohlc, {0: ("buy", 1.0950, 1.1080)})
    t = res.trades[0]
    assert t.exit_reason == "stop loss" and t.ambiguous_bar and t.exit_price == pytest.approx(1.0950)


def test_gap_through_stop_fills_at_open_not_at_stop():
    ohlc = [FLAT, FLAT, FLAT, (1.0900, 1.0910, 1.0890, 1.0905)]
    res, _ = run(ohlc, {0: ("buy", 1.0950, 1.1100)}, costs=CostModel(slippage_points=1))
    t = res.trades[0]
    assert t.exit_reason == "stop loss (gap)"
    assert t.exit_price == pytest.approx(1.0899)  # open minus 1 point slippage
    assert t.net_pnl < -t.planned_risk_money      # loss bigger than planned: realistic


def test_short_uses_ask_for_stops_and_pays_spread_on_exit():
    # short at bid 1.1000; stop 1.1010; bar high 1.1008 + spread 3pts = ask 1.1011 -> stopped
    ohlc = [FLAT, FLAT, (1.1000, 1.1008, 1.0995, 1.1000)]
    res, _ = run(ohlc, {0: ("sell", 1.1010, 1.0960)}, spread=3)
    t = res.trades[0]
    assert t.entry_price == pytest.approx(1.1000)
    assert t.exit_reason == "stop loss" and t.exit_price == pytest.approx(1.1010)
    assert t.spread_cost > 0


def test_commission_and_min_volume_rejection():
    res, _ = run([FLAT, FLAT, (1.1, 1.1030, 1.0999, 1.1)], {0: ("buy", 1.0980, 1.1030)},
                 costs=CostModel(commission_per_lot_per_side=3.5),
                 risk=RiskModel(risk_per_trade_pct=1.0))
    t = res.trades[0]
    # per lot risk = 20 pips + 7 commission = $27 -> 100/27 = 3.70 lots
    assert t.volume == pytest.approx(3.70) and t.commission == pytest.approx(7 * 3.70)

    tiny = dict(SPEC, volume_min=10.0, volume_step=1.0)
    res, m = run([FLAT, FLAT, FLAT], {0: ("buy", 1.0980, 1.1030)}, spec=tiny)
    assert res.trades == [] and m["rejections"] == {"minimum volume exceeds risk budget": 1}


def test_gap_invalidating_signal_and_degraded_rr_are_rejected():
    res, m = run([FLAT, (1.0970, 1.0975, 1.0960, 1.0970), FLAT], {0: ("buy", 1.0980, 1.1030)})
    assert res.trades == [] and "gap at open invalidated the signal" in m["rejections"]
    # opens near target: R:R collapses below 1.5
    res, m = run([FLAT, (1.1020, 1.1021, 1.1019, 1.1020), FLAT], {0: ("buy", 1.0980, 1.1030)})
    assert res.trades == [] and any("reward:risk" in k for k in m["rejections"])


def test_insufficient_margin_rejected():
    res, m = run([FLAT, FLAT, FLAT], {0: ("buy", 1.0999, 1.1100)}, leverage=1,
                 risk=RiskModel(risk_per_trade_pct=5))
    assert res.trades == [] and "insufficient margin" in m["rejections"]


def test_swap_charged_per_night_triple_on_wednesday():
    # 3 daily bars Tue->Wed->Thu->Fri ; long held over Tue, Wed(x3), Thu nights = 5 nights
    day = [FLAT] * 5
    df = bars(day, minutes=1440, start=datetime(2025, 3, 3, tzinfo=timezone.utc))
    s = Scripted({0: ("buy", 1.0900, 1.1500)})
    s.prepare(df, None, 1440, None)
    res = BacktestEngine(s, SPEC, BacktestConfig(), 1440).run(df)
    t = res.trades[0]
    assert t.exit_reason == "end of data"
    # entry Tue open; boundaries crossed: Tue->Wed(1), Wed->Thu(3), Thu->Fri(1) = 5 nights x 10 pts
    assert t.swap == pytest.approx(5 * 10 * t.volume)


def test_discretionary_exit_fills_next_open_and_time_stop():
    ohlc = [FLAT, FLAT, FLAT, (1.1003, 1.1006, 1.1001, 1.1004), FLAT]
    res, _ = run(ohlc, {0: ("buy", 1.0950, 1.1100)}, exits={2: "trend reversal"})
    assert res.trades[0].exit_reason == "trend reversal"
    assert res.trades[0].exit_time == T0 + timedelta(hours=3)
    assert res.trades[0].exit_price == pytest.approx(1.1003)

    res, _ = run([FLAT] * 6, {0: ("buy", 1.0950, 1.1100)}, risk=RiskModel(max_holding_bars=2))
    assert res.trades[0].exit_reason == "max holding time" and res.trades[0].bars_held == 2


def test_daily_loss_lockout_and_loss_cooldown():
    # two losing trades the same day with a 0.25% daily limit -> second signal rejected
    loss = (1.1000, 1.1001, 1.0940, 1.0950)
    ohlc = [FLAT, FLAT, loss, FLAT, FLAT, FLAT]
    res, m = run(ohlc, {0: ("buy", 1.0960, 1.1100), 3: ("buy", 1.0960, 1.1100)},
                 risk=RiskModel(risk_per_trade_pct=0.5, max_daily_loss_pct=0.25))
    assert len(res.trades) == 1 and m["rejections"].get("daily loss limit reached") == 1

    ohlc = [FLAT, FLAT, loss, FLAT, FLAT, FLAT]
    res, m = run(ohlc, {0: ("buy", 1.0960, 1.1100), 3: ("buy", 1.0960, 1.1100)},
                 risk=RiskModel(max_consecutive_losses=1, cooldown_bars_after_losses=10))
    assert m["rejections"].get("cooldown after consecutive losses") == 1


def test_incomplete_spec_refused():
    with pytest.raises(SpecError):
        BacktestEngine(Scripted({}), dict(SPEC, tick_value=0), BacktestConfig(), 60)


def test_equity_curve_marks_open_positions():
    ohlc = [FLAT, FLAT, (1.1000, 1.1010, 1.0990, 1.1010), (1.1010, 1.1011, 1.1009, 1.1010)]
    res, _ = run(ohlc, {0: ("buy", 1.0900, 1.1500)})
    eq = res.equity
    assert len(eq) == 4 and eq["time"].iloc[0] == T0 + timedelta(hours=1)  # marked at bar close
    assert eq["equity"].iloc[2] > eq["balance"].iloc[2]  # unrealised gain shows in equity only


def test_target_r_is_anchored_to_the_actual_fill():
    class RTarget(Scripted):
        def target_r(self):
            return 2.0
    df = bars([FLAT, FLAT, (1.1000, 1.1050, 1.0999, 1.1040)], spread=3)
    s = RTarget({0: ("buy", 1.0980, 1.1030)})
    s.prepare(df, None, 60, None)
    res = BacktestEngine(s, SPEC, BacktestConfig(), 60).run(df)
    t = res.trades[0]
    # fill at ask 1.1003, risk 23 pips -> target 1.1003 + 46 pips = 1.1049 (not the 1.1030 at signal time)
    assert t.entry_price == pytest.approx(1.1003)
    assert t.take_profit == pytest.approx(1.1049) and t.exit_reason == "take profit"
    assert t.bars_held == 2


def _with_atr(df, calm=0.0010, wild=0.0040, wild_from=None):
    df = df.copy()
    df["atr"] = calm
    if wild_from is not None:
        df.loc[wild_from:, "atr"] = wild
    return df


def test_volatility_sizing_shrinks_positions_when_market_is_wild():
    ohlc = [FLAT] * 120 + [FLAT, FLAT, (1.1000, 1.1030, 1.0999, 1.1025), FLAT]
    base = {"risk_per_trade_pct": 1.0, "min_reward_risk": 0.5}

    def vol_for(volatility_sizing, wild_from):
        df = _with_atr(bars(ohlc), wild_from=wild_from)
        s = Scripted({120: ("buy", 1.0980, 1.1022)})
        s.prepare(df, None, 60, None)
        cfg = BacktestConfig(risk=RiskModel(**base, volatility_sizing=volatility_sizing,
                                            vol_lookback_bars=100, vol_min_scale=0.4))
        res = BacktestEngine(s, SPEC, cfg, 60).run(df)
        return res.trades[0].volume, res.notes

    normal, _ = vol_for(False, None)
    calm, _ = vol_for(True, None)
    wild, notes = vol_for(True, 118)  # ATR jumps 4x just before the signal
    assert calm == pytest.approx(normal)
    assert wild == pytest.approx(round(normal * 0.4, 2), abs=0.011)
    assert any("volatility sizing" in n for n in notes)


def test_news_blackout_blocks_signals_only_where_calendar_exists():
    from app.integrations.news.filter import NewsBlackout
    from app.integrations.news.forexfactory import EconomicEvent
    spec = dict(SPEC, category="forex", currency_base="EUR", currency_profit="USD")
    ohlc = [FLAT, FLAT, FLAT, FLAT]
    decision = T0 + timedelta(hours=1)  # close of bar 0
    event = EconomicEvent("forexfactory", decision + timedelta(minutes=10), "USD", "CPI", "high")
    news = NewsBlackout([event], 30, 30)

    def run_news(weeks):
        df = bars(ohlc)
        s = Scripted({0: ("buy", 1.0950, 1.1100)})
        s.prepare(df, None, 60, None)
        return BacktestEngine(s, spec, BacktestConfig(), 60).run(df, news=news, news_weeks=weeks)

    blocked = run_news({decision.isocalendar()[:2]})
    assert blocked.trades == [] and blocked.rejections[0].reason == "news blackout"
    unknown = run_news(set())  # no calendar stored for that week: not applied, and said so
    assert len(unknown.trades) == 1 and any("news blackout not applied" in n for n in unknown.notes)
