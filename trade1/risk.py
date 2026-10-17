"""Position sizing and account-level guard rails."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from .models import Position, Signal, SymbolInfo


@dataclass
class RiskConfig:
    risk_per_trade_pct: float = 0.5   # % of equity lost if the stop is hit
    max_open_positions: int = 5
    max_daily_loss_pct: float = 2.0   # stop opening trades after this drawdown today
    max_spread_points: float = 50.0   # skip if spread is wider than this (in points)


class RiskManager:
    def __init__(self, cfg: RiskConfig | None = None):
        self.cfg = cfg or RiskConfig()
        self._day: date | None = None
        self._day_start_equity = 0.0

    def start_of_day(self, today: date, equity: float) -> None:
        if self._day != today:
            self._day = today
            self._day_start_equity = equity

    def daily_loss_hit(self, equity: float) -> bool:
        if self._day_start_equity <= 0:
            return False
        dd = (self._day_start_equity - equity) / self._day_start_equity * 100
        return dd >= self.cfg.max_daily_loss_pct

    def position_size(self, signal: Signal, info: SymbolInfo, equity: float) -> float:
        """Lots such that hitting the stop loses ~risk_per_trade_pct of equity."""
        risk_money = equity * self.cfg.risk_per_trade_pct / 100
        ticks = signal.risk_per_unit / info.tick_size
        loss_per_lot = ticks * info.tick_value
        if loss_per_lot <= 0:
            return 0.0
        raw = risk_money / loss_per_lot
        steps = math.floor(raw / info.volume_step + 1e-9)
        vol = round(steps * info.volume_step, 8)
        if vol < info.volume_min:
            return 0.0  # can't trade this small without exceeding risk
        return min(vol, info.volume_max)

    def can_open(self, signal: Signal, open_positions: list[Position], equity: float,
                 spread: float, info: SymbolInfo) -> tuple[bool, str]:
        if self.daily_loss_hit(equity):
            return False, "daily loss limit reached"
        if len(open_positions) >= self.cfg.max_open_positions:
            return False, "max open positions reached"
        if any(p.symbol == signal.symbol for p in open_positions):
            return False, "already have a position in this symbol"
        if info.point and spread / info.point > self.cfg.max_spread_points:
            return False, f"spread {spread / info.point:.0f} pts too wide"
        return True, ""
