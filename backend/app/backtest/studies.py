"""Research studies built on the backtest engine.

* multi_market: the same strategy and parameters on several symbols. A real edge
  usually shows up on related markets, not on one symbol only.
* tick_check: re-checks backtest exits against MT5 tick data, where the broker
  provides it, instead of relying on the "stop first" bar assumption.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuditLog, BacktestRun, BacktestTrade, ResearchReport
from ..mt5.constants import TIMEFRAMES, Timeframe
from .engine import BacktestConfig
from .service import BacktestError, _load_inputs, news_for
from .strategies import get_strategy
from .validation import Inputs, _segment, _summary


def _finish(session: Session, rep: ResearchReport) -> ResearchReport:
    rep_details = {"report_id": rep.id, "kind": rep.kind, "status": rep.status, "summary": rep.summary}
    session.add(AuditLog(actor="system", action=f"research.{rep.kind}", details=rep_details))
    session.flush()
    return rep


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


# --------------------------------------------------------------------------- multi-market

def run_multi_market(session: Session, *, symbols: list[str], timeframe: str, start: datetime, end: datetime,
                     strategy_name: str, params: dict | None = None, htf: str | None = None,
                     config: BacktestConfig | None = None, min_trades: int = 30,
                     min_positive_fraction: float = 2 / 3) -> ResearchReport:
    tf = Timeframe(timeframe)
    htf_tf = Timeframe(htf) if htf else None
    config = config or BacktestConfig()
    strategy = get_strategy(strategy_name, params)
    rep = ResearchReport(kind="multi_market", params={
        "symbols": symbols, "timeframe": tf.value, "htf": htf, "start": start.isoformat(), "end": end.isoformat(),
        "strategy": strategy.get_strategy_metadata(), "config": config.model_dump(), "min_trades": min_trades})
    session.add(rep)
    session.flush()
    if len(symbols) < 2:
        rep.status, rep.error = "failed", "multi-market needs at least two symbols"
        return _finish(session, rep)

    rows = []
    news, news_weeks = news_for(session, config, start, end)
    for sym in symbols:
        try:
            spec, primary, htf_df = _load_inputs(session, sym, tf, htf_tf, start, end, strategy)
            inp = Inputs(strategy_name, strategy.params.model_dump(), primary, htf_df, TIMEFRAMES[tf].minutes,
                         TIMEFRAMES[htf_tf].minutes if htf_tf else None, spec, config, news, news_weeks)
            m = _segment(inp, strategy.params.model_dump(), start, end)
            rows.append({"symbol": sym, "category": spec.get("category"), **_summary(m)})
        except (BacktestError, ValueError) as exc:
            rows.append({"symbol": sym, "error": str(exc)})

    usable = [r for r in rows if "error" not in r and r["closed_trades"] >= min_trades]
    positive = [r for r in usable if (r["expectancy_r"] or 0) > 0]
    if len(usable) < 2:
        verdict = "INSUFFICIENT"
        reason = f"only {len(usable)} symbol(s) with >= {min_trades} trades"
    else:
        frac = len(positive) / len(usable)
        verdict = "CONSISTENT" if frac >= min_positive_fraction else "INCONSISTENT"
        reason = f"{len(positive)} of {len(usable)} symbols positive after costs ({frac:.0%}); need {min_positive_fraction:.0%}"
    rep.result = {"verdict": verdict, "reason": reason, "symbols": rows,
                  "note": "Same parameters on every symbol; nothing is tuned per symbol."}
    rep.summary = f"{verdict}: {reason}"
    rep.status = "completed"
    return _finish(session, rep)


# --------------------------------------------------------------------------- tick check

def _first_touch(ticks: pd.DataFrame, side: str, sl: float, tp: float) -> str | None:
    """Which level the tick stream reaches first: 'stop', 'target' or None."""
    if ticks.empty:
        return None
    if side == "buy":   # long positions close on the bid
        hit_sl = ticks["bid"] <= sl
        hit_tp = ticks["bid"] >= tp
    else:               # short positions close on the ask
        hit_sl = ticks["ask"] >= sl
        hit_tp = ticks["ask"] <= tp
    i_sl = int(hit_sl.to_numpy().argmax()) if hit_sl.any() else None
    i_tp = int(hit_tp.to_numpy().argmax()) if hit_tp.any() else None
    if i_sl is None and i_tp is None:
        return None
    if i_tp is None or (i_sl is not None and i_sl <= i_tp):
        return "stop"
    return "target"


def run_tick_check(session: Session, gateway, *, backtest_run_id: int, max_trades: int = 300,
                   only_ambiguous: bool = False) -> ResearchReport:
    run = session.get(BacktestRun, backtest_run_id)
    rep = ResearchReport(kind="tick_check", params={"backtest_run_id": backtest_run_id, "max_trades": max_trades,
                                                     "only_ambiguous": only_ambiguous})
    session.add(rep)
    session.flush()
    if run is None or run.status != "completed":
        rep.status, rep.error = "failed", "backtest not found or not completed"
        return _finish(session, rep)
    minutes = TIMEFRAMES[Timeframe(run.timeframe)].minutes
    q = select(BacktestTrade).where(BacktestTrade.run_id == run.id,
                                    BacktestTrade.exit_reason.in_(("stop loss", "take profit")))
    if only_ambiguous:
        q = q.where(BacktestTrade.ambiguous_bar.is_(True))
    trades = session.scalars(q.order_by(BacktestTrade.ambiguous_bar.desc(), BacktestTrade.entry_time)
                             .limit(max_trades)).all()
    counts = {"checked": 0, "confirmed": 0, "flipped_to_target": 0, "flipped_to_stop": 0,
              "no_ticks_available": 0, "levels_not_touched_in_ticks": 0}
    details = []
    r_change = 0.0
    try:
        for t in trades:
            bar_open = _utc(t.exit_time)
            start = max(bar_open, _utc(t.entry_time))
            ticks = gateway.get_historical_ticks(run.symbol, start, bar_open + timedelta(minutes=minutes))
            counts["checked"] += 1
            if ticks.empty or not {"bid", "ask"} <= set(ticks.columns):
                counts["no_ticks_available"] += 1
                continue
            first = _first_touch(ticks, t.side, t.stop_loss, t.take_profit)
            simulated = "stop" if t.exit_reason == "stop loss" else "target"
            if first is None:
                counts["levels_not_touched_in_ticks"] += 1
                outcome = "not touched"
            elif first == simulated:
                counts["confirmed"] += 1
                outcome = "confirmed"
            else:
                key = "flipped_to_target" if first == "target" else "flipped_to_stop"
                counts[key] += 1
                outcome = key
                risk = abs(t.entry_price - t.stop_loss)
                reward_r = abs(t.take_profit - t.entry_price) / risk if risk else 0
                r_change += (reward_r + 1) if first == "target" else -(reward_r + 1)
            if outcome != "confirmed" and len(details) < 100:
                details.append({"trade_id": t.id, "entry_time": t.entry_time.isoformat(), "side": t.side,
                                "simulated": simulated, "ticks_say": first, "ambiguous_bar": t.ambiguous_bar})
    except Exception as exc:  # MT5 unavailable etc.: record, never guess
        rep.status, rep.error = "failed", f"tick data unavailable: {exc}"
        return _finish(session, rep)
    with_ticks = counts["checked"] - counts["no_ticks_available"]
    rep.result = {"counts": counts, "r_change_if_ticks_are_right": round(r_change, 3), "examples": details,
                  "coverage": f"{with_ticks} of {counts['checked']} trades had tick data",
                  "note": ("MT5 keeps limited tick history; older trades often have none. 'Levels not touched' "
                           "means bar and tick data disagree, usually different price feeds or gaps.")}
    rep.summary = (f"{counts['confirmed']} confirmed, {counts['flipped_to_target']} would have hit target, "
                   f"{counts['flipped_to_stop']} would have hit stop, {counts['no_ticks_available']} without ticks")
    rep.status = "completed"
    return _finish(session, rep)


# --------------------------------------------------------------------------- portfolio

def run_portfolio(session: Session, *, legs: list[dict], start: datetime, end: datetime,
                  config: BacktestConfig | None = None, limits=None) -> ResearchReport:
    """legs: [{"symbol", "timeframe", "strategy", "params"?, "htf"?}, ...]"""
    from .engine import BacktestEngine
    from .portfolio import LegTrade, PortfolioLimits, exposure_of, leg_correlation, replay

    config = config or BacktestConfig()
    limits = limits or PortfolioLimits(starting_balance=config.starting_balance,
                                       risk_per_trade_pct=config.risk.risk_per_trade_pct)
    rep = ResearchReport(kind="portfolio", params={"legs": legs, "start": start.isoformat(), "end": end.isoformat(),
                                                   "config": config.model_dump(), "limits": limits.model_dump()})
    session.add(rep)
    session.flush()
    if not legs or len(legs) > 20:
        rep.status, rep.error = "failed", "a portfolio needs 1 to 20 legs"
        return _finish(session, rep)

    all_trades: list[LegTrade] = []
    leg_info = []
    news, news_weeks = news_for(session, config, start, end)
    for n, leg in enumerate(legs):
        name = f"{n + 1}:{leg.get('strategy')}@{leg.get('symbol')} {leg.get('timeframe')}"
        try:
            tf = Timeframe(leg["timeframe"])
            htf_tf = Timeframe(leg["htf"]) if leg.get("htf") else None
            strategy = get_strategy(leg["strategy"], leg.get("params"))
            spec, primary, htf_df = _load_inputs(session, leg["symbol"], tf, htf_tf, start, end, strategy)
            strategy.instrument = spec
            df = strategy.prepare(primary, htf_df, TIMEFRAMES[tf].minutes,
                                  TIMEFRAMES[htf_tf].minutes if htf_tf else None)
            res = BacktestEngine(strategy, spec, config, TIMEFRAMES[tf].minutes).run(
                df, start, end, news=news, news_weeks=news_weeks)
            trades = [LegTrade(name, leg["symbol"], t.side, t.entry_time, t.exit_time,
                               t.net_pnl / t.planned_risk_money, exposure_of(spec, t.side))
                      for t in res.trades if t.planned_risk_money > 0 and t.exit_time is not None]
            all_trades += trades
            leg_info.append({"leg": name, "trades_standalone": len(trades),
                             "expectancy_r_standalone": round(float(np.mean([t.r for t in trades])), 4) if trades else None,
                             "strategy": strategy.get_strategy_metadata()})
        except (BacktestError, ValueError, KeyError) as exc:
            leg_info.append({"leg": name, "error": str(exc)})

    if not all_trades:
        rep.status, rep.error = "failed", "no leg produced trades: " + "; ".join(
            f"{x['leg']}: {x.get('error', '0 trades')}" for x in leg_info)
        rep.result = {"legs": leg_info}
        return _finish(session, rep)
    result = replay(all_trades, limits)
    result["legs"] = leg_info
    result["leg_correlation_daily_r"] = leg_correlation(all_trades)
    result["method"] = ("legs backtested separately, then replayed on one account with combined limits; "
                        "position size = risk % of equity at entry, lot rounding ignored")
    rep.result = result
    rep.summary = (f"{result['accepted']} of {result['signals']} trades taken, net {result['net_return_pct']}%, "
                   f"max DD {result['max_drawdown_pct']}%")
    rep.status = "completed"
    return _finish(session, rep)
