"""Trend continuation (research candidate A).

Rules, all evaluated at the close of primary bar i:
  * Higher-timeframe filter: last CLOSED HTF bar's close above (below) its EMA.
  * Primary trend: fast EMA above (below) slow EMA.
  * Momentum confirmation: close breaks above the highest high (below the
    lowest low) of the previous `breakout_lookback` bars.
  * Volatility filter: ATR / close within [min_atr_pct, max_atr_pct].
  * Stop: `stop_atr` x ATR beyond the close. Target: `reward_risk` x risk.
  * Discretionary exit: fast/slow EMA cross against the position.
This is a hypothesis to test, not a strategy known to be profitable.
"""
from __future__ import annotations

import math

import pandas as pd
from pydantic import Field, model_validator

from ..alignment import align_higher_timeframe
from ..indicators import atr, ema, rolling_high, rolling_low
from .base import Strategy, StrategyParams


class TrendParams(StrategyParams):
    fast_ema: int = Field(20, ge=2, le=500)
    slow_ema: int = Field(50, ge=3, le=1000)
    htf_ema: int = Field(50, ge=2, le=1000)
    use_htf_filter: bool = True
    breakout_lookback: int = Field(10, ge=2, le=500)
    atr_period: int = Field(14, ge=2, le=500)
    stop_atr: float = Field(1.5, gt=0, le=20)
    reward_risk: float = Field(1.5, ge=0.5, le=20)
    min_atr_pct: float = Field(0.0002, ge=0)
    max_atr_pct: float = Field(0.05, gt=0)
    max_htf_staleness_hours: float = Field(80, gt=0)  # covers weekends

    @model_validator(mode="after")
    def _check(self):
        if self.fast_ema >= self.slow_ema:
            raise ValueError("fast_ema must be < slow_ema")
        if self.min_atr_pct >= self.max_atr_pct:
            raise ValueError("min_atr_pct must be < max_atr_pct")
        return self


class TrendContinuation(Strategy):
    name = "trend_continuation"
    version = "1.0.0"
    Params = TrendParams
    description = "EMA trend + HTF filter + breakout momentum, ATR stop, fixed R:R target"
    uses_higher_timeframe = True

    def warmup_bars(self) -> int:
        p = self.params
        return max(p.slow_ema, p.breakout_lookback, p.atr_period) + 1

    def prepare(self, primary, htf, primary_minutes, htf_minutes):
        p = self.params
        df = primary.copy().reset_index(drop=True)
        df["ema_fast"] = ema(df["close"], p.fast_ema)
        df["ema_slow"] = ema(df["close"], p.slow_ema)
        df["atr"] = atr(df, p.atr_period)
        df["hh"] = rolling_high(df["high"], p.breakout_lookback)
        df["ll"] = rolling_low(df["low"], p.breakout_lookback)
        if p.use_htf_filter:
            if htf is None or htf_minutes is None:
                raise ValueError("trend_continuation needs higher-timeframe bars when use_htf_filter=True")
            h = htf.copy()
            h["ema"] = ema(h["close"], p.htf_ema)
            df = align_higher_timeframe(df, primary_minutes, h, htf_minutes, ["close", "ema"])
            df["htf_age_h"] = ((df["time"] + pd.Timedelta(minutes=primary_minutes) - df["htf_close_time"])
                               .dt.total_seconds() / 3600)
        self.df = df
        return df

    def _v(self, col: str, i: int) -> float:
        return float(self.df[col].iat[i])

    def validate_market_conditions(self, i):
        p = self.params
        a, c = self._v("atr", i), self._v("close", i)
        if any(math.isnan(x) for x in (a, self._v("ema_slow", i), self._v("hh", i))):
            return False, "indicators warming up"
        pct = a / c
        if not p.min_atr_pct <= pct <= p.max_atr_pct:
            return False, f"ATR% {pct:.5f} outside range"
        if p.use_htf_filter:
            if math.isnan(self._v("htf_ema", i)):
                return False, "higher-timeframe data unavailable"
            if self._v("htf_age_h", i) > p.max_htf_staleness_hours:
                return False, "higher-timeframe data stale"
        return True, ""

    def _htf_dir(self, i) -> int:
        if not self.params.use_htf_filter:
            return 0
        return 1 if self._v("htf_close", i) > self._v("htf_ema", i) else -1

    def calculate_entry(self, i):
        c = self._v("close", i)
        up = self._v("ema_fast", i) > self._v("ema_slow", i)
        htf = self._htf_dir(i)
        if up and htf >= 0 and c > self._v("hh", i) and c > self._v("ema_fast", i):
            return "buy"
        if not up and htf <= 0 and c < self._v("ll", i) and c < self._v("ema_fast", i):
            return "sell"
        return None

    def calculate_stop_loss(self, i, side):
        c, a = self._v("close", i), self._v("atr", i)
        return c - self.params.stop_atr * a if side == "buy" else c + self.params.stop_atr * a

    def calculate_take_profit(self, i, side, stop):
        c = self._v("close", i)
        r = abs(c - stop) * self.params.reward_risk
        return c + r if side == "buy" else c - r

    def target_r(self):
        return self.params.reward_risk

    def calculate_exit(self, i, side):
        up = self._v("ema_fast", i) > self._v("ema_slow", i)
        if side == "buy" and not up:
            return "trend reversal: fast EMA crossed below slow EMA"
        if side == "sell" and up:
            return "trend reversal: fast EMA crossed above slow EMA"
        return None

    def describe(self, i, side):
        data = {k: round(self._v(k, i), 6) for k in ("close", "ema_fast", "ema_slow", "atr", "hh", "ll")}
        if self.params.use_htf_filter:
            data.update(htf_close=self._v("htf_close", i), htf_ema=round(self._v("htf_ema", i), 6))
        word = "above" if side == "buy" else "below"
        rationale = (f"{side}: fast EMA {word} slow EMA, close broke {self.params.breakout_lookback}-bar "
                     f"{'high' if side == 'buy' else 'low'}"
                     + (f", HTF close {word} HTF EMA" if self.params.use_htf_filter else ""))
        invalidation = f"stop at {self.params.stop_atr}xATR or EMA cross against the trade"
        return rationale, invalidation, data
