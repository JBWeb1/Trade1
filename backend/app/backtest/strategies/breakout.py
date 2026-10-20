"""Range breakout (research candidate B).

Rules at the close of bar i:
  * Range = highest high / lowest low of the previous `range_bars` bars
    (e.g. 24 x H1 = the previous day), excluding bar i.
  * Consolidation filter: range width <= `max_range_atr` x ATR.
  * Closed-candle confirmation: close beyond the range edge by at least
    `buffer_atr` x ATR.
  * Spread filter: the bar's recorded spread must be below
    `max_spread_atr_frac` x ATR (wide spreads eat breakout edges).
  * Stop / invalidation: back at the range midpoint. Target: `reward_risk` x risk
    from the actual fill.
This is a hypothesis to test, not a strategy known to be profitable.
"""
from __future__ import annotations

import math

from pydantic import Field

from ..indicators import atr, rolling_high, rolling_low
from .base import RegimeParams, Strategy


class BreakoutParams(RegimeParams):
    range_bars: int = Field(24, ge=3, le=1000)
    atr_period: int = Field(14, ge=2, le=500)
    max_range_atr: float = Field(4.0, gt=0, le=50)
    buffer_atr: float = Field(0.1, ge=0, le=5)
    max_spread_atr_frac: float = Field(0.2, gt=0, le=5)
    reward_risk: float = Field(1.5, ge=0.5, le=20)


class RangeBreakout(Strategy):
    name = "range_breakout"
    version = "1.0.0"
    Params = BreakoutParams
    description = "Breakout of a consolidating N-bar range on a closed candle, stop at range midpoint"
    tunable = ("range_bars", "max_range_atr", "buffer_atr", "reward_risk")

    def warmup_bars(self):
        return max(self.params.range_bars, self.params.atr_period) + 1

    def prepare(self, primary, htf, primary_minutes, htf_minutes):
        p = self.params
        if not self.instrument.get("point"):
            raise ValueError("range_breakout needs the instrument point size (set strategy.instrument)")
        df = primary.copy().reset_index(drop=True)
        df["atr"] = atr(df, p.atr_period)
        df["rng_hi"] = rolling_high(df["high"], p.range_bars)
        df["rng_lo"] = rolling_low(df["low"], p.range_bars)
        self.add_regime(df, df["atr"])
        self.df = df
        return df

    def _v(self, col, i):
        return float(self.v(col, i))

    def validate_market_conditions(self, i):
        p = self.params
        a, hi, lo = self._v("atr", i), self._v("rng_hi", i), self._v("rng_lo", i)
        if any(math.isnan(x) for x in (a, hi, lo)) or a <= 0:
            return False, "indicators warming up"
        if hi - lo > p.max_range_atr * a:
            return False, "no consolidation"
        spread = self._v("spread_points", i) * self.instrument["point"]
        if spread > p.max_spread_atr_frac * a:
            return False, "spread too wide relative to ATR"
        return True, ""

    def calculate_entry(self, i):
        p = self.params
        c, a = self._v("close", i), self._v("atr", i)
        if c > self._v("rng_hi", i) + p.buffer_atr * a:
            return "buy"
        if c < self._v("rng_lo", i) - p.buffer_atr * a:
            return "sell"
        return None

    def calculate_stop_loss(self, i, side):
        return (self._v("rng_hi", i) + self._v("rng_lo", i)) / 2

    def calculate_take_profit(self, i, side, stop):
        c = self._v("close", i)
        r = abs(c - stop) * self.params.reward_risk
        return c + r if side == "buy" else c - r

    def target_r(self):
        return self.params.reward_risk

    def describe(self, i, side):
        data = {k: round(self._v(k, i), 6) for k in ("close", "atr", "rng_hi", "rng_lo")}
        edge = "high" if side == "buy" else "low"
        return (f"{side}: closed beyond {self.params.range_bars}-bar range {edge} after consolidation",
                "close back at the range midpoint (stop)", data)
