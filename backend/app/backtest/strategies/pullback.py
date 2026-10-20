"""Trend pullback (research candidate C).

Rules at the close of bar i:
  * Trend: close and fast EMA above (below) the slow EMA, and the slow EMA rising
    (falling) over `slope_bars` bars.
  * Pullback: within the last `pullback_bars` bars (including i), price traded back
    to within `touch_atr` x ATR of the fast EMA, without closing beyond the slow EMA.
  * Confirmation: bar i closes in the trend direction (bullish candle for longs)
    and beyond the previous bar's high (low).
  * Stop: beyond the lowest low (highest high) of the pullback, plus
    `stop_buffer_atr` x ATR. Target: `reward_risk` x risk from the actual fill.
  * Exit early if a bar closes beyond the slow EMA against the position.
This is a hypothesis to test, not a strategy known to be profitable.
"""
from __future__ import annotations

import math

from pydantic import Field, model_validator

from ..indicators import atr, ema
from .base import RegimeParams, Strategy


class PullbackParams(RegimeParams):
    fast_ema: int = Field(20, ge=2, le=500)
    slow_ema: int = Field(100, ge=3, le=1000)
    slope_bars: int = Field(10, ge=1, le=200)
    pullback_bars: int = Field(5, ge=1, le=50)
    touch_atr: float = Field(0.3, ge=0, le=5)
    atr_period: int = Field(14, ge=2, le=500)
    stop_buffer_atr: float = Field(0.3, ge=0, le=5)
    reward_risk: float = Field(1.5, ge=0.5, le=20)

    @model_validator(mode="after")
    def _check(self):
        if self.fast_ema >= self.slow_ema:
            raise ValueError("fast_ema must be < slow_ema")
        return self


class TrendPullback(Strategy):
    name = "trend_pullback"
    version = "1.0.0"
    Params = PullbackParams
    description = "Buy pullbacks to the fast EMA in an up-trend (sell rallies in a down-trend), confirmed by the close"
    tunable = ("fast_ema", "slow_ema", "touch_atr", "stop_buffer_atr", "reward_risk")

    def warmup_bars(self):
        p = self.params
        return max(p.slow_ema + p.slope_bars, p.atr_period) + p.pullback_bars + 1

    def prepare(self, primary, htf, primary_minutes, htf_minutes):
        p = self.params
        df = primary.copy().reset_index(drop=True)
        df["ema_fast"] = ema(df["close"], p.fast_ema)
        df["ema_slow"] = ema(df["close"], p.slow_ema)
        df["slow_prev"] = df["ema_slow"].shift(p.slope_bars)
        df["atr"] = atr(df, p.atr_period)
        n = p.pullback_bars
        # distance of each bar's low/high to the fast EMA, in ATR, then the best of the last n bars
        df["touch_long"] = ((df["low"] - df["ema_fast"]) / df["atr"]).rolling(n, min_periods=n).min()
        df["touch_short"] = ((df["ema_fast"] - df["high"]) / df["atr"]).rolling(n, min_periods=n).min()
        df["held_long"] = (df["close"] > df["ema_slow"]).astype(float).rolling(n, min_periods=n).min()
        df["held_short"] = (df["close"] < df["ema_slow"]).astype(float).rolling(n, min_periods=n).min()
        df["pb_low"] = df["low"].rolling(n, min_periods=n).min()
        df["pb_high"] = df["high"].rolling(n, min_periods=n).max()
        df["prev_high"] = df["high"].shift(1)
        df["prev_low"] = df["low"].shift(1)
        self.add_regime(df, df["atr"])
        self.df = df
        return df

    def _v(self, col, i):
        return float(self.v(col, i))

    def validate_market_conditions(self, i):
        if any(math.isnan(self._v(c, i)) for c in ("ema_slow", "slow_prev", "atr", "touch_long", "pb_low")):
            return False, "indicators warming up"
        if self._v("atr", i) <= 0:
            return False, "zero volatility"
        return True, ""

    def calculate_entry(self, i):
        p = self.params
        c, o = self._v("close", i), self._v("open", i)
        fast, slow, slow_prev = self._v("ema_fast", i), self._v("ema_slow", i), self._v("slow_prev", i)
        if (c > slow and fast > slow and slow > slow_prev and self._v("held_long", i) == 1
                and self._v("touch_long", i) <= p.touch_atr and c > o and c > self._v("prev_high", i)):
            return "buy"
        if (c < slow and fast < slow and slow < slow_prev and self._v("held_short", i) == 1
                and self._v("touch_short", i) <= p.touch_atr and c < o and c < self._v("prev_low", i)):
            return "sell"
        return None

    def calculate_stop_loss(self, i, side):
        buf = self.params.stop_buffer_atr * self._v("atr", i)
        return self._v("pb_low", i) - buf if side == "buy" else self._v("pb_high", i) + buf

    def calculate_take_profit(self, i, side, stop):
        c = self._v("close", i)
        r = abs(c - stop) * self.params.reward_risk
        return c + r if side == "buy" else c - r

    def target_r(self):
        return self.params.reward_risk

    def calculate_exit(self, i, side):
        c, slow = self._v("close", i), self._v("ema_slow", i)
        if side == "buy" and c < slow:
            return "trend invalidated: close below slow EMA"
        if side == "sell" and c > slow:
            return "trend invalidated: close above slow EMA"
        return None

    def describe(self, i, side):
        data = {k: round(self._v(k, i), 6) for k in ("close", "ema_fast", "ema_slow", "atr", "pb_low", "pb_high")}
        word = "up" if side == "buy" else "down"
        return (f"{side}: pullback to fast EMA in an {word}-trend, confirmed by a close beyond the prior bar",
                "stop beyond the pullback extreme; exit on a close through the slow EMA", data)
