"""Validation pipeline: chronological splits, walk-forward, parameter sensitivity.

    |<-------- development -------->|<-- validation -->|<-- final OOS -->|
    |<--------------- walk-forward + sensitivity ------>|   (untouched)    |

* Splits are chronological by bar count; nothing is shuffled.
* Walk-forward (anchored): the development+validation span is cut into
  `wf_folds + 1` blocks. For fold k, parameters are chosen on blocks 0..k-1
  (small grid around the base parameters) and tested on block k.
* Sensitivity: each tunable parameter is moved by +/- `sensitivity_step`
  (one at a time) on development+validation; a robust strategy stays positive.
* The final out-of-sample block is used once per strategy name, symbol,
  timeframe and window. Re-using it (e.g. after changing parameters) is
  recorded and blocks a PASSED verdict.

All thresholds are ENGINEERING DEFAULTS for screening. Passing them does not
mean a strategy will be profitable in the future.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from .engine import BacktestConfig, BacktestEngine
from .metrics import compute_metrics
from .strategies import get_strategy


class ValidationPolicy(BaseModel):
    model_config = {"extra": "forbid"}
    development_fraction: float = Field(0.6, gt=0.2, lt=0.9)
    validation_fraction: float = Field(0.2, gt=0.05, lt=0.5)
    wf_folds: int = Field(5, ge=2, le=12)
    min_trades_total: int = Field(200, ge=1)
    min_trades_validation: int = Field(30, ge=1)
    min_trades_oos: int = Field(30, ge=1)
    min_oos_expectancy_r: float = 0.0          # must be strictly above
    min_oos_profit_factor: float = Field(1.1, ge=0)
    max_drawdown_pct: float = Field(20.0, gt=0)
    min_wf_positive_fraction: float = Field(0.6, ge=0, le=1)
    min_sensitivity_positive_fraction: float = Field(0.7, ge=0, le=1)
    sensitivity_step: float = Field(0.2, gt=0, lt=1)
    wf_grid_params: int = Field(2, ge=0, le=3)  # how many tunable params the WF grid varies
    wf_min_in_sample_trades: int = Field(10, ge=1)
    # Monte Carlo on development+validation trades: 95th-percentile drawdown must stay within limit
    mc_simulations: int = Field(2000, ge=100, le=50_000)
    max_mc_p95_drawdown_pct: float = Field(25.0, gt=0)
    # Cost stress on development+validation: spread x multiplier, plus slippage = median spread
    stress_spread_multiplier: float = Field(2.0, ge=1.0, le=10)
    stress_extra_slippage_spreads: float = Field(1.0, ge=0, le=10)


@dataclass
class Inputs:
    strategy_name: str
    params: dict
    primary: pd.DataFrame
    htf: pd.DataFrame | None
    primary_minutes: int
    htf_minutes: int | None
    spec: dict
    config: BacktestConfig
    news: object | None = None          # NewsBlackout, when config.news_blackout is on
    news_weeks: set | None = None


def _segment(inp: Inputs, params: dict, start, end, config: BacktestConfig | None = None) -> dict:
    s = get_strategy(inp.strategy_name, params)
    s.instrument = inp.spec
    df = s.prepare(inp.primary, inp.htf, inp.primary_minutes, inp.htf_minutes)
    res = BacktestEngine(s, inp.spec, config or inp.config, inp.primary_minutes).run(
        df, start, end, news=inp.news, news_weeks=inp.news_weeks)
    m = compute_metrics(res, inp.config.starting_balance)
    rs = [t.net_pnl / t.planned_risk_money for t in res.trades if t.planned_risk_money > 0]
    m["r_multiples"] = rs
    return m


def _summary(m: dict) -> dict:
    keys = ("closed_trades", "win_rate", "expectancy_per_trade", "expectancy_r", "profit_factor",
            "net_return_pct", "max_drawdown_pct", "sharpe_daily_annualised", "longest_losing_streak",
            "by_regime", "costs", "first_bar_close", "last_bar_close")
    return {k: m.get(k) for k in keys}


def _t_stat(rs: list[float]) -> float | None:
    if len(rs) < 2:
        return None
    sd = float(np.std(rs, ddof=1))
    return round(float(np.mean(rs)) / (sd / math.sqrt(len(rs))), 3) if sd > 0 else None


def _perturb(value, step: float, sign: int):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        new = round(value * (1 + sign * step))
        if new == value:
            new = value + sign
        return max(new, 1)
    return round(value * (1 + sign * step), 10)


def _valid_params(name: str, params: dict) -> bool:
    try:
        get_strategy(name, params)
        return True
    except ValueError:
        return False


def decide(checks: list[dict]) -> str:
    """FAILED if any performance/robustness check fails (a small sample cannot rescue
    a strategy that loses or is fragile); INCONCLUSIVE if only sample-size or protocol
    checks fail; PASSED only if every check passes."""
    failed = {c["kind"] for c in checks if not c["passed"]}
    if failed & {"performance", "robustness"}:
        return "FAILED"
    if failed:
        return "INCONCLUSIVE"
    return "PASSED"


def split_points(times: pd.Series, policy: ValidationPolicy) -> tuple[datetime, datetime]:
    n = len(times)
    dev_end = times.iloc[int(n * policy.development_fraction)]
    val_end = times.iloc[int(n * (policy.development_fraction + policy.validation_fraction))]
    return dev_end, val_end


def run_validation(inp: Inputs, start: datetime, end: datetime, policy: ValidationPolicy,
                   oos_prior_uses: int = 0) -> dict:
    base = get_strategy(inp.strategy_name, inp.params)
    params = base.params.model_dump()
    window = inp.primary[(inp.primary["time"] >= pd.Timestamp(start)) & (inp.primary["time"] < pd.Timestamp(end))]
    if len(window) < 500:
        raise ValueError(f"only {len(window)} bars in the validation range; need at least 500")
    dev_end, val_end = split_points(window["time"], policy)

    dev = _segment(inp, params, start, dev_end)
    val = _segment(inp, params, dev_end, val_end)
    oos = _segment(inp, params, val_end, end)

    # ---- walk-forward (anchored) on development + validation
    span = window[window["time"] < val_end]["time"].reset_index(drop=True)
    edges = [span.iloc[int(len(span) * k / (policy.wf_folds + 1))] for k in range(policy.wf_folds + 1)]
    edges.append(val_end)
    tun = [p for p in base.tunable if p in params][:policy.wf_grid_params]
    grid_axes = [[_perturb(params[p], policy.sensitivity_step, -1), params[p],
                  _perturb(params[p], policy.sensitivity_step, +1)] for p in tun]
    grid = []
    for combo in itertools.product(*grid_axes) if grid_axes else [()]:
        cand = dict(params, **dict(zip(tun, combo)))
        if _valid_params(inp.strategy_name, cand) and cand not in grid:
            grid.append(cand)
    folds = []
    for k in range(1, policy.wf_folds + 1):
        is_start, is_end, oos_start, oos_end = edges[0], edges[k], edges[k], edges[k + 1]
        best, best_score = params, -math.inf
        for cand in grid:
            m = _segment(inp, cand, is_start, is_end)
            if m["closed_trades"] >= policy.wf_min_in_sample_trades and m["expectancy_r"] is not None \
                    and m["expectancy_r"] > best_score:
                best, best_score = cand, m["expectancy_r"]
        fm = _segment(inp, best, oos_start, oos_end)
        folds.append({"fold": k, "in_sample": [is_start.isoformat(), is_end.isoformat()],
                      "out_of_sample": [oos_start.isoformat(), oos_end.isoformat()],
                      "chosen_params": {p: best[p] for p in tun},
                      "in_sample_expectancy_r": None if best_score == -math.inf else round(best_score, 4),
                      "oos": _summary(fm), "_r": fm["r_multiples"]})
    wf_r = [r for f in folds for r in f.pop("_r")]
    wf_traded = [f for f in folds if f["oos"]["closed_trades"]]
    wf_positive = [f for f in wf_traded if (f["oos"]["expectancy_r"] or 0) > 0]
    wf = {"folds": folds, "oos_trades": len(wf_r),
          "oos_expectancy_r": round(float(np.mean(wf_r)), 4) if wf_r else None,
          "positive_fraction": round(len(wf_positive) / len(wf_traded), 3) if wf_traded else None,
          "grid_params": tun, "grid_size": len(grid)}

    # ---- sensitivity on development + validation
    sens = []
    for p in base.tunable:
        if p not in params:
            continue
        for sign in (-1, 1):
            v = _perturb(params[p], policy.sensitivity_step, sign)
            cand = dict(params, **{p: v})
            if v is None or not _valid_params(inp.strategy_name, cand):
                continue
            m = _segment(inp, cand, start, val_end)
            sens.append({"param": p, "value": v, "closed_trades": m["closed_trades"],
                         "expectancy_r": m["expectancy_r"], "profit_factor": m["profit_factor"]})
    sens_valid = [x for x in sens if x["closed_trades"] and x["expectancy_r"] is not None]
    sens_pos = sum(1 for x in sens_valid if x["expectancy_r"] > 0)
    sensitivity = {"step": policy.sensitivity_step, "runs": sens,
                   "positive_fraction": round(sens_pos / len(sens_valid), 3) if sens_valid else None}

    # ---- Monte Carlo on development + validation trades
    from .research import monte_carlo
    mc = monte_carlo(dev["r_multiples"] + val["r_multiples"], inp.config.risk.risk_per_trade_pct,
                     n_sims=policy.mc_simulations)

    # ---- cost stress on development + validation
    in_window = inp.primary[(inp.primary["time"] >= pd.Timestamp(start)) & (inp.primary["time"] < val_end)]
    median_spread = float(in_window["spread_points"].median()) if len(in_window) else 0.0
    costs = inp.config.costs.model_copy(update={
        "spread_multiplier": min(10.0, inp.config.costs.spread_multiplier * policy.stress_spread_multiplier),
        "slippage_points": inp.config.costs.slippage_points + policy.stress_extra_slippage_spreads * median_spread,
    })
    stressed_cfg = inp.config.model_copy(update={"costs": costs})
    stressed = _segment(inp, params, start, val_end, stressed_cfg)
    stress = {"spread_multiplier": costs.spread_multiplier, "slippage_points": round(costs.slippage_points, 3),
              "median_recorded_spread_points": median_spread, "result": _summary(stressed)}

    # ---- verdict
    total = dev["closed_trades"] + val["closed_trades"] + oos["closed_trades"]
    checks = []

    def check(name, ok, detail, kind):
        checks.append({"check": name, "passed": bool(ok), "detail": detail, "kind": kind})

    check("total trades", total >= policy.min_trades_total, f"{total} >= {policy.min_trades_total}", "sample")
    check("validation trades", val["closed_trades"] >= policy.min_trades_validation,
          f"{val['closed_trades']} >= {policy.min_trades_validation}", "sample")
    check("out-of-sample trades", oos["closed_trades"] >= policy.min_trades_oos,
          f"{oos['closed_trades']} >= {policy.min_trades_oos}", "sample")
    check("validation expectancy after costs", (val["expectancy_r"] or -1) > 0,
          f"{val['expectancy_r']} R > 0", "performance")
    check("out-of-sample expectancy after costs", (oos["expectancy_r"] or -1) > policy.min_oos_expectancy_r,
          f"{oos['expectancy_r']} R > {policy.min_oos_expectancy_r}", "performance")
    check("out-of-sample profit factor", (oos["profit_factor"] or 0) >= policy.min_oos_profit_factor,
          f"{oos['profit_factor']} >= {policy.min_oos_profit_factor}", "performance")
    worst_dd = max((x.get("max_drawdown_pct") or 0) for x in (dev, val, oos))
    check("max drawdown in every segment", worst_dd <= policy.max_drawdown_pct,
          f"{worst_dd}% <= {policy.max_drawdown_pct}%", "performance")
    check("walk-forward consistency", (wf["positive_fraction"] or 0) >= policy.min_wf_positive_fraction,
          f"{wf['positive_fraction']} of folds positive >= {policy.min_wf_positive_fraction}", "robustness")
    check("parameter sensitivity", (sensitivity["positive_fraction"] or 0) >= policy.min_sensitivity_positive_fraction,
          f"{sensitivity['positive_fraction']} of perturbations positive >= "
          f"{policy.min_sensitivity_positive_fraction}", "robustness")
    check("Monte Carlo 95th-percentile drawdown",
          mc is not None and mc["max_drawdown_pct"]["p95"] <= policy.max_mc_p95_drawdown_pct,
          f"{mc['max_drawdown_pct']['p95'] if mc else 'n/a (<10 trades)'}% <= {policy.max_mc_p95_drawdown_pct}%",
          "robustness")
    check("still profitable with stressed costs", (stressed["expectancy_r"] or -1) > 0,
          f"{stressed['expectancy_r']} R > 0 with spread x{costs.spread_multiplier:g} and "
          f"+{costs.slippage_points:.1f} pts slippage", "robustness")
    check("final out-of-sample used for the first time", oos_prior_uses == 0,
          f"previous uses of this out-of-sample window: {oos_prior_uses}", "protocol")

    failed = [c for c in checks if not c["passed"]]
    verdict = decide(checks)
    reasons = [f"{c['check']}: {c['detail']}" for c in failed]
    if total == 0:
        # nothing was traded, so performance and robustness cannot be judged either way
        verdict = "INCONCLUSIVE"
        reasons.insert(0, "no trades in any segment - nothing to judge; see the backtest's rejected signals")

    return {
        "verdict": verdict,
        "reasons": reasons,
        "checks": checks,
        "splits": {"start": pd.Timestamp(start).isoformat(), "development_end": dev_end.isoformat(),
                   "validation_end": val_end.isoformat(), "end": pd.Timestamp(end).isoformat()},
        "segments": {"development": _summary(dev), "validation": _summary(val), "out_of_sample": _summary(oos)},
        "oos_t_stat": _t_stat(oos["r_multiples"]),
        "walk_forward": wf,
        "sensitivity": sensitivity,
        "monte_carlo": mc,
        "cost_stress": stress,
        "policy": policy.model_dump(),
        "disclaimer": ("Screening thresholds are engineering defaults. A PASSED verdict means the "
                       "strategy survived these tests on this data, not that it will be profitable. "
                       "Forward-test on demo before considering any real money."),
    }
