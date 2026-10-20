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
from pydantic import BaseModel


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


class Strategy(ABC):
    name: ClassVar[str]
    version: ClassVar[str]
    Params: ClassVar[type[StrategyParams]]
    description: ClassVar[str] = ""
    uses_higher_timeframe: ClassVar[bool] = False

    def __init__(self, params: StrategyParams | dict | None = None):
        if isinstance(params, dict) or params is None:
            params = self.Params(**(params or {}))
        self.params = params
        self.df: pd.DataFrame | None = None

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

    def generate_signal(self, i: int) -> SignalIntent | None:
        ok, _ = self.validate_market_conditions(i)
        if not ok:
            return None
        side = self.calculate_entry(i)
        if side is None:
            return None
        ref = float(self.df["close"].iat[i])
        stop = self.calculate_stop_loss(i, side)
        tp = self.calculate_take_profit(i, side, stop)
        if (side == "buy" and not stop < ref < tp) or (side == "sell" and not tp < ref < stop):
            return None
        rationale, invalidation, data = self.describe(i, side)
        return SignalIntent(side, ref, stop, tp, rationale, invalidation, data, self.target_r())

    def target_r(self) -> float | None:
        """Override to anchor the target to the fill price as a multiple of risk."""
        return None
