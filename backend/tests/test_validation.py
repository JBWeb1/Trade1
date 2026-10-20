from datetime import timedelta

import pandas as pd
import pytest

from app.backtest.engine import BacktestConfig, BacktestEngine
from app.backtest.service import run_validation_report
from app.backtest.strategies import get_strategy
from app.backtest.validation import ValidationPolicy

from .test_backtest_service import END, START, seeded  # noqa: F401  (fixture)
from .test_no_lookahead import random_walk

SPEC = {"point": 0.00001, "tick_size": 0.00001, "tick_value": 1.0, "volume_min": 0.01,
        "volume_max": 100, "volume_step": 0.01, "swap_mode": "DISABLED"}
FAST = {"wf_folds": 3, "wf_grid_params": 1}


def test_engine_trading_window():
    h1 = random_walk(1500, 60, seed=2)
    s = get_strategy("trend_continuation", {"breakout_lookback": 5, "use_htf_filter": False})
    df = s.prepare(h1, None, 60, None)
    a, b = h1["time"].iloc[600], h1["time"].iloc[1100]
    res = BacktestEngine(s, SPEC, BacktestConfig(), 60).run(df, a, b)
    assert res.trades and all(a <= t.entry_time < b for t in res.trades)
    assert all(t.exit_time <= b for t in res.trades)
    assert res.bars == 500 and res.equity["time"].iloc[0] == a + timedelta(hours=1)
    with pytest.raises(ValueError):
        BacktestEngine(s, SPEC, BacktestConfig(), 60).run(df, b, a)


def test_validation_report_structure_and_protected_oos(seeded):  # noqa: F811
    policy = ValidationPolicy(min_trades_total=10, min_trades_validation=3, min_trades_oos=3, **FAST)
    rep = run_validation_report(seeded, symbol="EURUSDm", timeframe="H1", htf="H4", start=START, end=END,
                                strategy_name="trend_continuation", params={"breakout_lookback": 5},
                                policy=policy)
    assert rep.status == "completed", rep.error
    r = rep.report
    sp = r["splits"]
    assert sp["start"] < sp["development_end"] < sp["validation_end"] < sp["end"]
    # walk-forward and sensitivity never touch the final out-of-sample block
    for f in r["walk_forward"]["folds"]:
        assert f["out_of_sample"][1] <= sp["validation_end"]
    oos = r["segments"]["out_of_sample"]
    assert oos["first_bar_close"] > sp["validation_end"]
    # verdict is consistent with the checks
    failed = [c for c in r["checks"] if not c["passed"]]
    assert (rep.verdict == "PASSED") == (not failed)
    assert rep.verdict in ("PASSED", "FAILED", "INCONCLUSIVE") and r["disclaimer"]
    assert r["sensitivity"]["runs"] and len(r["walk_forward"]["folds"]) == 3
    assert rep.oos_prior_uses == 0

    # same OOS window with different parameters = reuse -> can never be PASSED
    rep2 = run_validation_report(seeded, symbol="EURUSDm", timeframe="H1", htf="H4", start=START, end=END,
                                 strategy_name="trend_continuation", params={"breakout_lookback": 6},
                                 policy=policy)
    assert rep2.oos_prior_uses == 1 and rep2.verdict != "PASSED"
    assert any("out-of-sample used for the first time" in x for x in rep2.reasons)


def test_small_sample_never_passes(seeded):  # noqa: F811
    rep = run_validation_report(seeded, symbol="EURUSDm", timeframe="H1", htf="H4", start=START, end=END,
                                strategy_name="liquidity_sweep", policy=ValidationPolicy(**FAST))
    assert rep.status == "completed", rep.error
    assert rep.verdict != "PASSED"  # default policy wants 200+ trades
    assert any("total trades" in x for x in rep.reasons)


def test_validation_needs_enough_data(seeded):  # noqa: F811
    rep = run_validation_report(seeded, symbol="EURUSDm", timeframe="H1", htf="H4", start=START,
                                end=START + timedelta(days=10), strategy_name="trend_continuation")
    assert rep.status == "failed" and "500" in rep.error


def test_policy_rejects_nonsense():
    with pytest.raises(ValueError):
        ValidationPolicy(development_fraction=0.95)
    with pytest.raises(ValueError):
        ValidationPolicy(wf_folds=1)


def test_verdict_precedence():
    from app.backtest.validation import decide

    def c(kind, ok):
        return {"check": kind, "passed": ok, "detail": "", "kind": kind}
    assert decide([c("sample", True), c("performance", True), c("robustness", True), c("protocol", True)]) == "PASSED"
    assert decide([c("sample", False), c("performance", True)]) == "INCONCLUSIVE"
    assert decide([c("protocol", False), c("performance", True)]) == "INCONCLUSIVE"
    assert decide([c("sample", False), c("performance", False)]) == "FAILED"
    assert decide([c("sample", True), c("robustness", False)]) == "FAILED"
