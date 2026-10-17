"""Load settings from a YAML file plus secrets from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

from .risk import RiskConfig
from .strategy import StrategyConfig


@dataclass
class SymbolMap:
    ticker: str      # ticker as Quiver reports it, e.g. AAPL
    mt5: str         # symbol name in your MT5 terminal, e.g. AAPL.NAS / #AAPL


@dataclass
class AutoUniverse:
    enabled: bool = False
    top_n: int = 10
    min_events: int = 3       # need at least this many buys+sells to trust the score
    mt5_prefix: str = ""
    mt5_suffix: str = ""


@dataclass
class QuiverConfig:
    lookback_days: int = 30
    congress_weight: float = 1.0
    insider_weight: float = 1.0
    refresh_minutes: int = 60


@dataclass
class MT5Config:
    path: str | None = None
    magic: int = 770011
    deviation: int = 20


@dataclass
class Settings:
    mode: str = "paper"            # paper | live
    timeframe: str = "H1"
    poll_seconds: int = 30
    paper_equity: float = 10_000.0
    symbols: list[SymbolMap] = field(default_factory=list)
    auto_universe: AutoUniverse = field(default_factory=AutoUniverse)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    quiver: QuiverConfig = field(default_factory=QuiverConfig)
    mt5: MT5Config = field(default_factory=MT5Config)

    # secrets (env only)
    quiver_token: str = ""
    mt5_login: int | None = None
    mt5_password: str | None = None
    mt5_server: str | None = None


def _build(cls, data: dict | None):
    data = data or {}
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"unknown {cls.__name__} keys: {sorted(unknown)}")
    return cls(**data)


def load_settings(path: str | Path = "config.yaml") -> Settings:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    raw: dict = {}
    p = Path(path)
    if p.exists():
        raw = yaml.safe_load(p.read_text()) or {}

    s = Settings(
        mode=raw.get("mode", "paper"),
        timeframe=raw.get("timeframe", "H1"),
        poll_seconds=int(raw.get("poll_seconds", 30)),
        paper_equity=float(raw.get("paper_equity", 10_000.0)),
        symbols=[_build(SymbolMap, m) for m in raw.get("symbols", [])],
        auto_universe=_build(AutoUniverse, raw.get("auto_universe")),
        strategy=_build(StrategyConfig, raw.get("strategy")),
        risk=_build(RiskConfig, raw.get("risk")),
        quiver=_build(QuiverConfig, raw.get("quiver")),
        mt5=_build(MT5Config, raw.get("mt5")),
    )
    if s.mode not in ("paper", "live"):
        raise ValueError("mode must be 'paper' or 'live'")
    if s.strategy.bias_mode not in ("require", "boost", "ignore"):
        raise ValueError("strategy.bias_mode must be require, boost or ignore")

    s.quiver_token = os.getenv("QUIVER_API_TOKEN", "")
    login = os.getenv("MT5_LOGIN")
    s.mt5_login = int(login) if login else None
    s.mt5_password = os.getenv("MT5_PASSWORD")
    s.mt5_server = os.getenv("MT5_SERVER")
    s.mt5.path = os.getenv("MT5_PATH", s.mt5.path)
    return s
