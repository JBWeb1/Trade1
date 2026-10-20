"""Multi-timeframe alignment without look-ahead.

Rules (all times UTC, bar `time` = bar OPEN time, as MT5 provides):
  * A bar's information is available only at its CLOSE: time + timeframe.
  * A decision on the primary timeframe is taken at the close of primary bar i.
  * A higher-timeframe (HTF) bar may be used for that decision only if its
    close time is <= the decision time. So an H4 bar opened at 08:00 becomes
    usable at 12:00, never at 09:00 on the H1 chart.
  * Orders resulting from a decision fill at the OPEN of the next primary bar.
"""
from __future__ import annotations

import pandas as pd


def decision_times(df: pd.DataFrame, tf_minutes: int) -> pd.Series:
    return df["time"] + pd.Timedelta(minutes=tf_minutes)


def align_higher_timeframe(primary: pd.DataFrame, primary_minutes: int, htf: pd.DataFrame,
                           htf_minutes: int, columns: list[str], prefix: str = "htf_") -> pd.DataFrame:
    """Return `primary` with HTF `columns` (prefixed) from the latest *closed* HTF bar
    at each primary decision time, plus `<prefix>close_time`. Rows with no closed HTF
    bar yet get NaN."""
    if htf_minutes < primary_minutes:
        raise ValueError("higher timeframe must not be shorter than the primary timeframe")
    left = primary.copy()
    left["_decision_time"] = decision_times(left, primary_minutes)
    right = htf[["time", *columns]].copy()
    right["_htf_close"] = right["time"] + pd.Timedelta(minutes=htf_minutes)
    right = right.drop(columns="time").rename(columns={c: prefix + c for c in columns})
    right = right.rename(columns={"_htf_close": prefix + "close_time"})
    left = left.sort_values("_decision_time")
    merged = pd.merge_asof(left, right.sort_values(prefix + "close_time"),
                           left_on="_decision_time", right_on=prefix + "close_time",
                           direction="backward", allow_exact_matches=True)
    return merged.drop(columns="_decision_time").sort_values("time").reset_index(drop=True)
