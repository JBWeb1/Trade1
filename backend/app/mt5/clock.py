from __future__ import annotations

from datetime import datetime, timezone

HALF_HOUR = 1800
MAX_TICK_AGE_S = 120


def estimate_server_offset_hours(gateway, max_symbols: int = 200) -> float | None:
    """Estimate the trade-server clock offset from UTC using the freshest tick.

    A tick's raw time is server wall-clock, so raw_tick_time - utc_now is
    (offset - tick_age). Only ticks under two minutes old are informative,
    so this returns None when every market is closed.
    """
    now = datetime.now(timezone.utc).timestamp()
    best_offset, best_age = None, None
    for info in gateway.get_available_symbols()[:max_symbols]:
        tick = gateway._call("symbol_info_tick", info["name"])
        if tick is None or not tick.time:
            continue
        diff = tick.time - now
        offset = round(diff / HALF_HOUR) * HALF_HOUR
        age = offset - diff
        if -5 <= age <= MAX_TICK_AGE_S and (best_age is None or age < best_age):
            best_offset, best_age = offset, age
    return None if best_offset is None else best_offset / 3600
