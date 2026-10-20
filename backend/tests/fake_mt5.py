"""Deterministic stand-in for the `MetaTrader5` module, for unit tests only.

Return types mirror the real package: named tuples for info objects and numpy
structured arrays for rates/ticks. It has NO order_send/order_check, and any
attempt to reach them fails the test.
"""
from __future__ import annotations

from collections import namedtuple
from datetime import datetime, timezone

import numpy as np
import pandas as pd

TerminalInfo = namedtuple("TerminalInfo", "connected trade_allowed tradeapi_disabled name company "
                          "build ping_last community_account maxbars codepage language path")
AccountInfo = namedtuple("AccountInfo", "login trade_mode leverage limit_orders margin_so_mode "
                         "trade_allowed trade_expert margin_mode currency_digits balance credit "
                         "profit equity margin margin_free margin_level margin_so_call "
                         "margin_so_so currency server company name")
SYMBOL_FIELDS = ("name path description currency_base currency_profit currency_margin digits point "
                 "trade_tick_size trade_tick_value trade_tick_value_profit trade_tick_value_loss "
                 "trade_contract_size volume_min volume_max volume_step trade_mode trade_exemode "
                 "trade_calc_mode filling_mode order_mode trade_stops_level trade_freeze_level "
                 "swap_mode swap_long swap_short spread spread_float margin_initial "
                 "margin_maintenance visible select bid ask time")
SymbolInfo = namedtuple("SymbolInfo", SYMBOL_FIELDS)
Tick = namedtuple("Tick", "time bid ask last volume time_msc flags volume_real")

RATE_DTYPE = [("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"),
              ("tick_volume", "<u8"), ("spread", "<i4"), ("real_volume", "<u8")]


def symbol(name="EURUSDm", path="Forex\\Majors\\EURUSDm", base="EUR", profit="USD", **kw):
    d = dict(name=name, path=path, description=name, currency_base=base, currency_profit=profit,
             currency_margin=base, digits=5, point=0.00001, trade_tick_size=0.00001,
             trade_tick_value=1.0, trade_tick_value_profit=1.0, trade_tick_value_loss=1.0,
             trade_contract_size=100000.0, volume_min=0.01, volume_max=200.0, volume_step=0.01,
             trade_mode=4, trade_exemode=2, trade_calc_mode=0, filling_mode=2, order_mode=127,
             trade_stops_level=0, trade_freeze_level=0, swap_mode=1, swap_long=-7.0,
             swap_short=1.5, spread=10, spread_float=True, margin_initial=0.0,
             margin_maintenance=0.0, visible=True, select=True, bid=1.1, ask=1.1001,
             time=int(datetime.now(timezone.utc).timestamp()))
    d.update(kw)
    return SymbolInfo(**d)


def account(trade_mode=0, server="Exness-MT5Trial7", login=12345678, **kw):
    d = dict(login=login, trade_mode=trade_mode, leverage=200, limit_orders=1024, margin_so_mode=0,
             trade_allowed=True, trade_expert=True, margin_mode=2, currency_digits=2,
             balance=10000.0, credit=0.0, profit=0.0, equity=10000.0, margin=0.0,
             margin_free=10000.0, margin_level=0.0, margin_so_call=60.0, margin_so_so=0.0,
             currency="USD", server=server, company="Exness Technologies Ltd",
             name="SECRET HOLDER NAME")
    d.update(kw)
    return AccountInfo(**d)


class FakeMT5:
    TIMEFRAME_M1, TIMEFRAME_M15, TIMEFRAME_H1, TIMEFRAME_H4 = 1, 15, 16385, 16388
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    COPY_TICKS_ALL = -1
    _TF_MIN = {1: 1, 15: 15, 16385: 60, 16388: 240}

    def __init__(self, symbols=None, acct=None, connected=True, init_ok=True):
        self.symbols = {s.name: s for s in (symbols or [symbol()])}
        self.acct = acct if acct is not None else account()
        self.connected = connected
        self.init_ok = init_ok
        self.bars: dict[tuple[str, int], pd.DataFrame] = {}
        self.calls: list[tuple] = []
        self.initialized = False
        self.fail_rates = False

    def __getattr__(self, name):
        if name.startswith("order_send") or name.startswith("order_check"):
            raise AssertionError(f"test attempted to reach MT5.{name}")
        raise AttributeError(name)

    # lifecycle
    def initialize(self, **kwargs):
        self.calls.append(("initialize", kwargs))
        self.initialized = self.init_ok
        return self.init_ok

    def shutdown(self):
        self.initialized = False

    def last_error(self):
        return (-10004, "No IPC connection") if not self.init_ok else (1, "Success")

    def terminal_info(self):
        return TerminalInfo(self.connected, True, False, "MetaTrader 5", "Exness", 4755, 35000, False,
                            100000, 0, "English", "C:\\MT5")

    def account_info(self):
        return self.acct

    # symbols
    def symbols_get(self):
        return tuple(self.symbols.values())

    def symbol_info(self, name):
        return self.symbols.get(name)

    def symbol_select(self, name, enable):
        return name in self.symbols

    def symbol_info_tick(self, name):
        s = self.symbols.get(name)
        if s is None:
            return None
        return Tick(s.time, s.bid, s.ask, 0.0, 0, s.time * 1000, 6, 0.0)

    # history
    def set_bars(self, name: str, tf_const: int, df: pd.DataFrame):
        self.bars[(name, tf_const)] = df

    def copy_rates_range(self, name, tf, start, end):
        self.calls.append(("copy_rates_range", name, tf, start, end))
        if self.fail_rates:
            return None
        df = self.bars.get((name, tf))
        out = np.zeros(0, dtype=RATE_DTYPE)
        if df is None:
            return out
        s, e = int(start.timestamp()), int(end.timestamp())
        sel = df[(df["time"] >= s) & (df["time"] <= e)]
        out = np.zeros(len(sel), dtype=RATE_DTYPE)
        for col, _ in RATE_DTYPE:
            out[col] = sel[col].to_numpy() if col in sel else 0
        return out

    def copy_ticks_range(self, name, start, end, flags):
        return np.zeros(0, dtype=[("time", "<i8"), ("bid", "<f8"), ("ask", "<f8"),
                                  ("time_msc", "<i8")])

    # account state
    def positions_get(self):
        return ()

    def orders_get(self):
        return ()

    def history_orders_get(self, a, b):
        return ()

    def history_deals_get(self, a, b):
        return ()

    def order_calc_margin(self, action, name, volume, price):
        s = self.symbols[name]
        return volume * s.trade_contract_size * price / self.acct.leverage

    def order_calc_profit(self, action, name, volume, p_open, p_close):
        s = self.symbols[name]
        sign = 1 if action == self.ORDER_TYPE_BUY else -1
        return sign * (p_close - p_open) / s.trade_tick_size * s.trade_tick_value * volume


def make_rates(start: datetime, n: int, tf_minutes: int, price: float = 1.1,
               skip: set[int] | None = None) -> pd.DataFrame:
    """Regular synthetic bars for tests (never used outside tests)."""
    t0 = int(start.timestamp())
    rows = []
    for i in range(n):
        if skip and i in skip:
            continue
        p = price + 0.0001 * (i % 7)
        rows.append({"time": t0 + i * tf_minutes * 60, "open": p, "high": p + 0.0005,
                     "low": p - 0.0005, "close": p + 0.0001, "tick_volume": 100 + i, "spread": 8,
                     "real_volume": 0})
    return pd.DataFrame(rows)
