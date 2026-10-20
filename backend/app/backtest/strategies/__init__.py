from .base import RegimeParams, SignalIntent, Strategy, StrategyParams
from .breakout import RangeBreakout
from .pullback import TrendPullback
from .session import AsianRangeBreakout, SessionOpenBreakout
from .sweep import LiquiditySweep
from .trend import TrendContinuation

REGISTRY: dict[str, type[Strategy]] = {
    c.name: c for c in (TrendContinuation, RangeBreakout, LiquiditySweep, TrendPullback,
                        SessionOpenBreakout, AsianRangeBreakout)}


def get_strategy(name: str, params: dict | None = None) -> Strategy:
    try:
        cls = REGISTRY[name]
    except KeyError:
        raise ValueError(f"unknown strategy {name!r}; available: {sorted(REGISTRY)}") from None
    return cls(params or {})


__all__ = ["REGISTRY", "RegimeParams", "SignalIntent", "Strategy", "StrategyParams", "get_strategy"]
