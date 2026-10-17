"""Broker adapters.

* MT5Broker   - live trading through a running MetaTrader 5 terminal
                (official `MetaTrader5` Python package, Windows only).
* PaperBroker - simulates fills; pulls market data from another broker
                (e.g. MT5 for real prices) or from in-memory bars for tests.
"""
from __future__ import annotations

import itertools
import logging
from datetime import datetime, timezone
from typing import Protocol

from .models import Bar, OrderResult, Position, Quote, Side, Signal, SymbolInfo

log = logging.getLogger(__name__)


class Broker(Protocol):
    def connect(self) -> None: ...
    def shutdown(self) -> None: ...
    def equity(self) -> float: ...
    def symbol_info(self, symbol: str) -> SymbolInfo: ...
    def quote(self, symbol: str) -> Quote: ...
    def bars(self, symbol: str, timeframe: str, count: int) -> list[Bar]: ...
    def positions(self) -> list[Position]: ...
    def market_order(self, signal: Signal, volume: float) -> OrderResult: ...


class BrokerError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# MetaTrader 5
# --------------------------------------------------------------------------

class MT5Broker:
    """Talks to a locally running MT5 terminal.

    The terminal must be installed, logged in (or given login/password/server)
    and have "Algo Trading" enabled.
    """

    TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")

    def __init__(self, login: int | None = None, password: str | None = None,
                 server: str | None = None, path: str | None = None,
                 magic: int = 770011, deviation: int = 20, comment: str = "trade1"):
        self.login, self.password, self.server, self.path = login, password, server, path
        self.magic = magic
        self.deviation = deviation
        self.comment = comment
        self.mt5 = None

    def connect(self) -> None:
        try:
            import MetaTrader5 as mt5  # type: ignore
        except ImportError as exc:
            raise BrokerError(
                "MetaTrader5 package not available. It only runs on Windows with the "
                "MT5 terminal installed: pip install MetaTrader5") from exc
        kwargs = {}
        if self.path:
            kwargs["path"] = self.path
        if self.login:
            kwargs.update(login=int(self.login), password=self.password or "", server=self.server or "")
        if not mt5.initialize(**kwargs):
            raise BrokerError(f"MT5 initialize failed: {mt5.last_error()}")
        info = mt5.account_info()
        if info is None:
            raise BrokerError(f"MT5 not logged in: {mt5.last_error()}")
        term = mt5.terminal_info()
        if term is not None and not term.trade_allowed:
            log.warning("Algo Trading is disabled in the MT5 terminal; orders will be rejected.")
        self.mt5 = mt5
        log.info("Connected to MT5 account %s on %s (balance %.2f %s)",
                 info.login, info.server, info.balance, info.currency)

    def shutdown(self) -> None:
        if self.mt5:
            self.mt5.shutdown()

    def _require(self):
        if self.mt5 is None:
            raise BrokerError("MT5Broker not connected")
        return self.mt5

    def equity(self) -> float:
        return float(self._require().account_info().equity)

    def _select(self, symbol: str):
        mt5 = self._require()
        if not mt5.symbol_select(symbol, True):
            raise BrokerError(f"symbol {symbol!r} not available at this broker")
        info = mt5.symbol_info(symbol)
        if info is None:
            raise BrokerError(f"no symbol info for {symbol!r}")
        return info

    def symbol_info(self, symbol: str) -> SymbolInfo:
        i = self._select(symbol)
        return SymbolInfo(symbol, i.point, i.trade_tick_size, i.trade_tick_value,
                          i.volume_min, i.volume_max, i.volume_step, i.digits)

    def quote(self, symbol: str) -> Quote:
        self._select(symbol)
        t = self._require().symbol_info_tick(symbol)
        if t is None:
            raise BrokerError(f"no tick for {symbol}")
        return Quote(t.bid, t.ask)

    def bars(self, symbol: str, timeframe: str, count: int) -> list[Bar]:
        mt5 = self._require()
        if timeframe not in self.TIMEFRAMES:
            raise BrokerError(f"unsupported timeframe {timeframe}")
        self._select(symbol)
        tf = getattr(mt5, f"TIMEFRAME_{timeframe}")
        # start_pos=1 skips the bar that is still forming
        rates = mt5.copy_rates_from_pos(symbol, tf, 1, count)
        if rates is None:
            raise BrokerError(f"no rates for {symbol}: {mt5.last_error()}")
        return [Bar(datetime.fromtimestamp(int(r["time"]), tz=timezone.utc),
                    float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                    float(r["real_volume"] or r["tick_volume"]))
                for r in rates]

    def positions(self) -> list[Position]:
        mt5 = self._require()
        out = []
        for p in mt5.positions_get() or ():
            if p.magic != self.magic:
                continue  # leave manual / other-EA trades alone
            side = Side.BUY if p.type == mt5.POSITION_TYPE_BUY else Side.SELL
            out.append(Position(p.ticket, p.symbol, side, p.volume, p.price_open,
                                p.sl, p.tp, p.profit, p.comment))
        return out

    def _filling(self, info) -> int:
        mt5 = self._require()
        # symbol_info.filling_mode is a bit mask: 1 = FOK allowed, 2 = IOC allowed
        if info.filling_mode & 1:
            return mt5.ORDER_FILLING_FOK
        if info.filling_mode & 2:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def market_order(self, signal: Signal, volume: float) -> OrderResult:
        mt5 = self._require()
        info = self._select(signal.symbol)
        tick = mt5.symbol_info_tick(signal.symbol)
        buy = signal.side is Side.BUY
        price = tick.ask if buy else tick.bid
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": signal.symbol,
            "volume": float(volume),
            "type": mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": round(signal.stop, info.digits),
            "tp": round(signal.target, info.digits),
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": self.comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(info),
        }
        check = mt5.order_check(request)
        if check is None or check.retcode not in (0, mt5.TRADE_RETCODE_DONE):
            msg = getattr(check, "comment", None) or str(mt5.last_error())
            return OrderResult(False, message=f"order_check failed: {msg}", raw=request)
        res = mt5.order_send(request)
        if res is None:
            return OrderResult(False, message=f"order_send returned None: {mt5.last_error()}", raw=request)
        if res.retcode != mt5.TRADE_RETCODE_DONE:
            return OrderResult(False, message=f"retcode {res.retcode}: {res.comment}", raw=request)
        return OrderResult(True, ticket=res.order, price=res.price, message=res.comment, raw=request)


# --------------------------------------------------------------------------
# Paper trading
# --------------------------------------------------------------------------

class PaperBroker:
    """Simulated execution. Market data comes from `data_source` if given
    (any Broker, typically MT5), otherwise from bars loaded via `load_bars`."""

    def __init__(self, starting_equity: float = 10_000.0, data_source: Broker | None = None,
                 default_info: SymbolInfo | None = None):
        self._equity = starting_equity
        self.data_source = data_source
        self.default_info = default_info
        self._bars: dict[str, list[Bar]] = {}
        self._positions: list[Position] = []
        self._tickets = itertools.count(1)
        self.orders: list[tuple[Signal, float]] = []

    def connect(self) -> None:
        if self.data_source:
            self.data_source.connect()

    def shutdown(self) -> None:
        if self.data_source:
            self.data_source.shutdown()

    def load_bars(self, symbol: str, bars: list[Bar]) -> None:
        self._bars[symbol] = bars

    def equity(self) -> float:
        return self._equity

    def symbol_info(self, symbol: str) -> SymbolInfo:
        if self.data_source:
            return self.data_source.symbol_info(symbol)
        return self.default_info or SymbolInfo(symbol, 0.01, 0.01, 0.01, 0.01, 100.0, 0.01, 2)

    def quote(self, symbol: str) -> Quote:
        if self.data_source:
            return self.data_source.quote(symbol)
        c = self._bars[symbol][-1].close
        return Quote(c, c)

    def bars(self, symbol: str, timeframe: str, count: int) -> list[Bar]:
        if self.data_source:
            return self.data_source.bars(symbol, timeframe, count)
        return self._bars.get(symbol, [])[-count:]

    def positions(self) -> list[Position]:
        self._mark_to_market()
        return list(self._positions)

    def _mark_to_market(self) -> None:
        """Close paper positions whose SL or TP was touched; book the P&L."""
        still_open = []
        for p in self._positions:
            q = self.quote(p.symbol)
            info = self.symbol_info(p.symbol)
            px = q.bid if p.side is Side.BUY else q.ask
            hit = None
            if p.side is Side.BUY:
                hit = p.sl if px <= p.sl else p.tp if px >= p.tp else None
            else:
                hit = p.sl if px >= p.sl else p.tp if px <= p.tp else None
            exit_px = hit if hit is not None else px
            move = (exit_px - p.price_open) * (1 if p.side is Side.BUY else -1)
            p.profit = move / info.tick_size * info.tick_value * p.volume
            if hit is None:
                still_open.append(p)
            else:
                self._equity += p.profit
                log.info("[PAPER] closed #%d %s at %.5g, P&L %.2f", p.ticket, p.symbol, exit_px, p.profit)
        self._positions = still_open

    def market_order(self, signal: Signal, volume: float) -> OrderResult:
        q = self.quote(signal.symbol)
        price = q.ask if signal.side is Side.BUY else q.bid
        ticket = next(self._tickets)
        self._positions.append(Position(ticket, signal.symbol, signal.side, volume, price,
                                        signal.stop, signal.target, comment="paper"))
        self.orders.append((signal, volume))
        log.info("[PAPER] %s %s %.2f @ %.5g sl=%.5g tp=%.5g", signal.side.value.upper(),
                 signal.symbol, volume, price, signal.stop, signal.target)
        return OrderResult(True, ticket=ticket, price=price, message="paper fill")
