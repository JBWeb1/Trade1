"""A FakeMT5 that can 'trade', for execution tests only. Records every order_send."""
from __future__ import annotations

import itertools
from collections import namedtuple
from datetime import datetime, timezone

from .fake_mt5 import FakeMT5

Result = namedtuple("Result", "retcode deal order volume price bid ask comment request_id")
CheckResult = namedtuple("CheckResult", "retcode balance equity profit margin margin_free margin_level comment")
Position = namedtuple("Position", "ticket time type magic identifier volume price_open sl tp price_current "
                                  "swap profit symbol comment")
Order = namedtuple("Order", "ticket symbol type magic volume_current price_open comment")


class FakeTradingMT5(FakeMT5):
    TRADE_ACTION_DEAL, TRADE_ACTION_REMOVE = 1, 8
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1

    def __init__(self, *a, send_mode: str = "fill", **kw):
        super().__init__(*a, **kw)
        self.send_mode = send_mode
        self.sent: list[dict] = []
        self.positions: list[Position] = []
        self.pending: list[Order] = []
        self.deals: list[dict] = []
        self._tickets = itertools.count(5001)
        self.account_switch_after: int | None = None  # account_info calls before switching to a REAL account
        self._account_calls = 0

    def account_info(self):
        self._account_calls += 1
        if self.account_switch_after is not None and self._account_calls > self.account_switch_after:
            return self.acct._replace(trade_mode=2, server="Exness-MT5Real8")
        return self.acct

    def order_check(self, request):
        return CheckResult(0, 10000, 10000, 0, 10, 9990, 1000, "Done")

    def _open(self, request, sl=True):
        s = self.symbols[request["symbol"]]
        ticket = next(self._tickets)
        price = s.ask if request["type"] == 0 else s.bid
        self.positions.append(Position(ticket, int(datetime.now(timezone.utc).timestamp()), request["type"],
                                       request["magic"], ticket, request["volume"], price,
                                       request["sl"] if sl else 0.0, request["tp"] if sl else 0.0, price, 0.0, 0.0,
                                       request["symbol"], request["comment"]))
        return ticket, price

    def order_send(self, request):
        self.sent.append(dict(request))
        mode = self.send_mode
        if request["action"] == self.TRADE_ACTION_REMOVE:
            self.pending = [o for o in self.pending if o.ticket != request["order"]]
            return Result(10009, 0, request["order"], 0, 0, 0, 0, "removed", 1)
        if "position" in request:  # close
            self.positions = [p for p in self.positions if p.ticket != request["position"]]
            return Result(10009, 1, 1, request["volume"], request["price"], 0, 0, "closed", 1)
        if mode == "none":
            return None
        if mode == "raise":
            raise TimeoutError("IPC timeout")
        if mode == "reject":
            return Result(10006, 0, 0, 0, 0, 0, 0, "Request rejected", 1)
        if mode == "fill_no_position":
            return Result(10009, 1, 1, request["volume"], request["price"], 0, 0, "done", 1)
        if mode == "fill_but_none":  # position opens but the call returns None (lost reply)
            self._open(request)
            return None
        ticket, price = self._open(request, sl=(mode != "fill_no_sl"))
        return Result(10009, ticket, ticket, request["volume"], price, 0, 0, "done", 1)

    def positions_get(self):
        return tuple(self.positions)

    def orders_get(self):
        return tuple(self.pending)

    def history_deals_get(self, a, b):
        return tuple(self.deals)
