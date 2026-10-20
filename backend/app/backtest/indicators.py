"""Causal indicators: the value at row i depends only on rows <= i."""
from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    # adjust=False is the recursive (causal) EMA; the first period-1 values are not trusted
    out = series.ewm(span=period, adjust=False, min_periods=period).mean()
    return out


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()  # Wilder


def rolling_high(series: pd.Series, lookback: int) -> pd.Series:
    """Highest value of the previous `lookback` rows, excluding the current row."""
    return series.shift(1).rolling(lookback, min_periods=lookback).max()


def rolling_low(series: pd.Series, lookback: int) -> pd.Series:
    return series.shift(1).rolling(lookback, min_periods=lookback).min()
