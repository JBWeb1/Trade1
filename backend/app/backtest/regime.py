"""Market-regime classification (causal).

Each bar gets one label, using only data up to that bar:
  * high_volatility: ATR/close is above the `vol_pct_threshold` percentile of the
    trailing `vol_window` bars.
  * trending: Kaufman efficiency ratio over `er_period` bars >= `er_threshold`
    (price moved mostly in one direction).
  * ranging: otherwise.
  * unknown: not enough history yet.
Thresholds are engineering defaults to be tested, not established truths.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

REGIMES = ("trending", "ranging", "high_volatility")


def efficiency_ratio(close: pd.Series, period: int) -> pd.Series:
    change = (close - close.shift(period)).abs()
    path = close.diff().abs().rolling(period, min_periods=period).sum()
    return change / path.replace(0, np.nan)


def classify(df: pd.DataFrame, atr_series: pd.Series, er_period: int = 20, er_threshold: float = 0.3,
             vol_window: int = 250, vol_pct_threshold: float = 0.8) -> pd.Series:
    er = efficiency_ratio(df["close"], er_period)
    atr_pct = atr_series / df["close"]
    # rank of the current value within the trailing window (includes current, excludes future)
    vol_rank = atr_pct.rolling(vol_window, min_periods=min(vol_window, 50)).rank(pct=True)
    label = np.where(vol_rank > vol_pct_threshold, "high_volatility",
                     np.where(er >= er_threshold, "trending", "ranging"))
    label = np.where(er.isna() | vol_rank.isna(), "unknown", label)
    return pd.Series(label, index=df.index)
