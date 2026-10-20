from .base import RegimeParams, SignalIntent, Strategy, StrategyParams
from .breakout import RangeBreakout
from .sweep import LiquiditySweep
from .trend import TrendContinuation

REGISTRY: dict[str, type[Strategy]] = {
    c.name: c for c in (TrendContinuation, RangeBreakout, LiquiditySweep)}


def get_strategy(name: str, params: dict | None = None) -> Strategy:
    try:
        cls = REGISTRY[name]
    except KeyError:
        raise ValueError(f"unknown strategy {name!r}; available: {sorted(REGISTRY)}") from None
    return cls(params or {})


__all__ = ["REGISTRY", "RegimeParams", "SignalIntent", "Strategy", "StrategyParams", "get_strategy"]
