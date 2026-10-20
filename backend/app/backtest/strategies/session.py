"""Session strategies (research candidates), with exchange-local times so that
daylight-saving changes are handled by the time-zone database, not by hand.

* session_open_breakout: the first `opening_minutes` after the London (08:00
  Europe/London) or New York (08:00 America/New_York) open form an opening range.
  The first close beyond it, within `trade_window_hours` of the open, is traded.
* asian_range_breakout: the Tokyo session (09:00-15:00 Asia/Tokyo) forms a range.
  The first close beyond it during the London morning (08:00 to 08:00 +
  `trade_window_hours`, Europe/London) is traded.

Both: one signal per session day; stop at the opposite side of the range (or the
midpoint); target `reward_risk` x risk from the actual fill; a range wider than
`max_range_atr` x ATR is skipped. Use intraday timeframes (M1/M15/H1).
No look-ahead: a range is only used once every bar in it has closed.
These are hypotheses to test, not strategies known to be profitable.
"""
from __future__ import annotations

import math
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import Field

from ..indicators import atr
from .base import RegimeParams, Strategy

SESSIONS = {
    "london": ("Europe/London", 8, 0),
    "new_york": ("America/New_York", 8, 0),
}


def _local_minutes(times: pd.Series, tz: str) -> tuple[pd.Series, pd.Series]:
    local = times.dt.tz_convert(tz)
    return local.dt.date, local.dt.hour * 60 + local.dt.minute


class _SessionBase(Strategy):
    """Shared logic; subclasses define the range window and the trade window."""

    def _window_columns(self, df: pd.DataFrame, minutes: int) -> pd.DataFrame:
        raise NotImplementedError

    def prepare(self, primary, htf, primary_minutes, htf_minutes):
        p = self.params
        if primary_minutes > 60:
            raise ValueError(f"{self.name} needs an intraday timeframe (M1, M15 or H1)")
        df = primary.copy().reset_index(drop=True)
        df["atr"] = atr(df, p.atr_period)
        df = self._window_columns(df, primary_minutes)
        # range per session day from bars fully inside the range window
        in_range = df["in_range"]
        grp = df[in_range].groupby("day")
        stats = pd.DataFrame({"rng_hi": grp["high"].max(), "rng_lo": grp["low"].min(), "rng_n": grp["high"].size()})
        df = df.join(stats, on="day")
        expected = df["range_minutes"].iloc[0] / primary_minutes if len(df) else 1
        complete = df["rng_n"] >= max(1, math.floor(expected * 0.8))
        tradable = df["in_trade"] & complete
        long_break = tradable & (df["close"] > df["rng_hi"] + p.buffer_atr * df["atr"])
        short_break = tradable & (df["close"] < df["rng_lo"] - p.buffer_atr * df["atr"])
        any_break = (long_break | short_break).astype(int)
        # first breakout of the day only (cumulative count within the day == 1)
        first = any_break.groupby(df["day"]).cumsum().eq(1) & any_break.astype(bool)
        df["sig"] = np.where(first & long_break, 1, np.where(first & short_break, -1, 0))
        self.add_regime(df, df["atr"])
        self.df = df
        return df

    def _v(self, col, i):
        return float(self.v(col, i))

    def warmup_bars(self):
        return self.params.atr_period + 1

    def validate_market_conditions(self, i):
        a = self._v("atr", i)
        if math.isnan(a) or a <= 0:
            return False, "indicators warming up"
        if self.v("sig", i) == 0:
            return False, "no first breakout of the session on this bar"
        if self._v("rng_hi", i) - self._v("rng_lo", i) > self.params.max_range_atr * a:
            return False, "session range too wide"
        return True, ""

    def calculate_entry(self, i):
        s = self.v("sig", i)
        return "buy" if s == 1 else "sell" if s == -1 else None

    def calculate_stop_loss(self, i, side):
        hi, lo = self._v("rng_hi", i), self._v("rng_lo", i)
        if self.params.stop_at == "midpoint":
            return (hi + lo) / 2
        return lo if side == "buy" else hi

    def calculate_take_profit(self, i, side, stop):
        c = self._v("close", i)
        r = abs(c - stop) * self.params.reward_risk
        return c + r if side == "buy" else c - r

    def target_r(self):
        return self.params.reward_risk

    def describe(self, i, side):
        data = {k: round(self._v(k, i), 6) for k in ("close", "rng_hi", "rng_lo", "atr")}
        data["session_day"] = str(self.v("day", i))
        edge = "high" if side == "buy" else "low"
        return (f"{side}: first close beyond the {self.range_label()} {edge}",
                f"stop at the range {'midpoint' if self.params.stop_at == 'midpoint' else 'opposite side'}", data)

    def range_label(self) -> str:
        return "session range"


class SessionParams(RegimeParams):
    session: Literal["london", "new_york"] = "london"
    opening_minutes: int = Field(60, ge=15, le=240)
    trade_window_hours: float = Field(3, gt=0, le=12)
    atr_period: int = Field(14, ge=2, le=500)
    buffer_atr: float = Field(0.05, ge=0, le=5)
    max_range_atr: float = Field(4.0, gt=0, le=50)
    stop_at: Literal["opposite", "midpoint"] = "midpoint"
    reward_risk: float = Field(1.5, ge=0.5, le=20)


class SessionOpenBreakout(_SessionBase):
    name = "session_open_breakout"
    version = "1.0.0"
    Params = SessionParams
    description = "Opening-range breakout at the London or New York open (local time, DST-aware)"
    tunable = ("opening_minutes", "trade_window_hours", "buffer_atr", "reward_risk")

    def _window_columns(self, df, minutes):
        p = self.params
        tz, h, m = SESSIONS[p.session]
        day, mins = _local_minutes(df["time"], tz)
        open_m = h * 60 + m
        df["day"] = day
        bar_end = mins + minutes
        df["in_range"] = (mins >= open_m) & (bar_end <= open_m + p.opening_minutes)
        df["in_trade"] = (mins >= open_m + p.opening_minutes) & (mins < open_m + p.trade_window_hours * 60)
        df["range_minutes"] = p.opening_minutes
        return df

    def range_label(self):
        return f"{self.params.session} opening range"


class AsianParams(RegimeParams):
    trade_window_hours: float = Field(4, gt=0, le=12)
    atr_period: int = Field(14, ge=2, le=500)
    buffer_atr: float = Field(0.05, ge=0, le=5)
    max_range_atr: float = Field(6.0, gt=0, le=50)
    stop_at: Literal["opposite", "midpoint"] = "midpoint"
    reward_risk: float = Field(1.5, ge=0.5, le=20)


class AsianRangeBreakout(_SessionBase):
    name = "asian_range_breakout"
    version = "1.0.0"
    Params = AsianParams
    description = "Breakout of the Tokyo-session range during the London morning (DST-aware)"
    tunable = ("trade_window_hours", "buffer_atr", "max_range_atr", "reward_risk")

    def _window_columns(self, df, minutes):
        p = self.params
        # the Tokyo session (09:00-15:00 JST) and the London morning fall on the same UTC date
        _, t_mins = _local_minutes(df["time"], "Asia/Tokyo")
        _, l_mins = _local_minutes(df["time"], "Europe/London")
        df["day"] = df["time"].dt.tz_convert("UTC").dt.date
        df["in_range"] = (t_mins >= 9 * 60) & (t_mins + minutes <= 15 * 60)
        df["in_trade"] = (l_mins >= 8 * 60) & (l_mins < 8 * 60 + p.trade_window_hours * 60)
        df["range_minutes"] = 6 * 60
        return df

    def range_label(self):
        return "Asian (Tokyo) session range"
