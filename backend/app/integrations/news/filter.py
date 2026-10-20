"""News blackout: block new entries around scheduled high-impact events.

A symbol is affected by events in its base and profit currencies, e.g.
EURUSD <- EUR, USD; XAUUSD <- USD; US30 (profit currency USD) <- USD.
This is an approximation, applied only to the categories configured in
TRADE1_NEWS_CATEGORIES (crypto is excluded by default: macro calendars are
not an established driver for every crypto CFD).
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timedelta

from .forexfactory import EconomicEvent

DEFAULT_CATEGORIES = ("forex", "metals", "energy", "indices")


def symbol_currencies(spec: dict) -> set[str]:
    out = set()
    for key in ("currency_base", "currency_profit"):
        v = (spec.get(key) or "").upper()
        if len(v) == 3 and v.isalpha():
            out.add(v)
    return out


@dataclass
class BlackoutDecision:
    blocked: bool
    reason: str = ""
    event: EconomicEvent | None = None
    applicable: bool = True  # False when the filter does not apply to this symbol


class NewsBlackout:
    def __init__(self, events: list[EconomicEvent], minutes_before: int = 30,
                 minutes_after: int = 30, impacts: tuple[str, ...] = ("high",),
                 categories: tuple[str, ...] = DEFAULT_CATEGORIES):
        self.before = timedelta(minutes=minutes_before)
        self.after = timedelta(minutes=minutes_after)
        self.categories = set(categories)
        self.events = sorted((e for e in events if e.impact in impacts), key=lambda e: e.event_time)
        self._times = [e.event_time for e in self.events]

    def check(self, spec: dict, at: datetime) -> BlackoutDecision:
        """`at` is the decision time (UTC). Only the published schedule is used,
        which was known in advance, so there is no look-ahead."""
        if spec.get("category") not in self.categories:
            return BlackoutDecision(False, "news filter not applied to this category", applicable=False)
        ccys = symbol_currencies(spec)
        # events with event_time in [at - after, at + before]
        i = bisect_left(self._times, at - self.after)
        while i < len(self.events) and self._times[i] <= at + self.before:
            e = self.events[i]
            if e.currency in ccys:
                when = "in" if e.event_time >= at else "since"
                mins = abs(int((e.event_time - at).total_seconds() // 60))
                return BlackoutDecision(True, f"{e.impact}-impact {e.currency} '{e.title}' "
                                              f"{when} {mins} min", e)
            i += 1
        return BlackoutDecision(False)
