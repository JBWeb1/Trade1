"""MT5 enumerations used by the app (values from the MetaTrader 5 documentation)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Timeframe(str, Enum):
    M1 = "M1"
    M15 = "M15"
    H1 = "H1"
    H4 = "H4"


@dataclass(frozen=True)
class TimeframeSpec:
    minutes: int
    mt5_const: str


TIMEFRAMES: dict[Timeframe, TimeframeSpec] = {
    Timeframe.M1: TimeframeSpec(1, "TIMEFRAME_M1"),
    Timeframe.M15: TimeframeSpec(15, "TIMEFRAME_M15"),
    Timeframe.H1: TimeframeSpec(60, "TIMEFRAME_H1"),
    Timeframe.H4: TimeframeSpec(240, "TIMEFRAME_H4"),
}

# ENUM_ACCOUNT_TRADE_MODE
ACCOUNT_TRADE_MODE_DEMO = 0
ACCOUNT_TRADE_MODE_CONTEST = 1
ACCOUNT_TRADE_MODE_REAL = 2
ACCOUNT_TRADE_MODES = {0: "DEMO", 1: "CONTEST", 2: "REAL"}

# ENUM_SYMBOL_TRADE_MODE
SYMBOL_TRADE_MODES = {0: "DISABLED", 1: "LONGONLY", 2: "SHORTONLY", 3: "CLOSEONLY", 4: "FULL"}

# ENUM_SYMBOL_TRADE_EXECUTION
SYMBOL_EXECUTION_MODES = {0: "REQUEST", 1: "INSTANT", 2: "MARKET", 3: "EXCHANGE"}

# ENUM_SYMBOL_CALC_MODE
SYMBOL_CALC_MODES = {
    0: "FOREX", 1: "FOREX_NO_LEVERAGE", 2: "FUTURES", 3: "CFD", 4: "CFDINDEX",
    5: "CFDLEVERAGE", 32: "EXCH_STOCKS", 33: "EXCH_FUTURES",
    34: "EXCH_FUTURES_FORTS", 35: "EXCH_OPTIONS", 36: "EXCH_OPTIONS_MARGIN",
    37: "EXCH_BONDS", 38: "EXCH_STOCKS_MOEX", 39: "EXCH_BONDS_MOEX", 64: "SERV_COLLATERAL",
}

# SYMBOL_FILLING_MODE flags
FILLING_FLAGS = {1: "FOK", 2: "IOC", 4: "BOC"}
# SYMBOL_ORDER_MODE flags
ORDER_MODE_FLAGS = {1: "MARKET", 2: "LIMIT", 4: "STOP", 8: "STOP_LIMIT", 16: "SL", 32: "TP",
                    64: "CLOSE_BY"}
# ENUM_SYMBOL_SWAP_MODE
SWAP_MODES = {0: "DISABLED", 1: "POINTS", 2: "CURRENCY_SYMBOL", 3: "CURRENCY_MARGIN",
              4: "CURRENCY_DEPOSIT", 5: "INTEREST_CURRENT", 6: "INTEREST_OPEN",
              7: "REOPEN_CURRENT", 8: "REOPEN_BID"}


def flags(value: int | None, table: dict[int, str]) -> list[str]:
    if not value:
        return []
    return [name for bit, name in table.items() if value & bit]
