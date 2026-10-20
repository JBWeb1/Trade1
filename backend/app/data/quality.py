"""Data-quality checks for OHLC bars. Pure functions on pandas DataFrames.

Gaps are reported, never filled: markets close (weekends, daily breaks,
holidays) and the app must not pretend they traded continuously.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

REQUIRED = ("time", "open", "high", "low", "close")


@dataclass
class Gap:
    start: str          # last bar before the gap (UTC)
    end: str            # first bar after the gap (UTC)
    missing_bars: int
    kind: str           # weekend | session_break | unexpected


@dataclass
class QualityReport:
    timeframe_minutes: int
    bars_received: int = 0
    duplicates_removed: int = 0
    invalid_rows: int = 0
    invalid_examples: list[str] = field(default_factory=list)
    misaligned_rows: int = 0
    zero_volume_bars: int = 0
    usable_bars: int = 0
    first_bar: str | None = None
    last_bar: str | None = None
    gaps_weekend: int = 0
    gaps_session_break: int = 0
    gaps_unexpected: int = 0
    missing_bars_unexpected: int = 0
    largest_gaps: list[Gap] = field(default_factory=list)
    history_starts_late_by_days: float = 0.0
    stale: bool = False
    status: str = "ok"  # ok | warning | failed
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _classify_gap(prev: pd.Timestamp, nxt: pd.Timestamp, tf_minutes: int) -> str:
    span = nxt - prev
    # Typical FX/CFD weekend: Friday evening -> Sunday evening UTC
    if prev.weekday() in (4, 5) and nxt.weekday() in (6, 0) and span <= timedelta(days=3, hours=12):
        return "weekend"
    if span <= timedelta(hours=3) + timedelta(minutes=tf_minutes):
        return "session_break"  # daily maintenance / rollover break
    return "unexpected"


def clean_and_check(df: pd.DataFrame, tf_minutes: int, requested_start: datetime,
                    requested_end: datetime, server_offset_hours: float = 0.0,
                    now: datetime | None = None) -> tuple[pd.DataFrame, QualityReport]:
    """Return (usable rows sorted by time, report). Invalid/duplicate rows are dropped
    and counted, never repaired."""
    rep = QualityReport(timeframe_minutes=tf_minutes, bars_received=len(df))
    now = now or datetime.now(timezone.utc)
    if df.empty:
        rep.status = "failed"
        rep.notes.append("MT5 returned no bars for the requested range")
        return df, rep

    missing_cols = [c for c in REQUIRED if c not in df.columns]
    if missing_cols:
        rep.status = "failed"
        rep.notes.append(f"missing columns: {missing_cols}")
        return df.iloc[0:0], rep

    df = df.copy()
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.as_unit("ns")
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    for c in ("tick_volume", "real_volume", "spread"):
        if c not in df.columns:
            df[c] = 0
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("int64")
    df = df.sort_values("time", kind="stable")

    dup = df["time"].duplicated(keep="first")
    rep.duplicates_removed = int(dup.sum())
    df = df[~dup]

    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    bad = (df[["open", "high", "low", "close"]].isna().any(axis=1)
           | (o <= 0) | (h <= 0) | (l <= 0) | (c <= 0)
           | (h < l) | (h < np.maximum(o, c)) | (l > np.minimum(o, c)))
    rep.invalid_rows = int(bad.sum())
    rep.invalid_examples = [t.isoformat() for t in df.loc[bad, "time"].head(5)]
    df = df[~bad]

    # bars must open on a timeframe boundary in broker server time
    epoch = pd.Timestamp(0, tz="UTC")
    server_secs = (df["time"] + pd.Timedelta(hours=server_offset_hours) - epoch) // pd.Timedelta(seconds=1)
    misaligned = (server_secs % (tf_minutes * 60)) != 0
    rep.misaligned_rows = int(misaligned.sum())
    df = df[~misaligned]

    # outside the requested window (should not happen, but never trust it)
    in_range = (df["time"] >= pd.Timestamp(requested_start)) & (df["time"] <= pd.Timestamp(requested_end))
    if (~in_range).any():
        rep.notes.append(f"{int((~in_range).sum())} bars outside requested range dropped")
    df = df[in_range].reset_index(drop=True)

    rep.usable_bars = len(df)
    if df.empty:
        rep.status = "failed"
        rep.notes.append("no usable bars after validation")
        return df, rep

    rep.zero_volume_bars = int((df["tick_volume"] == 0).sum())
    rep.first_bar = df["time"].iloc[0].isoformat()
    rep.last_bar = df["time"].iloc[-1].isoformat()

    step = pd.Timedelta(minutes=tf_minutes)
    diffs = df["time"].diff()
    gaps: list[Gap] = []
    for i in np.flatnonzero((diffs > step).to_numpy()):
        prev, nxt = df["time"].iloc[i - 1], df["time"].iloc[i]
        kind = _classify_gap(prev, nxt, tf_minutes)
        missing = int((nxt - prev) / step) - 1
        gaps.append(Gap(prev.isoformat(), nxt.isoformat(), missing, kind))
        if kind == "weekend":
            rep.gaps_weekend += 1
        elif kind == "session_break":
            rep.gaps_session_break += 1
        else:
            rep.gaps_unexpected += 1
            rep.missing_bars_unexpected += missing
    rep.largest_gaps = sorted(gaps, key=lambda g: g.missing_bars, reverse=True)[:10]

    late = df["time"].iloc[0] - pd.Timestamp(requested_start)
    rep.history_starts_late_by_days = round(late.total_seconds() / 86400, 2)
    if late > timedelta(days=4):
        rep.notes.append("terminal history starts after the requested start. MT5 may still be loading "
                         "older history: open this symbol's chart in MT5, press Home to scroll back, set "
                         "Tools > Options > Charts > 'Max bars in chart' to Unlimited, then download again "
                         "(already-stored bars are skipped)")

    # stale: the request reaches 'now' but data stops well before it
    if pd.Timestamp(requested_end) >= pd.Timestamp(now) - timedelta(minutes=tf_minutes * 2):
        age = pd.Timestamp(now) - df["time"].iloc[-1]
        if age > timedelta(minutes=tf_minutes * 3) + timedelta(days=3):
            rep.stale = True
            rep.notes.append(f"latest bar is {age} old")

    warn = (rep.duplicates_removed or rep.invalid_rows or rep.misaligned_rows
            or rep.gaps_unexpected or rep.stale or late > timedelta(days=4))
    rep.status = "warning" if warn else "ok"
    return df, rep
