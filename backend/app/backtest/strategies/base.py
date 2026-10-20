"""Strategy interface.

A strategy sees prepared, causal indicator columns and, for bar i, may only
read rows <= i (the engine passes the full frame but tests verify that
truncating the future never changes past signals).
"""
from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar

import pandas as pd
from typing import Literal

from pydantic import BaseModel, Field


@dataclass
class SignalIntent:
    side: str                # "buy" | "sell"
    reference_price: float   # close of the decision bar (bid)
    stop_loss: float
    take_profit: float
    rationale: str
    invalidation: str
    data_used: dict[str, Any] = field(default_factory=dict)
    # If set, the engine places the target at target_r x (actual fill - stop) from the
    # fill price, so spread/slippage at entry do not silently shrink the planned R:R.
    target_r: float | None = None

    @property
    def planned_risk(self) -> float:
        return abs(self.reference_price - self.stop_loss)

    def as_dict(self) -> dict:
        return asdict(self)


class StrategyParams(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}


class RegimeParams(StrategyParams):
    """Shared regime-filter settings (see app.backtest.regime)."""
    regime_filter: Literal["any", "trending", "ranging"] = "any"
    avoid_high_volatility: bool = False
    regime_er_period: int = Field(20, ge=5, le=500)
    regime_er_threshold: float = Field(0.3, gt=0, lt=1)


class Strategy(ABC):
    name: ClassVar[str]
    version: ClassVar[str]
    Params: ClassVar[type[StrategyParams]]
    description: ClassVar[str] = ""
    uses_higher_timeframe: ClassVar[bool] = False
    # numeric parameters perturbed by sensitivity analysis and walk-forward tuning
    tunable: ClassVar[tuple[str, ...]] = ()

    def __init__(self, params: StrategyParams | dict | None = None):
        if isinstance(params, dict) or params is None:
            params = self.Params(**(params or {}))
        self.params = params
        self._df: pd.DataFrame | None = None
        self._cols: dict = {}
        # instrument contract spec (point, tick_size, ...); not part of the strategy identity
        self.instrument: dict = {}

    # ---- prepared data (column values cached as numpy arrays for fast per-bar access)
    @property
    def df(self) -> pd.DataFrame | None:
        return self._df

    @df.setter
    def df(self, value: pd.DataFrame | None) -> None:
        self._df = value
        self._cols = {}

    def v(self, col: str, i: int):
        arr = self._cols.get(col)
        if arr is None:
            arr = self._cols[col] = self._df[col].to_numpy()
        return arr[i]

    # ---- identity / reproducibility
    def params_hash(self) -> str:
        blob = json.dumps({"name": self.name, "version": self.version,
                           "params": self.params.model_dump()}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def get_strategy_metadata(self) -> dict:
        return {"name": self.name, "version": self.version, "description": self.description,
                "params": self.params.model_dump(), "params_hash": self.params_hash(),
                "uses_higher_timeframe": self.uses_higher_timeframe}

    # ---- lifecycle
    @abstractmethod
    def prepare(self, primary: pd.DataFrame, htf: pd.DataFrame | None,
                primary_minutes: int, htf_minutes: int | None) -> pd.DataFrame:
        """Compute causal indicator columns; return the frame the engine iterates."""

    def warmup_bars(self) -> int:
        return 50

    @abstractmethod
    def validate_market_conditions(self, i: int) -> tuple[bool, str]:
        ...

    @abstractmethod
    def calculate_entry(self, i: int) -> str | None:
        """Return 'buy', 'sell' or None from the setup rules at bar i's close."""

    @abstractmethod
    def calculate_stop_loss(self, i: int, side: str) -> float: ...

    @abstractmethod
    def calculate_take_profit(self, i: int, side: str, stop: float) -> float: ...

    def calculate_exit(self, i: int, side: str) -> str | None:
        """Optional discretionary exit reason at bar i's close (filled next open)."""
        return None

    def describe(self, i: int, side: str) -> tuple[str, str, dict]:
        return f"{self.name} {side}", "stop loss", {}

    def add_regime(self, df: pd.DataFrame, atr_series: pd.Series) -> pd.DataFrame:
        from ..regime import classify
        p = self.params
        df["regime"] = classify(df, atr_series, getattr(p, "regime_er_period", 20),
                                getattr(p, "regime_er_threshold", 0.3))
        return df

    def regime_ok(self, i: int) -> tuple[bool, str]:
        p = self.params
        if self.df is None or "regime" not in self.df:
            return True, ""
        r = self.v("regime", i)
        if getattr(p, "avoid_high_volatility", False) and r == "high_volatility":
            return False, "high-volatility regime"
        want = getattr(p, "regime_filter", "any")
        if want != "any" and r != want:
            return False, f"regime {r} (strategy trades {want} only)"
        return True, ""

    def generate_signal(self, i: int) -> SignalIntent | None:
        ok, _ = self.regime_ok(i)
        if not ok:
            return None
        ok, _ = self.validate_market_conditions(i)
        if not ok:
            return None
        side = self.calculate_entry(i)
        if side is None:
            return None
        ref = float(self.v("close", i))
        stop = self.calculate_stop_loss(i, side)
        tp = self.calculate_take_profit(i, side, stop)
        if (side == "buy" and not stop < ref < tp) or (side == "sell" and not tp < ref < stop):
            return None
        rationale, invalidation, data = self.describe(i, side)
        if "regime" in self.df:
            data["regime"] = str(self.v("regime", i))
        return SignalIntent(side, ref, stop, tp, rationale, invalidation, data, self.target_r())

    def target_r(self) -> float | None:
        """Override to anchor the target to the fill price as a multiple of risk."""
        return None
