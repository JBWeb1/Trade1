from .base import SignalIntent, Strategy, StrategyParams
from .trend import TrendContinuation

REGISTRY: dict[str, type[Strategy]] = {TrendContinuation.name: TrendContinuation}


def get_strategy(name: str, params: dict | None = None) -> Strategy:
    try:
        cls = REGISTRY[name]
    except KeyError:
        raise ValueError(f"unknown strategy {name!r}; available: {sorted(REGISTRY)}") from None
    return cls(params or {})


__all__ = ["REGISTRY", "SignalIntent", "Strategy", "StrategyParams", "get_strategy"]
