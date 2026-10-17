"""Shared data types."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class Bar:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class SymbolInfo:
    """Broker contract specification needed for position sizing."""

    name: str
    point: float  # smallest price increment
    tick_size: float
    tick_value: float  # account-currency value of one tick for 1.0 lot
    volume_min: float
    volume_max: float
    volume_step: float
    digits: int = 2


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass
class Signal:
    symbol: str
    side: Side
    entry: float
    stop: float
    target: float
    reason: str = ""
    bias_score: float = 0.0

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry - self.stop)


@dataclass
class Position:
    ticket: int
    symbol: str
    side: Side
    volume: float
    price_open: float
    sl: float
    tp: float
    profit: float = 0.0
    comment: str = ""


@dataclass
class OrderResult:
    ok: bool
    ticket: int | None = None
    price: float | None = None
    message: str = ""
    raw: dict = field(default_factory=dict)
