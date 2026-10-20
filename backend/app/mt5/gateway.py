"""Read-only gateway to the MetaTrader 5 terminal.

All access to the `MetaTrader5` package goes through this class. It:
  * serialises calls with a lock (the package talks to one terminal per process
    and is not safe for concurrent use),
  * converts MT5 named tuples / numpy arrays into plain dicts / DataFrames,
  * converts trade-server timestamps to UTC.

It intentionally exposes NO order-sending function. Order submission will live
only in the demo execution gateway (Phase E), behind demo verification.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from ..config import Settings
from ..logging_setup import register_secret
from .constants import TIMEFRAMES, Timeframe

log = logging.getLogger(__name__)


class MT5Error(RuntimeError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message if code is None else f"{message} (MT5 error {code})")
        self.code = code


class MT5NotAvailable(MT5Error):
    pass


def _as_dict(obj: Any) -> dict:
    if obj is None:
        return {}
    if hasattr(obj, "_asdict"):
        return dict(obj._asdict())
    return dict(obj)


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime: all timestamps must be timezone-aware UTC")
    return dt.astimezone(timezone.utc)


class MT5Gateway:
    def __init__(self, settings: Settings, module: Any | None = None):
        self.settings = settings
        self._mt5 = module
        self._lock = threading.RLock()
        self._initialized = False
        self._offset = timedelta(hours=settings.mt5_server_utc_offset_hours)
        if settings.mt5_password:
            register_secret(settings.mt5_password.get_secret_value())

    # ---------------------------------------------------------------- module
    @property
    def mt5(self):
        if self._mt5 is None:
            try:
                import MetaTrader5  # type: ignore
            except ImportError as exc:
                raise MT5NotAvailable(
                    "The MetaTrader5 Python package is not installed. It only runs on "
                    "Windows next to an installed MT5 terminal.") from exc
            self._mt5 = MetaTrader5
        return self._mt5

    def _last_error(self) -> tuple[int | None, str]:
        try:
            code, msg = self.mt5.last_error()
            return code, msg
        except Exception:  # pragma: no cover - defensive
            return None, "unknown"

    def _call(self, name: str, *args, **kwargs):
        with self._lock:
            if not self._initialized and name != "initialize":
                self.initialize_terminal()
            return getattr(self.mt5, name)(*args, **kwargs)

    # ---------------------------------------------------------------- time
    def to_server_time(self, dt: datetime) -> datetime:
        """UTC -> broker server wall-clock, expressed as a UTC-tagged datetime
        (that is how the MT5 package expects request times)."""
        return _utc(dt) + self._offset

    def from_server_epoch(self, seconds) -> pd.Series | datetime:
        if isinstance(seconds, pd.Series):
            return pd.to_datetime(seconds, unit="s", utc=True) - self._offset
        return datetime.fromtimestamp(int(seconds), tz=timezone.utc) - self._offset

    # ---------------------------------------------------------------- lifecycle
    def initialize_terminal(self) -> None:
        s = self.settings
        kwargs: dict[str, Any] = {"timeout": s.mt5_timeout_ms}
        if s.mt5_path:
            kwargs["path"] = s.mt5_path
        if s.mt5_login:
            kwargs["login"] = int(s.mt5_login)
            kwargs["password"] = s.mt5_password.get_secret_value() if s.mt5_password else ""
            kwargs["server"] = s.mt5_server or ""
        with self._lock:
            if self._initialized:
                return
            if not self.mt5.initialize(**kwargs):
                code, msg = self._last_error()
                raise MT5Error(f"MT5 initialize failed: {msg}", code)
            self._initialized = True
            log.info("MT5 terminal initialised")

    def shutdown_terminal(self) -> None:
        with self._lock:
            if self._initialized and self._mt5 is not None:
                self._mt5.shutdown()
            self._initialized = False

    def check_connection(self) -> dict:
        """Never raises: returns a status dict suitable for health checks."""
        try:
            term = self.get_terminal_info()
            acct = self.get_account_info()
        except MT5Error as exc:
            return {"connected": False, "error": str(exc)}
        return {
            "connected": bool(term.get("connected")) and bool(acct),
            "terminal_trade_allowed": bool(term.get("trade_allowed")),
            "ping_ms": (term.get("ping_last") or 0) / 1000,
            "build": term.get("build"),
        }

    # ---------------------------------------------------------------- info
    def get_terminal_info(self) -> dict:
        info = self._call("terminal_info")
        if info is None:
            code, msg = self._last_error()
            raise MT5Error(f"terminal_info failed: {msg}", code)
        d = _as_dict(info)
        return {k: d.get(k) for k in ("connected", "trade_allowed", "tradeapi_disabled", "name",
                                       "company", "build", "ping_last", "community_account",
                                       "maxbars", "codepage", "language", "path")}

    def get_account_info(self) -> dict:
        info = self._call("account_info")
        if info is None:
            code, msg = self._last_error()
            raise MT5Error(f"account_info failed (terminal not logged in?): {msg}", code)
        d = _as_dict(info)
        # never return identity/credential fields beyond what the UI needs
        keep = ("login", "trade_mode", "leverage", "limit_orders", "margin_so_mode", "trade_allowed",
                "trade_expert", "margin_mode", "currency_digits", "balance", "credit", "profit",
                "equity", "margin", "margin_free", "margin_level", "margin_so_call", "margin_so_so",
                "currency", "server", "company")
        return {k: d.get(k) for k in keep}

    def verify_demo_account(self):
        from ..safety import verify_demo_account
        try:
            term = self.get_terminal_info()
            acct = self.get_account_info()
        except MT5Error as exc:
            return verify_demo_account(None, None, self.settings, error=str(exc))
        return verify_demo_account(acct, term, self.settings)

    # ---------------------------------------------------------------- symbols
    def get_available_symbols(self) -> list[dict]:
        syms = self._call("symbols_get")
        if syms is None:
            code, msg = self._last_error()
            raise MT5Error(f"symbols_get failed: {msg}", code)
        return [_as_dict(s) for s in syms]

    def get_symbol_info(self, symbol: str) -> dict:
        info = self._call("symbol_info", symbol)
        if info is None:
            raise MT5Error(f"symbol {symbol!r} not found at this broker")
        if not info.visible:
            # history and ticks are only served for symbols in Market Watch
            self._call("symbol_select", symbol, True)
            info = self._call("symbol_info", symbol) or info
        return _as_dict(info)

    def get_market_tick(self, symbol: str) -> dict | None:
        tick = self._call("symbol_info_tick", symbol)
        if tick is None:
            return None
        d = _as_dict(tick)
        d["time_utc"] = self.from_server_epoch(d["time"])
        return d

    # ---------------------------------------------------------------- history
    def get_historical_bars(self, symbol: str, timeframe: Timeframe | str,
                            start: datetime, end: datetime) -> pd.DataFrame:
        """Bars whose open time is in [start, end] (UTC). Empty DataFrame if none.
        Never fabricates bars."""
        tf = Timeframe(timeframe)
        const = getattr(self.mt5, TIMEFRAMES[tf].mt5_const)
        self.get_symbol_info(symbol)  # ensure selected
        rates = self._call("copy_rates_range", symbol, const,
                           self.to_server_time(start), self.to_server_time(end))
        if rates is None:
            code, msg = self._last_error()
            raise MT5Error(f"copy_rates_range({symbol}, {tf.value}) failed: {msg}", code)
        df = pd.DataFrame(rates)
        if df.empty:
            return pd.DataFrame(columns=["time", "open", "high", "low", "close",
                                         "tick_volume", "spread", "real_volume"])
        df["time"] = self.from_server_epoch(df["time"])
        return df

    def get_historical_ticks(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        self.get_symbol_info(symbol)
        ticks = self._call("copy_ticks_range", symbol, self.to_server_time(start),
                           self.to_server_time(end), self.mt5.COPY_TICKS_ALL)
        if ticks is None:
            code, msg = self._last_error()
            raise MT5Error(f"copy_ticks_range({symbol}) failed: {msg}", code)
        df = pd.DataFrame(ticks)
        if not df.empty:
            df["time"] = pd.to_datetime(df["time_msc"], unit="ms", utc=True) - self._offset
        return df

    # ---------------------------------------------------------------- account state (read-only)
    def get_open_positions(self) -> list[dict]:
        return [_as_dict(p) for p in (self._call("positions_get") or ())]

    def get_open_orders(self) -> list[dict]:
        return [_as_dict(o) for o in (self._call("orders_get") or ())]

    def get_historical_orders(self, start: datetime, end: datetime) -> list[dict]:
        rows = self._call("history_orders_get", self.to_server_time(start), self.to_server_time(end))
        return [_as_dict(o) for o in (rows or ())]

    def get_historical_deals(self, start: datetime, end: datetime) -> list[dict]:
        rows = self._call("history_deals_get", self.to_server_time(start), self.to_server_time(end))
        return [_as_dict(d) for d in (rows or ())]

    # ---------------------------------------------------------------- calculators
    def _order_type(self, side: str) -> int:
        side = side.lower()
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        return self.mt5.ORDER_TYPE_BUY if side == "buy" else self.mt5.ORDER_TYPE_SELL

    def calculate_margin(self, symbol: str, side: str, volume: float, price: float) -> float:
        m = self._call("order_calc_margin", self._order_type(side), symbol, float(volume), float(price))
        if m is None:
            code, msg = self._last_error()
            raise MT5Error(f"order_calc_margin failed: {msg}", code)
        return float(m)

    def calculate_profit_in_account_currency(self, symbol: str, side: str, volume: float,
                                             price_open: float, price_close: float) -> float:
        p = self._call("order_calc_profit", self._order_type(side), symbol, float(volume),
                       float(price_open), float(price_close))
        if p is None:
            code, msg = self._last_error()
            raise MT5Error(f"order_calc_profit failed: {msg}", code)
        return float(p)
