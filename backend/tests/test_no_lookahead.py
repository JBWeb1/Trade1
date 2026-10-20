from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.backtest.alignment import align_higher_timeframe
from app.backtest.engine import BacktestConfig, BacktestEngine
from app.backtest.metrics import compute_metrics
from app.backtest.strategies import get_strategy

T0 = datetime(2025, 1, 6, tzinfo=timezone.utc)


def random_walk(n, minutes, seed=7, start=T0):
    """Synthetic prices for engine tests only - never shown as market data."""
    rng = np.random.default_rng(seed)
    close = 1.1 + np.cumsum(rng.normal(0, 0.0008, n))
    open_ = np.concatenate([[1.1], close[:-1]])
    spread = np.abs(rng.normal(0, 0.0006, n))
    return pd.DataFrame({
        "time": [start + timedelta(minutes=minutes * k) for k in range(n)],
        "open": open_, "high": np.maximum(open_, close) + spread, "low": np.minimum(open_, close) - spread,
        "close": close, "tick_volume": 100, "spread_points": 8})


def resample_h4(h1: pd.DataFrame) -> pd.DataFrame:
    g = h1.set_index("time").resample("4h", origin="epoch")
    return pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                         "close": g["close"].last()}).dropna().reset_index()


def test_htf_bar_only_visible_after_it_closes():
    h1 = random_walk(24, 60)
    h4 = resample_h4(h1)
    out = align_higher_timeframe(h1, 60, h4, 240, ["close"])
    by_time = out.set_index("time")
    # H1 bars 00:00..02:00 decide at 01:00..03:00 -> no H4 bar closed yet
    assert by_time.loc[T0, "htf_close"] != by_time.loc[T0, "htf_close"]  # NaN
    assert np.isnan(by_time.loc[T0 + timedelta(hours=2), "htf_close"])
    # H1 bar 03:00 decides at 04:00 -> the 00:00 H4 bar has just closed
    assert by_time.loc[T0 + timedelta(hours=3), "htf_close"] == h4["close"].iloc[0]
    # H1 bar 05:00 (decides 06:00) still sees the 00:00 H4 bar, NOT the forming 04:00 bar
    assert by_time.loc[T0 + timedelta(hours=5), "htf_close"] == h4["close"].iloc[0]
    assert (out["htf_close_time"].dropna() <= out["time"][out["htf_close_time"].notna()]
            + timedelta(hours=1)).all()


def test_htf_shorter_than_primary_rejected():
    with pytest.raises(ValueError):
        align_higher_timeframe(random_walk(10, 240), 240, random_walk(10, 60), 60, ["close"])


def _signals(h1, h4):
    s = get_strategy("trend_continuation", {"breakout_lookback": 5})
    s.prepare(h1, h4, 60, 240)
    out = {}
    for i in range(s.warmup_bars(), len(s.df)):
        sig = s.generate_signal(i)
        if sig:
            out[s.df["time"].iat[i]] = (sig.side, round(sig.stop_loss, 10), round(sig.take_profit, 10))
    return out


def test_truncating_the_future_never_changes_past_signals():
    h1 = random_walk(1500, 60)
    full = _signals(h1, resample_h4(h1))
    assert len(full) > 10, "test needs signals to be meaningful"
    for cut in (400, 777, 1200):
        part_h1 = h1.iloc[:cut]
        # H4 built only from data that existed up to the cut (forming H4 bar excluded)
        cut_time = part_h1["time"].iloc[-1] + timedelta(hours=1)
        h4 = resample_h4(part_h1)
        h4 = h4[h4["time"] + timedelta(hours=4) <= cut_time]
        part = _signals(part_h1, h4)
        expected = {t: v for t, v in full.items() if t <= part_h1["time"].iloc[-1]}
        assert part == expected


def test_backtest_is_reproducible():
    h1 = random_walk(2000, 60, seed=11)
    h4 = resample_h4(h1)
    spec = {"point": 0.00001, "tick_size": 0.00001, "tick_value": 1.0, "volume_min": 0.01,
            "volume_max": 100, "volume_step": 0.01, "swap_mode": "DISABLED"}

    def once():
        s = get_strategy("trend_continuation", {"breakout_lookback": 5})
        df = s.prepare(h1, h4, 60, 240)
        res = BacktestEngine(s, spec, BacktestConfig(), 60).run(df)
        return [t.as_dict() for t in res.trades], compute_metrics(res, 10_000)

    a, b = once(), once()
    assert a == b and a[1]["closed_trades"] > 0


def test_strategy_params_validated_and_hash_stable():
    with pytest.raises(ValueError):
        get_strategy("trend_continuation", {"fast_ema": 60, "slow_ema": 50})
    with pytest.raises(ValueError):
        get_strategy("trend_continuation", {"unknown_param": 1})
    with pytest.raises(ValueError):
        get_strategy("nope")
    a = get_strategy("trend_continuation", {"fast_ema": 10})
    b = get_strategy("trend_continuation", {"fast_ema": 10})
    c = get_strategy("trend_continuation", {"fast_ema": 12})
    assert a.params_hash() == b.params_hash() != c.params_hash()


@pytest.mark.parametrize("name,params", [
    ("range_breakout", {"range_bars": 12, "max_range_atr": 8}),
    ("liquidity_sweep", {"lookback": 40}),
    ("trend_continuation", {"breakout_lookback": 5, "use_htf_filter": False, "regime_filter": "trending"}),
    ("trend_pullback", {"slow_ema": 50, "touch_atr": 0.5}),
])
def test_all_strategies_are_truncation_invariant(name, params):
    h1 = random_walk(1200, 60, seed=3)
    _check_truncation(name, params, h1, 60, (500, 901))


@pytest.mark.parametrize("name,params", [
    ("session_open_breakout", {"session": "london"}),
    ("session_open_breakout", {"session": "new_york", "opening_minutes": 30}),
    ("asian_range_breakout", {"max_range_atr": 20}),
])
def test_session_strategies_are_truncation_invariant(name, params):
    m15 = random_walk(96 * 60, 15, seed=8)  # 60 days of M15, crosses the March DST change
    _check_truncation(name, params, m15, 15, (2000, 3333, 5000))


def _check_truncation(name, params, h1, minutes, cuts):

    def sigs(df):
        s = get_strategy(name, params)
        s.instrument = {"point": 0.00001}
        s.prepare(df, None, minutes, None)
        out = {}
        for i in range(s.warmup_bars(), len(s.df)):
            sig = s.generate_signal(i)
            if sig:
                out[s.df["time"].iat[i]] = (sig.side, round(sig.stop_loss, 10), sig.data_used.get("regime"))
        return out

    full = sigs(h1)
    assert len(full) >= 5, f"{name} produced too few signals for a meaningful test"
    for cut in cuts:
        part = sigs(h1.iloc[:cut])
        assert part == {t: v for t, v in full.items() if t <= h1["time"].iloc[cut - 1]}


def test_regime_labels_are_causal_and_known():
    from app.backtest.indicators import atr
    from app.backtest.regime import classify
    h1 = random_walk(800, 60, seed=4)
    full = classify(h1, atr(h1, 14))
    part = classify(h1.iloc[:500], atr(h1.iloc[:500], 14))
    assert (full.iloc[:500] == part).all()
    assert set(full.unique()) <= {"trending", "ranging", "high_volatility", "unknown"}
    assert {"trending", "ranging"} <= set(full.unique())


def test_session_times_follow_daylight_saving():
    """London open is 08:00 UTC in winter and 07:00 UTC in summer (BST)."""
    from app.backtest.strategies.session import SessionOpenBreakout
    s = SessionOpenBreakout({"session": "london", "opening_minutes": 60})
    winter = random_walk(96 * 3, 15, start=datetime(2025, 1, 6, tzinfo=timezone.utc))
    summer = random_walk(96 * 3, 15, start=datetime(2025, 7, 7, tzinfo=timezone.utc))
    for data, utc_hour in ((winter, 8), (summer, 7)):
        df = s.prepare(data, None, 15, None)
        first_range_bar = df[df["in_range"]]["time"].iloc[0]
        assert first_range_bar.hour == utc_hour


def test_session_strategy_rejects_slow_timeframes():
    with pytest.raises(ValueError):
        get_strategy("asian_range_breakout").prepare(random_walk(100, 240), None, 240, None)
