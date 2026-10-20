"""Portfolio backtest: several symbol/strategy legs on one account.

Method (stated in every result):
  1. Each leg is backtested on its own with the usual engine, giving trades with
     entry/exit times and R-multiples (net result / planned risk, after costs).
  2. The trades are replayed together in time order on one account balance.
     A trade is taken only if the combined limits allow it at its entry time:
     max open positions, max total open risk, max same-direction exposure per
     currency (correlation limit), daily loss limit and drawdown lockout.
  3. An accepted trade risks `risk_per_trade_pct` of the account equity at entry;
     its result is R-multiple x that risk. Lot rounding is ignored.

Correlation limit: each position is broken into currency exposure. Long XAUUSD
is +XAU / -USD, long EURUSD is +EUR / -USD, so both together are two short-USD
bets. A trade is rejected if any currency's net exposure would exceed the limit.
Non-currency CFDs (indices, stocks, energies) count as exposure to their own symbol.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from .research import monte_carlo

FX_LIKE = {"forex", "metals"}


class PortfolioLimits(BaseModel):
    model_config = {"extra": "forbid"}
    starting_balance: float = Field(10_000, gt=0)
    risk_per_trade_pct: float = Field(0.25, gt=0, le=5)
    max_open_positions: int = Field(3, ge=1, le=50)
    max_total_open_risk_pct: float = Field(1.0, gt=0, le=20)
    max_currency_exposure: int = Field(2, ge=1, le=10)
    max_daily_loss_pct: float = Field(1.0, gt=0, le=100)
    max_drawdown_pct: float = Field(20.0, gt=0, le=100)


@dataclass
class LegTrade:
    leg: str
    symbol: str
    side: str
    entry_time: datetime
    exit_time: datetime
    r: float
    exposure: dict[str, int]


def exposure_of(spec: dict, side: str) -> dict[str, int]:
    d = 1 if side == "buy" else -1
    base, quote = (spec.get("currency_base") or "").upper(), (spec.get("currency_profit") or "").upper()
    if spec.get("category") in FX_LIKE and base and quote and base != quote:
        return {base: d, quote: -d}
    return {spec.get("symbol") or "?": d}


def replay(trades: list[LegTrade], limits: PortfolioLimits) -> dict:
    # Order at equal timestamps: exits of earlier trades (0), then entries (1), then exits of
    # trades that opened and closed within that same bar (2), so no trade is closed before it opens.
    events = []
    for k, t in enumerate(trades):
        events.append((t.entry_time, 1, k))
        events.append((t.exit_time, 2 if t.exit_time <= t.entry_time else 0, k))
    events.sort(key=lambda e: (e[0], e[1], e[2]))

    equity = limits.starting_balance
    peak = equity
    open_: dict[int, float] = {}            # trade index -> risk money
    exposure: Counter = Counter()
    accepted, rejected = [], Counter()
    curve = [(events[0][0] if events else None, equity)]
    day, day_start = None, equity
    locked = False
    for time, kind, k in events:
        t = trades[k]
        if kind in (0, 2):
            if k in open_:
                pnl = t.r * open_.pop(k)
                equity += pnl
                for c, v in t.exposure.items():
                    exposure[c] -= v
                accepted.append({"leg": t.leg, "symbol": t.symbol, "side": t.side, "entry_time": t.entry_time.isoformat(),
                                 "exit_time": t.exit_time.isoformat(), "r": round(t.r, 4), "pnl": round(pnl, 2)})
                curve.append((time, equity))
                peak = max(peak, equity)
                if (peak - equity) / peak * 100 >= limits.max_drawdown_pct:
                    locked = True
            continue
        d = time.date()
        if d != day:
            day, day_start = d, equity
        risk_pct_open = len(open_) * limits.risk_per_trade_pct
        new_exposure = exposure.copy()
        for c, v in t.exposure.items():
            new_exposure[c] += v
        if locked:
            rejected["drawdown lockout"] += 1
        elif (day_start - equity) / day_start * 100 >= limits.max_daily_loss_pct:
            rejected["daily loss limit"] += 1
        elif len(open_) >= limits.max_open_positions:
            rejected["max open positions"] += 1
        elif risk_pct_open + limits.risk_per_trade_pct > limits.max_total_open_risk_pct + 1e-9:
            rejected["max total open risk"] += 1
        elif any(abs(v) > limits.max_currency_exposure for v in new_exposure.values()):
            rejected["currency exposure (correlation) limit"] += 1
        else:
            open_[k] = equity * limits.risk_per_trade_pct / 100
            exposure = new_exposure

    rs = np.array([a["r"] for a in accepted]) if accepted else np.array([])
    pnl = np.array([a["pnl"] for a in accepted]) if accepted else np.array([])
    eq = np.array([e for _, e in curve])
    peaks = np.maximum.accumulate(eq) if len(eq) else eq
    max_dd = float(((peaks - eq) / peaks).max() * 100) if len(eq) else 0.0
    by_leg = defaultdict(lambda: {"trades": 0, "net": 0.0})
    for a in accepted:
        by_leg[a["leg"]]["trades"] += 1
        by_leg[a["leg"]]["net"] += a["pnl"]
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    return {
        "signals": len(trades), "accepted": len(accepted), "rejected": dict(rejected),
        "win_rate": round(float((pnl > 0).mean()), 4) if len(pnl) else None,
        "expectancy_r": round(float(rs.mean()), 4) if len(rs) else None,
        "profit_factor": round(float(wins.sum() / -losses.sum()), 4) if len(losses) and losses.sum() < 0 else None,
        "net_profit": round(float(pnl.sum()), 2), "net_return_pct": round((equity / limits.starting_balance - 1) * 100, 3),
        "max_drawdown_pct": round(max_dd, 3), "final_equity": round(equity, 2),
        "by_leg": {k: {"trades": v["trades"], "net": round(v["net"], 2)} for k, v in by_leg.items()},
        "monte_carlo": monte_carlo(list(rs), limits.risk_per_trade_pct),
        "equity_curve": [{"time": t.isoformat() if t else None, "equity": round(e, 2)} for t, e in curve[-2000:]],
        "trades": accepted[-500:],
    }


def leg_correlation(trades: list[LegTrade]) -> dict:
    """Correlation of daily R totals between legs (days where any leg closed a trade)."""
    if not trades:
        return {}
    df = pd.DataFrame([{"leg": t.leg, "day": t.exit_time.date(), "r": t.r} for t in trades])
    piv = df.pivot_table(index="day", columns="leg", values="r", aggfunc="sum").fillna(0.0)
    if piv.shape[1] < 2 or len(piv) < 10:
        return {}
    corr = piv.corr().round(3)
    return {a: {b: (None if pd.isna(corr.loc[a, b]) else float(corr.loc[a, b])) for b in corr.columns} for a in corr.index}
