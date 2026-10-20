"""Robustness tools: Monte Carlo resampling of trade sequences.

Monte Carlo here answers: "given these trade outcomes, how bad could the
drawdown and losing streak have been if the trades had come in a different
order (or a slightly different mix)?"  Each simulation bootstraps the trade
R-multiples (sampling with replacement, same trade count) and compounds them
at a fixed fraction of equity per trade. Fixed seed => reproducible.

Assumptions (stated in every result): trades are independent; position size is
`risk_pct` of current equity per trade (lot rounding ignored); costs are already
inside the R-multiples.
"""
from __future__ import annotations

import numpy as np


def _longest_losing_streak(row: np.ndarray) -> int:
    best = cur = 0
    for x in row:
        cur = cur + 1 if x <= 0 else 0
        if cur > best:
            best = cur
    return best


def monte_carlo(r_multiples: list[float], risk_pct: float, n_sims: int = 2000, seed: int = 12345,
                ruin_drawdown_pct: float = 50.0) -> dict | None:
    r = np.asarray([x for x in r_multiples if np.isfinite(x)], dtype=float)
    if len(r) < 10:
        return None
    rng = np.random.default_rng(seed)
    samples = rng.choice(r, size=(n_sims, len(r)), replace=True)
    growth = np.clip(1 + samples * risk_pct / 100, 1e-9, None)
    equity = np.cumprod(growth, axis=1)
    peak = np.maximum.accumulate(np.concatenate([np.ones((n_sims, 1)), equity], axis=1), axis=1)[:, 1:]
    max_dd = ((peak - equity) / peak).max(axis=1) * 100
    final_ret = (equity[:, -1] - 1) * 100
    streaks = np.array([_longest_losing_streak(row) for row in samples])

    def pct(a, q):
        return round(float(np.percentile(a, q)), 3)

    return {
        "simulations": n_sims, "trades_per_simulation": int(len(r)), "seed": seed, "risk_pct": risk_pct,
        "method": "bootstrap of trade R-multiples, fixed-fraction compounding",
        "max_drawdown_pct": {"p50": pct(max_dd, 50), "p95": pct(max_dd, 95), "p99": pct(max_dd, 99)},
        "final_return_pct": {"p5": pct(final_ret, 5), "p50": pct(final_ret, 50), "p95": pct(final_ret, 95)},
        "longest_losing_streak": {"p50": int(np.percentile(streaks, 50)), "p95": int(np.percentile(streaks, 95))},
        "probability_of_loss": round(float((final_ret < 0).mean()), 4),
        f"probability_drawdown_over_{int(ruin_drawdown_pct)}pct": round(float((max_dd > ruin_drawdown_pct).mean()), 4),
        "assumptions": "independent trades; size = risk_pct of equity; lot rounding ignored; costs inside R",
    }
