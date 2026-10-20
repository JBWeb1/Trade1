"""Demo execution gateway: the ONLY module in Trade1 that may call MT5 order_send.

Safety model (fail closed at every step):
  1. Pre-flight checks, re-evaluated for every order, never cached:
     execution mode DEMO (from the server's .env, not the browser), demo trading
     enabled, emergency stop off, no unresolved reconciliation events, account
     verified DEMO and identical (login + server) to the account demo trading was
     enabled on, instrument metadata valid, fresh quote, spread and stop-distance
     limits, volume limits, open-position / daily-loss / drawdown limits, margin.
  2. Idempotency: an order intent with the same key is never sent twice.
  3. Inside the MT5 lock, immediately before order_send, the account's trade mode
     is read from the terminal once more. Anything but DEMO is refused.
  4. After order_send, success is only recorded once the position is found in MT5
     with its stop-loss and take-profit. No result, an exception, or a missing
     position => status "uncertain", a blocking reconciliation event, and NO retry.
There is no LIVE mode and no code path that sends an order to a non-demo account.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..alerts import notify
from ..config import ExecutionMode, Settings
from ..models import AuditLog, Instrument, OrderIntent, ReconciliationEvent, utcnow
from ..mt5.constants import ACCOUNT_TRADE_MODE_DEMO
from ..mt5.gateway import MT5Error, MT5Gateway
from . import state

log = logging.getLogger(__name__)

TRADE_RETCODE_PLACED = 10008
TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_DONE_PARTIAL = 10010
OK_RETCODES = (TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL)


class ExecutionRefused(RuntimeError):
    pass


def _as_dict(x) -> dict:
    if x is None:
        return {}
    return dict(x._asdict()) if hasattr(x, "_asdict") else dict(x)


class DemoExecutionGateway:
    def __init__(self, settings: Settings, mt5: MT5Gateway):
        self.settings = settings
        self.gw = mt5

    # ------------------------------------------------------------------ checks
    def account_checks(self, session: Session) -> list[dict]:
        """Checks that apply to any order (open, close, cancel)."""
        s = self.settings
        st = state.demo_state(session)
        checks: list[dict] = []

        def add(name, ok, detail=""):
            checks.append({"check": name, "passed": bool(ok), "detail": detail})

        add("execution mode is DEMO (server setting)", s.execution_mode == ExecutionMode.DEMO,
            f"TRADE1_EXECUTION_MODE={s.execution_mode.value}")
        add("demo trading enabled", st.get("enabled") is True, "enable it after a PASSED validation")
        add("emergency stop off", not state.emergency_active(session))
        n = state.unresolved_reconciliation(session)
        add("no unresolved reconciliation events", n == 0, f"{n} open")
        v = self.gw.verify_demo_account()
        add("terminal account verified DEMO", v.verified_demo, "; ".join(v.reasons) or (v.server or ""))
        same = v.login == st.get("login") and v.server == st.get("server")
        add("same account as when demo trading was enabled", same and st.get("login") is not None,
            f"now {v.login}@{v.server}, enabled on {st.get('login')}@{st.get('server')}")
        return checks

    def _order_checks(self, session: Session, symbol: str, side: str, volume: float,
                      sl: float, tp: float) -> tuple[list[dict], dict]:
        s = self.settings
        checks = self.account_checks(session)

        def add(name, ok, detail=""):
            checks.append({"check": name, "passed": bool(ok), "detail": detail})

        ctx: dict = {}
        if not all(c["passed"] for c in checks):
            return checks, ctx  # do not touch the market further for a refused account
        acct = self.gw.get_account_info()
        server = acct.get("server")
        inst = session.scalars(select(Instrument).where(Instrument.symbol == symbol,
                                                        Instrument.broker_server == server)).first()
        add("instrument discovered with valid metadata", inst is not None and inst.metadata_ok,
            "; ".join(inst.issues) if inst else "not discovered on this server")
        info = self.gw.get_symbol_info(symbol)
        trade_mode = info.get("trade_mode")
        allowed = trade_mode == 4 or (trade_mode == 1 and side == "buy") or (trade_mode == 2 and side == "sell")
        add("symbol open for this direction", allowed, f"trade_mode={trade_mode}")
        tick = self.gw.get_market_tick(symbol)
        point = info.get("point") or 0
        if tick is None:
            add("fresh quote", False, "no tick")
            return checks, ctx
        age = (datetime.now(timezone.utc) - tick["time_utc"]).total_seconds()
        add("fresh quote", age <= s.demo_max_tick_age_seconds, f"{age:.0f}s old (max {s.demo_max_tick_age_seconds}s)")
        spread_pts = (tick["ask"] - tick["bid"]) / point if point else float("inf")
        base_spread = (inst.spec.get("spread_points") if inst else None) or 1
        max_spread = base_spread * s.demo_max_spread_multiple
        add("spread within limit", spread_pts <= max_spread, f"{spread_pts:.0f} pts (max {max_spread:.0f})")
        price = tick["ask"] if side == "buy" else tick["bid"]
        ctx.update(price=price, point=point, digits=info.get("digits", 5), filling=info.get("filling_mode", 0),
                   account=acct)
        stops = (info.get("trade_stops_level") or 0) * point
        geometry = (sl < price < tp) if side == "buy" else (tp < price < sl)
        add("stop and target on the correct side of price", geometry, f"price {price}, sl {sl}, tp {tp}")
        add("stop/target beyond broker stops level", abs(price - sl) >= stops and abs(tp - price) >= stops,
            f"stops level {stops}")
        vmin, vmax, vstep = info.get("volume_min"), info.get("volume_max"), info.get("volume_step")
        on_step = vstep and abs(volume / vstep - round(volume / vstep)) < 1e-6
        add("volume valid", vmin and vmin <= volume <= vmax and on_step, f"{volume} (min {vmin}, max {vmax}, step {vstep})")

        mine = [p for p in self.gw.get_open_positions() if p.get("magic") == s.demo_magic]
        add("open positions below limit", len(mine) < s.demo_max_open_positions,
            f"{len(mine)} open (max {s.demo_max_open_positions})")
        add("no open position on this symbol", not any(p.get("symbol") == symbol for p in mine))
        eq = state.update_equity_anchors(session, float(acct["equity"]))
        day_loss = (eq["day_start_equity"] - acct["equity"]) / eq["day_start_equity"] * 100
        add("daily loss limit", day_loss < s.demo_max_daily_loss_pct,
            f"{day_loss:.2f}% today (max {s.demo_max_daily_loss_pct}%)")
        dd = (eq["peak_equity"] - acct["equity"]) / eq["peak_equity"] * 100
        add("drawdown limit", dd < s.demo_max_drawdown_pct, f"{dd:.2f}% from peak (max {s.demo_max_drawdown_pct}%)")
        try:
            margin = self.gw.calculate_margin(symbol, side, volume, price)
            add("enough free margin", margin <= float(acct.get("margin_free") or 0),
                f"needs {margin:.2f}, free {acct.get('margin_free')}")
        except MT5Error as exc:
            add("enough free margin", False, str(exc))
        return checks, ctx

    # ------------------------------------------------------------------ helpers
    def _filling(self, flags: int) -> int:
        mt5 = self.gw.mt5
        if flags & 1:
            return mt5.ORDER_FILLING_FOK
        if flags & 2:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def _assert_demo_inside_lock(self, expected_login, expected_server) -> None:
        """Called with the MT5 lock held, immediately before order_send."""
        acct = self.gw.mt5.account_info()
        if acct is None:
            raise ExecutionRefused("account_info unavailable right before sending")
        if acct.trade_mode != ACCOUNT_TRADE_MODE_DEMO or "real" in str(acct.server).lower():
            raise ExecutionRefused(f"account is not DEMO (trade_mode={acct.trade_mode}, server={acct.server})")
        if acct.login != expected_login or acct.server != expected_server:
            raise ExecutionRefused(f"account changed to {acct.login}@{acct.server}")

    def _event(self, session: Session, kind: str, intent: OrderIntent | None, details: dict) -> None:
        session.add(ReconciliationEvent(kind=kind, order_intent_id=intent.id if intent else None, details=details))
        session.flush()
        notify(self.settings, f"Reconciliation needed ({kind}): {details}. New entries are blocked.", "critical")

    def _audit(self, session: Session, action: str, intent: OrderIntent) -> None:
        session.add(AuditLog(actor="execution", action=action, details={
            "intent_id": intent.id, "key": intent.idempotency_key, "symbol": intent.symbol, "side": intent.side,
            "volume": intent.volume, "status": intent.status, "reason": intent.reason}))

    def _find_position(self, intent: OrderIntent) -> dict | None:
        tag = f"T1#{intent.id}"
        mine = [p for p in self.gw.get_open_positions()
                if p.get("magic") == self.settings.demo_magic and p.get("symbol") == intent.symbol]
        for p in mine:
            if p.get("comment") == tag:
                return p
        # some servers rewrite comments: fall back to magic + symbol + volume + side + recent open time
        want_type = self.gw.mt5.POSITION_TYPE_BUY if intent.side == "buy" else self.gw.mt5.POSITION_TYPE_SELL
        since = (intent.created_at.replace(tzinfo=timezone.utc) if intent.created_at.tzinfo is None
                 else intent.created_at) - timedelta(seconds=60)
        for p in mine:
            opened = datetime.fromtimestamp(int(p.get("time", 0)), tz=timezone.utc) - self.gw._offset
            if (p.get("type") == want_type and abs(float(p.get("volume", 0)) - intent.volume) < 1e-9
                    and opened >= since):
                return p
        return None

    # ------------------------------------------------------------------ open
    def open_position(self, session: Session, *, key: str, symbol: str, side: str, volume: float,
                      stop_loss: float, take_profit: float, signal: dict | None = None,
                      strategy_version_id: int | None = None) -> OrderIntent:
        existing = session.scalars(select(OrderIntent).where(OrderIntent.idempotency_key == key)).first()
        if existing is not None:
            return existing  # duplicate signal: never send again
        intent = OrderIntent(idempotency_key=key, kind="open", symbol=symbol, side=side, volume=volume,
                             stop_loss=stop_loss, take_profit=take_profit, signal=signal or {},
                             strategy_version_id=strategy_version_id, status="pending")
        session.add(intent)
        session.flush()

        try:
            checks, ctx = self._order_checks(session, symbol, side, volume, stop_loss, take_profit)
        except MT5Error as exc:
            checks, ctx = [{"check": "MT5 reachable", "passed": False, "detail": str(exc)}], {}
        intent.checks = checks
        failed = [c for c in checks if not c["passed"]]
        if failed:
            intent.status = "rejected"
            intent.reason = "; ".join(f"{c['check']}: {c['detail']}" for c in failed)
            self._audit(session, "order.rejected", intent)
            return intent

        st = state.demo_state(session)
        mt5 = self.gw.mt5
        digits = ctx["digits"]
        request = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": float(volume),
            "type": mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL,
            "price": ctx["price"], "sl": round(stop_loss, digits), "tp": round(take_profit, digits),
            "deviation": self.settings.demo_max_slippage_points, "magic": self.settings.demo_magic,
            "comment": f"T1#{intent.id}", "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(ctx["filling"]),
        }
        intent.request = dict(request)
        intent.requested_price = ctx["price"]
        intent.account = {"login": ctx["account"].get("login"), "server": ctx["account"].get("server"),
                          "trade_mode": ctx["account"].get("trade_mode")}

        result, error = None, None
        with self.gw._lock:
            try:
                self._assert_demo_inside_lock(st.get("login"), st.get("server"))
                check = mt5.order_check(request)
                if check is None or getattr(check, "retcode", -1) != 0:
                    intent.status = "rejected"
                    intent.reason = f"order_check refused: {getattr(check, 'comment', None) or mt5.last_error()}"
                    intent.response = _as_dict(check)
                    self._audit(session, "order.rejected", intent)
                    return intent
                result = mt5.order_send(request)
            except ExecutionRefused as exc:
                intent.status = "rejected"
                intent.reason = f"refused: {exc}"
                state.set_emergency(session, True, f"account check failed at send time: {exc}", "execution")
                self._audit(session, "order.refused_account", intent)
                notify(self.settings, f"Order refused and emergency stop engaged: {exc}", "critical")
                return intent
            except Exception as exc:  # timeout / IPC failure: outcome unknown
                error = f"{type(exc).__name__}: {exc}"

        intent.response = _as_dict(result) if result is not None else {"error": error or str(mt5.last_error())}
        if result is not None and result.retcode not in OK_RETCODES + (TRADE_RETCODE_PLACED,):
            intent.status = "rejected"
            intent.reason = f"broker rejected: retcode {result.retcode} {getattr(result, 'comment', '')}"
            self._audit(session, "order.rejected", intent)
            notify(self.settings, f"{symbol} {side} {volume} rejected by broker: {intent.reason}", "warning")
            return intent

        # Success or unknown: the account state decides, never the return value alone.
        pos = self._find_position(intent)
        if pos is None:
            intent.status = "uncertain"
            intent.reason = ("order_send gave no result" if result is None
                             else "broker reported success but no matching position was found")
            self._event(session, "order_state_uncertain", intent, {"intent_id": intent.id, "reason": intent.reason})
            self._audit(session, "order.uncertain", intent)
            return intent
        self._record_fill(session, intent, pos, ctx["point"])
        return intent

    def _record_fill(self, session: Session, intent: OrderIntent, pos: dict, point: float) -> None:
        intent.status = "filled" if intent.status == "pending" else "reconciled_filled"
        intent.position_ticket = pos.get("ticket")
        intent.fill_price = pos.get("price_open")
        if intent.requested_price and point:
            slip = (intent.fill_price - intent.requested_price) / point
            intent.slippage_points = round(slip if intent.side == "buy" else -slip, 1)
        tol = point * 2 if point else 1e-9
        sl_ok = abs(float(pos.get("sl") or 0) - intent.stop_loss) <= tol
        tp_ok = abs(float(pos.get("tp") or 0) - intent.take_profit) <= tol
        self._audit(session, "order.filled", intent)
        notify(self.settings, f"Filled {intent.side} {intent.volume} {intent.symbol} @ {intent.fill_price} "
                              f"(SL {pos.get('sl')}, TP {pos.get('tp')})", "fill")
        if not (sl_ok and tp_ok):
            self._event(session, "stop_or_target_missing", intent,
                        {"intent_id": intent.id, "ticket": pos.get("ticket"), "sl": pos.get("sl"), "tp": pos.get("tp"),
                         "expected_sl": intent.stop_loss, "expected_tp": intent.take_profit})
        if (intent.slippage_points or 0) > self.settings.demo_max_slippage_points:
            notify(self.settings, f"High slippage on {intent.symbol}: {intent.slippage_points} points", "warning")

    # ------------------------------------------------------------------ close
    def close_position(self, session: Session, *, key: str, ticket: int, reason: str) -> OrderIntent:
        existing = session.scalars(select(OrderIntent).where(OrderIntent.idempotency_key == key)).first()
        if existing is not None:
            return existing
        pos = next((p for p in self.gw.get_open_positions()
                    if p.get("ticket") == ticket and p.get("magic") == self.settings.demo_magic), None)
        intent = OrderIntent(idempotency_key=key, kind="close", symbol=(pos or {}).get("symbol", "?"),
                             side="sell" if (pos or {}).get("type") == self.gw.mt5.POSITION_TYPE_BUY else "buy",
                             volume=float((pos or {}).get("volume", 0)), position_ticket=ticket,
                             signal={"reason": reason}, status="pending")
        session.add(intent)
        session.flush()
        if pos is None:
            intent.status, intent.reason = "rejected", "position not found (already closed?) or not Trade1's"
            self._audit(session, "close.rejected", intent)
            return intent
        checks = self.account_checks(session)
        # closing is allowed during an emergency stop or open reconciliation (it reduces risk)
        blocking = [c for c in checks if not c["passed"] and c["check"] not in
                    ("emergency stop off", "no unresolved reconciliation events", "demo trading enabled")]
        intent.checks = checks
        if blocking:
            intent.status = "rejected"
            intent.reason = "; ".join(f"{c['check']}: {c['detail']}" for c in blocking)
            self._audit(session, "close.rejected", intent)
            return intent
        st = state.demo_state(session)
        mt5 = self.gw.mt5
        tick = self.gw.get_market_tick(intent.symbol) or {}
        info = self.gw.get_symbol_info(intent.symbol)
        request = {"action": mt5.TRADE_ACTION_DEAL, "symbol": intent.symbol, "volume": intent.volume,
                   "type": mt5.ORDER_TYPE_SELL if intent.side == "sell" else mt5.ORDER_TYPE_BUY,
                   "position": ticket, "price": tick.get("bid") if intent.side == "sell" else tick.get("ask"),
                   "deviation": self.settings.demo_max_slippage_points, "magic": self.settings.demo_magic,
                   "comment": f"T1#{intent.id} close", "type_time": mt5.ORDER_TIME_GTC,
                   "type_filling": self._filling(info.get("filling_mode", 0))}
        intent.request = dict(request)
        result, error = None, None
        with self.gw._lock:
            try:
                self._assert_demo_inside_lock(st.get("login"), st.get("server"))
                result = mt5.order_send(request)
            except ExecutionRefused as exc:
                intent.status, intent.reason = "rejected", f"refused: {exc}"
                state.set_emergency(session, True, f"account check failed at close: {exc}", "execution")
                self._audit(session, "close.refused_account", intent)
                return intent
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
        intent.response = _as_dict(result) if result is not None else {"error": error}
        still_open = any(p.get("ticket") == ticket for p in self.gw.get_open_positions())
        if not still_open:
            intent.status = "filled"
            self._audit(session, "close.filled", intent)
            notify(self.settings, f"Closed {intent.symbol} position {ticket}: {reason}", "fill")
        elif result is not None and result.retcode not in OK_RETCODES:
            intent.status, intent.reason = "rejected", f"broker rejected close: retcode {result.retcode}"
            self._audit(session, "close.rejected", intent)
        else:
            intent.status, intent.reason = "uncertain", "close sent but position still open"
            self._event(session, "close_state_uncertain", intent, {"intent_id": intent.id, "ticket": ticket})
        return intent

    # ------------------------------------------------------------------ emergency stop
    def emergency_stop(self, session: Session, reason: str, actor: str) -> dict:
        """Disable new entries immediately, then cancel Trade1's pending demo orders.
        Open positions keep their broker-side stop-loss and take-profit."""
        state.set_emergency(session, True, reason, actor)
        st = state.demo_state(session)
        cancelled, problems = [], []
        try:
            pending = [o for o in self.gw.get_open_orders() if o.get("magic") == self.settings.demo_magic]
        except MT5Error as exc:
            pending, problems = [], [f"could not list pending orders: {exc}"]
        mt5 = self.gw.mt5 if pending else None
        for o in pending:
            with self.gw._lock:
                try:
                    self._assert_demo_inside_lock(st.get("login"), st.get("server"))
                    res = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o["ticket"]})
                    (cancelled if res is not None and res.retcode in OK_RETCODES else problems).append(o["ticket"])
                except Exception as exc:
                    problems.append(f"{o['ticket']}: {exc}")
        session.add(AuditLog(actor=actor, action="system.emergency_stop",
                             details={"reason": reason, "cancelled": cancelled, "problems": problems}))
        notify(self.settings, f"EMERGENCY STOP by {actor}: {reason}. Cancelled {len(cancelled)} pending order(s).",
               "critical")
        return {"active": True, "cancelled_orders": cancelled, "problems": problems}

    # ------------------------------------------------------------------ reconciliation
    def reconcile(self, session: Session) -> dict:
        """Compare intents with what MT5 actually holds. Resolves uncertain intents when the
        broker state is unambiguous; flags positions Trade1 does not know about."""
        out = {"resolved": [], "still_uncertain": [], "unknown_positions": []}
        positions = [p for p in self.gw.get_open_positions() if p.get("magic") == self.settings.demo_magic]
        for intent in session.scalars(select(OrderIntent).where(OrderIntent.status == "uncertain",
                                                                OrderIntent.kind == "open")):
            pos = self._find_position(intent)
            if pos is not None:
                self._record_fill(session, intent, pos, self.gw.get_symbol_info(intent.symbol).get("point") or 0)
                out["resolved"].append(intent.id)
                continue
            deals = [d for d in self.gw.get_historical_deals(intent.created_at.replace(tzinfo=timezone.utc)
                                                              if intent.created_at.tzinfo is None else intent.created_at,
                                                              datetime.now(timezone.utc) + timedelta(minutes=1))
                     if d.get("comment") == f"T1#{intent.id}"
                     or (d.get("magic") == self.settings.demo_magic and d.get("symbol") == intent.symbol)]
            if not deals:
                intent.status = "reconciled_not_filled"
                intent.reason = (intent.reason or "") + "; reconciliation: no position and no deal found"
                out["resolved"].append(intent.id)
            else:
                out["still_uncertain"].append(intent.id)  # deals exist but no open position: needs a human
        known = {i.position_ticket for i in session.scalars(select(OrderIntent).where(
            OrderIntent.position_ticket.is_not(None)))}
        for p in positions:
            if p.get("ticket") not in known:
                out["unknown_positions"].append(p.get("ticket"))
        for ev in session.scalars(select(ReconciliationEvent).where(ReconciliationEvent.resolved.is_(False),
                                                                    ReconciliationEvent.kind == "order_state_uncertain")):
            if ev.order_intent_id in out["resolved"]:
                ev.resolved, ev.resolved_at = True, utcnow()
                ev.resolution = "resolved automatically: broker state is unambiguous"
        if out["unknown_positions"]:
            already = session.scalars(select(ReconciliationEvent).where(
                ReconciliationEvent.resolved.is_(False), ReconciliationEvent.kind == "unknown_positions")).all()
            if not any(sorted(e.details.get("tickets", [])) == sorted(out["unknown_positions"]) for e in already):
                self._event(session, "unknown_positions", None, {"tickets": out["unknown_positions"]})
        session.add(AuditLog(actor="execution", action="reconcile", details=out))
        session.flush()
        return out
