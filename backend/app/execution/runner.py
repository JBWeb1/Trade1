"""Forward-test runner: evaluates the enabled strategy on each newly closed bar and
hands signals to the demo execution gateway.

Uses the same strategy code and position sizing as the backtester. Bars come
straight from MT5; the bar that is still forming is dropped. Stale data, a
closed market or any MT5 error means: do nothing this cycle.
"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy.orm import Session

from ..alerts import notify
from ..backtest.engine import BacktestConfig, size_position
from ..backtest.strategies import get_strategy
from ..config import Settings
from ..models import AuditLog
from ..mt5.constants import TIMEFRAMES, Timeframe
from ..mt5.gateway import MT5Error, MT5Gateway
from . import state
from .gateway import DemoExecutionGateway

log = logging.getLogger(__name__)


def _closed_bars(gw: MT5Gateway, symbol: str, tf: Timeframe, bars: int, now: datetime) -> pd.DataFrame:
    minutes = TIMEFRAMES[tf].minutes
    df = gw.get_historical_bars(symbol, tf, now - timedelta(minutes=minutes * bars * 2), now)
    if df.empty:
        return df
    df = df.rename(columns={"spread": "spread_points"})
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df[df["time"] + pd.Timedelta(minutes=minutes) <= pd.Timestamp(now)].reset_index(drop=True)


def run_once(session: Session, settings: Settings, gw: MT5Gateway, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    st = state.demo_state(session)
    if not st.get("enabled"):
        return {"action": "idle", "reason": "demo trading not enabled"}
    if state.emergency_active(session):
        return {"action": "idle", "reason": "emergency stop active"}
    tf = Timeframe(st["timeframe"])
    htf = Timeframe(st["htf"]) if st.get("htf") else None
    minutes = TIMEFRAMES[tf].minutes
    strategy = get_strategy(st["strategy"], st["params"])
    need = strategy.warmup_bars() + 300

    primary = _closed_bars(gw, st["symbol"], tf, need, now)
    if primary.empty:
        return {"action": "idle", "reason": "no bars from MT5"}
    last_bar = primary["time"].iloc[-1]
    if st.get("last_processed_bar") == last_bar.isoformat():
        return {"action": "idle", "reason": "no new closed bar"}
    if now - (last_bar + pd.Timedelta(minutes=minutes)).to_pydatetime() > timedelta(minutes=minutes * 2):
        return {"action": "idle", "reason": f"data stale or market closed (last bar {last_bar.isoformat()})"}
    htf_df = _closed_bars(gw, st["symbol"], htf, 400, now) if htf else None

    spec = dict(_spec_for(session, gw, st["symbol"]))
    strategy.instrument = spec
    df = strategy.prepare(primary, htf_df, minutes, TIMEFRAMES[htf].minutes if htf else None)
    i = len(df) - 1
    execu = DemoExecutionGateway(settings, gw)
    result: dict = {"bar": last_bar.isoformat()}

    mine = [p for p in gw.get_open_positions()
            if p.get("magic") == settings.demo_magic and p.get("symbol") == st["symbol"]]
    if mine:
        p = mine[0]
        side = "buy" if p.get("type") == gw.mt5.POSITION_TYPE_BUY else "sell"
        reason = strategy.calculate_exit(i, side)
        if reason:
            intent = execu.close_position(session, key=f"close|{p['ticket']}|{last_bar.isoformat()}",
                                          ticket=p["ticket"], reason=reason)
            result.update(action="close", status=intent.status, reason=reason)
        else:
            result.update(action="hold")
    else:
        sig = strategy.generate_signal(i)
        if sig is None:
            result.update(action="no_signal")
        else:
            result.update(_act_on_signal(session, settings, gw, execu, st, spec, sig, last_bar))

    st = state.demo_state(session)
    st["last_processed_bar"] = last_bar.isoformat()
    st["last_cycle"] = {"at": now.isoformat(), **{k: v for k, v in result.items() if k != "bar"}}
    state.put(session, state.DEMO_STATE, st)
    return result


def _spec_for(session: Session, gw: MT5Gateway, symbol: str) -> dict:
    """Live contract spec from the terminal (same shape as discovery stores)."""
    from ..markets.discovery import build_spec
    return asdict(build_spec(gw.get_symbol_info(symbol), gw.get_account_info().get("currency")))


def _act_on_signal(session, settings, gw, execu, st, spec, sig, last_bar) -> dict:
    risk_cfg = (st.get("risk_config") or {})
    if risk_cfg.get("news_blackout"):
        from ..backtest.service import news_for
        news, _weeks = news_for(session, BacktestConfig(**risk_cfg), last_bar, last_bar + pd.Timedelta(days=1))
        if news is not None:
            blk = news.check(spec, datetime.now(timezone.utc))
            if blk.blocked:
                return {"action": "skip", "reason": f"news blackout: {blk.reason}"}
    tick = gw.get_market_tick(st["symbol"])
    if tick is None:
        return {"action": "skip", "reason": "no quote"}
    entry = tick["ask"] if sig.side == "buy" else tick["bid"]
    tp = sig.take_profit
    if sig.target_r and (entry - sig.stop_loss) * (1 if sig.side == "buy" else -1) > 0:
        r = abs(entry - sig.stop_loss) * sig.target_r
        tp = entry + r if sig.side == "buy" else entry - r
    acct = gw.get_account_info()
    commission = ((risk_cfg.get("costs") or {}).get("commission_per_lot_per_side") or 0.0)
    volume = size_position(spec, entry, sig.stop_loss, float(acct["equity"]), settings.demo_risk_per_trade_pct,
                           commission)
    if volume <= 0:
        session.add(AuditLog(actor="runner", action="signal.skipped",
                             details={"reason": "minimum volume exceeds risk budget", "bar": last_bar.isoformat()}))
        return {"action": "skip", "reason": "minimum volume exceeds risk budget"}
    key = f"{st['strategy_version_id']}|{st['symbol']}|{st['timeframe']}|{last_bar.isoformat()}|{sig.side}"
    intent = execu.open_position(session, key=key, symbol=st["symbol"], side=sig.side, volume=volume,
                                 stop_loss=sig.stop_loss, take_profit=tp,
                                 signal={**sig.as_dict(), "bar_time": last_bar.isoformat(), "entry_reference": entry,
                                         "planned_risk_money": abs(entry - sig.stop_loss) / spec["tick_size"]
                                         * spec["tick_value"] * volume},
                                 strategy_version_id=st["strategy_version_id"])
    return {"action": "open", "status": intent.status, "intent_id": intent.id, "reason": intent.reason}


def run_forever(settings: Settings, gw: MT5Gateway, session_factory, interval_seconds: int = 20) -> None:
    """Loop until interrupted. Every cycle runs in its own transaction."""
    failures = 0
    notify(settings, "Demo runner started", "info")
    while True:
        try:
            with session_factory() as s:
                res = run_once(s, settings, gw)
                if res.get("action") not in ("idle", None):
                    log.info("cycle: %s", res)
                with_issue = state.unresolved_reconciliation(s)
            failures = 0
            if with_issue:
                log.warning("%s unresolved reconciliation event(s): new entries blocked", with_issue)
        except MT5Error as exc:
            failures += 1
            log.warning("MT5 problem: %s", exc)
            if failures in (3, 30):
                notify(settings, f"MT5 connection problem ({failures} cycles): {exc}", "warning")
        except Exception as exc:  # keep running; never act on a half-finished cycle
            log.exception("cycle failed")
            notify(settings, f"Runner error: {exc}", "warning")
        time.sleep(interval_seconds)
