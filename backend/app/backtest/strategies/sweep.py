"""Liquidity sweep (research candidate, ported from the original Trade1 prototype).

Idea: resting stop orders cluster beyond recent swing highs/lows. When a bar
trades through such a level and CLOSES back inside, those orders were taken and
price may reverse.

No look-ahead: a swing point at bar j needs `swing_strength` bars on each side,
so it is only known at bar j + swing_strength. For a decision at bar i, only
swings with j + swing_strength <= i - 1 are used, and a level counts as
"unswept" only if no bar between j and i-1 traded through it.

Rules at the close of bar i:
  * Bullish: low_i < unswept swing low L < close_i -> buy; stop below the wick
    by `stop_buffer_atr` x ATR.  Bearish: the mirror image.
  * Target: `reward_risk` x risk from the actual fill.
This is a hypothesis to test, not a strategy known to be profitable.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from pydantic import Field

from ..indicators import atr
from .base import RegimeParams, Strategy


class SweepParams(RegimeParams):
    swing_strength: int = Field(3, ge=1, le=20)
    lookback: int = Field(60, ge=10, le=1000)
    atr_period: int = Field(14, ge=2, le=500)
    stop_buffer_atr: float = Field(0.2, ge=0, le=5)
    reward_risk: float = Field(2.0, ge=0.5, le=20)
    min_atr_pct: float = Field(0.0002, ge=0)
    max_atr_pct: float = Field(0.05, gt=0)


class LiquiditySweep(Strategy):
    name = "liquidity_sweep"
    version = "1.0.0"
    Params = SweepParams
    description = "Reversal after a wick through an unswept swing high/low that closes back inside"
    tunable = ("swing_strength", "lookback", "stop_buffer_atr", "reward_risk")

    def warmup_bars(self):
        p = self.params
        return max(p.lookback, p.atr_period) + p.swing_strength + 2

    def prepare(self, primary, htf, primary_minutes, htf_minutes):
        df = primary.copy().reset_index(drop=True)
        df["atr"] = atr(df, self.params.atr_period)
        self.add_regime(df, df["atr"])
        self.df = df
        k = self.params.swing_strength
        h, l = df["high"], df["low"]
        # Swing flags look k bars to each side. A flag at j is only CONSULTED for
        # decisions at i >= j + k + 1 (see _levels), when all its bars have closed.
        sh = pd.Series(True, index=df.index)
        sl = pd.Series(True, index=df.index)
        for d in range(1, k + 1):
            sh &= (h > h.shift(d)) & (h > h.shift(-d))
            sl &= (l < l.shift(d)) & (l < l.shift(-d))
        self._is_sh, self._is_sl = sh.to_numpy(), sl.to_numpy()
        self._h, self._l = h.to_numpy(), l.to_numpy()
        return df

    def _levels(self, i: int):
        """Unswept swing highs/lows known at the close of bar i-1 (no look-ahead)."""
        k = self.params.swing_strength
        lo_idx = max(k, i - self.params.lookback)
        hi_idx = i - k  # j + k <= i - 1
        if hi_idx <= lo_idx:
            return [], []
        h, l = self._h, self._l
        highs = [h[j] for j in np.flatnonzero(self._is_sh[lo_idx:hi_idx]) + lo_idx
                 if h[j + 1:i].max() <= h[j]]
        lows = [l[j] for j in np.flatnonzero(self._is_sl[lo_idx:hi_idx]) + lo_idx
                if l[j + 1:i].min() >= l[j]]
        return highs, lows

    def validate_market_conditions(self, i):
        p = self.params
        a, c = float(self.v("atr", i)), float(self.v("close", i))
        if math.isnan(a) or a <= 0:
            return False, "indicators warming up"
        if not p.min_atr_pct <= a / c <= p.max_atr_pct:
            return False, "ATR% outside range"
        return True, ""

    def calculate_entry(self, i):
        c, lo, hi = float(self.v("close", i)), self._l[i], self._h[i]
        highs, lows = self._levels(i)
        self._swept = None
        swept_lows = [x for x in lows if lo < x < c]
        if swept_lows:
            self._swept = min(swept_lows)
            return "buy"
        swept_highs = [x for x in highs if c < x < hi]
        if swept_highs:
            self._swept = max(swept_highs)
            return "sell"
        return None

    def calculate_stop_loss(self, i, side):
        buf = self.params.stop_buffer_atr * float(self.v("atr", i))
        return self._l[i] - buf if side == "buy" else self._h[i] + buf

    def calculate_take_profit(self, i, side, stop):
        c = float(self.v("close", i))
        r = abs(c - stop) * self.params.reward_risk
        return c + r if side == "buy" else c - r

    def target_r(self):
        return self.params.reward_risk

    def describe(self, i, side):
        kind = "low" if side == "buy" else "high"
        return (f"{side}: bar swept unswept swing {kind} {self._swept:.6g} and closed back inside",
                "stop beyond the sweep wick",
                {"swept_level": float(self._swept), "close": float(self.v("close", i)),
                 "atr": round(float(self.v("atr", i)), 6)})

