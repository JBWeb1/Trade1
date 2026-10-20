"""Performance metrics computed from simulated trades and the equity curve.

Sharpe ratio assumptions: daily returns from the last equity mark of each UTC
day, risk-free rate 0, annualised with sqrt(252). Days without bars (weekends)
are skipped. Reported as None with fewer than 20 daily observations.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .engine import BacktestResult


def _f(x, nd=4):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x), nd)


def compute_metrics(res: BacktestResult, starting_balance: float) -> dict:
    trades = res.trades
    eq = res.equity
    pnl = np.array([t.net_pnl for t in trades], dtype=float)
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    n = len(pnl)

    streak = longest = 0
    for x in pnl:
        streak = streak + 1 if x <= 0 else 0
        longest = max(longest, streak)

    m: dict = {
        "closed_trades": n,
        "win_rate": _f(len(wins) / n) if n else None,
        "average_win": _f(wins.mean(), 2) if len(wins) else None,
        "average_loss": _f(losses.mean(), 2) if len(losses) else None,
        "expectancy_per_trade": _f(pnl.mean(), 2) if n else None,
        "expectancy_r": _f(np.mean([t.net_pnl / t.planned_risk_money for t in trades
                                    if t.planned_risk_money > 0])) if n else None,
        "profit_factor": _f(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else None,
        "net_profit": _f(pnl.sum(), 2),
        "net_return_pct": _f(pnl.sum() / starting_balance * 100, 3),
        "longest_losing_streak": longest,
        "long_trades": sum(1 for t in trades if t.side == "buy"),
        "short_trades": sum(1 for t in trades if t.side == "sell"),
        "stops_with_ambiguous_bar": sum(1 for t in trades if t.ambiguous_bar),
        "costs": {
            "commission": _f(sum(t.commission for t in trades), 2),
            "spread": _f(sum(t.spread_cost for t in trades), 2),
            "slippage": _f(sum(t.slippage_cost for t in trades), 2),
            "swap_cost_negative_is_income": _f(sum(t.swap for t in trades), 2),
        },
        "turnover_lots": _f(sum(t.volume for t in trades) * 2, 2),
        "exit_reasons": pd.Series([t.exit_reason for t in trades]).value_counts().to_dict() if n else {},
        "rejections": pd.Series([r.reason for r in res.rejections]).value_counts().to_dict()
        if res.rejections else {},
        "bars": res.bars,
    }

    if len(eq):
        e = eq["equity"].to_numpy()
        peak = np.maximum.accumulate(np.concatenate([[starting_balance], e]))[1:]
        dd = (peak - e) / peak
        m["max_drawdown_pct"] = _f(dd.max() * 100, 3)
        m["max_drawdown_money"] = _f((peak - e).max(), 2)
        # drawdown durations in bars, converted to days via timestamps
        under = dd > 1e-12
        durations, start = [], None
        times = eq["time"].to_list()
        for k, u in enumerate(under):
            if u and start is None:
                start = k
            elif not u and start is not None:
                durations.append((times[k] - times[start]).total_seconds() / 86400)
                start = None
        if start is not None:
            durations.append((times[-1] - times[start]).total_seconds() / 86400)
        m["max_drawdown_duration_days"] = _f(max(durations), 2) if durations else 0.0
        m["avg_drawdown_duration_days"] = _f(np.mean(durations), 2) if durations else 0.0

        daily = eq.set_index("time")["equity"].resample("1D").last().dropna()
        rets = daily.pct_change().dropna()
        m["sharpe_daily_annualised"] = _f(rets.mean() / rets.std() * math.sqrt(252), 3) \
            if len(rets) >= 20 and rets.std() > 0 else None
        m["final_equity"] = _f(e[-1], 2)
        m["first_bar_close"] = times[0].isoformat()
        m["last_bar_close"] = times[-1].isoformat()

    bars_in_market = sum(t.bars_held or 1 for t in trades)
    m["exposure_pct"] = _f(bars_in_market / res.bars * 100, 2) if res.bars else None

    if n:
        df = pd.DataFrame([{"month": t.exit_time.strftime("%Y-%m"), "pnl": t.net_pnl, "side": t.side}
                           for t in trades])
        by_month = df.groupby("month")["pnl"].agg(["count", "sum"])
        m["by_month"] = {k: {"trades": int(v["count"]), "net": _f(v["sum"], 2)} for k, v in by_month.iterrows()}
        by_side = df.groupby("side")["pnl"].agg(["count", "sum", "mean"])
        m["by_side"] = {k: {"trades": int(v["count"]), "net": _f(v["sum"], 2), "expectancy": _f(v["mean"], 2)}
                        for k, v in by_side.iterrows()}
        rg = pd.DataFrame([{"regime": t.regime or "unknown", "pnl": t.net_pnl} for t in trades])
        by_regime = rg.groupby("regime")["pnl"].agg(["count", "sum", "mean"])
        m["by_regime"] = {k: {"trades": int(v["count"]), "net": _f(v["sum"], 2), "expectancy": _f(v["mean"], 2)}
                          for k, v in by_regime.iterrows()}
    else:
        m["by_month"], m["by_side"], m["by_regime"] = {}, {}, {}
    m["sample_warning"] = (None if n >= 100 else
                           f"only {n} closed trades - far too few to draw conclusions" if n < 30 else
                           f"{n} closed trades - treat results as preliminary")
    m["notes"] = res.notes
    return m


def downsample_equity(eq: pd.DataFrame, max_points: int = 2000) -> list[dict]:
    if len(eq) > max_points:
        idx = np.linspace(0, len(eq) - 1, max_points).round().astype(int)
        eq = eq.iloc[np.unique(idx)]
    return [{"time": t.isoformat(), "equity": round(float(e), 2), "balance": round(float(b), 2)}
            for t, e, b in eq.itertuples(index=False)]
