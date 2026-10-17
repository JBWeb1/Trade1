"""Liquidity-sweep strategy filtered by Quiver Quant bias.

Idea: resting stop orders ("liquidity") pool just beyond recent swing highs and
lows. When price wicks through a swing level and then closes back inside the
range, those stops have been taken and price often reverses.

  * Bullish sweep: last closed bar trades below a recent swing low but closes
    back above it  -> BUY, stop under the wick, target = R multiple or the
    nearest swing high (the opposite pool of liquidity), whichever is closer.
  * Bearish sweep: the mirror image -> SELL.

A trade is only taken if the market is liquid enough (average volume, ATR
range) and, depending on `bias_mode`, agrees with the Quiver Quant bias.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Bar, Side, Signal


@dataclass
class StrategyConfig:
    swing_strength: int = 3        # bars on each side that define a swing point
    lookback: int = 60             # bars searched for swing levels
    atr_period: int = 14
    stop_buffer_atr: float = 0.2   # extra stop distance beyond the wick, in ATR
    reward_risk: float = 2.0
    min_reward_risk: float = 1.2   # skip if opposite liquidity is closer than this
    min_avg_volume: float = 0.0    # liquidity filter on average bar volume
    min_atr_pct: float = 0.0005    # ATR / price; avoid dead markets
    max_atr_pct: float = 0.05      # and avoid chaos
    bias_mode: str = "require"     # require | boost | ignore
    bias_threshold: float = 0.2    # |score| needed to count as a bias


def atr(bars: list[Bar], period: int) -> float:
    if len(bars) < period + 1:
        return 0.0
    trs = []
    for prev, cur in zip(bars[-period - 1:-1], bars[-period:]):
        trs.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
    return sum(trs) / period


def swing_highs(bars: list[Bar], strength: int) -> list[tuple[int, float]]:
    out = []
    for i in range(strength, len(bars) - strength):
        h = bars[i].high
        if all(h > bars[j].high for j in range(i - strength, i + strength + 1) if j != i):
            out.append((i, h))
    return out


def swing_lows(bars: list[Bar], strength: int) -> list[tuple[int, float]]:
    out = []
    for i in range(strength, len(bars) - strength):
        lo = bars[i].low
        if all(lo < bars[j].low for j in range(i - strength, i + strength + 1) if j != i):
            out.append((i, lo))
    return out


def _unswept(levels: list[tuple[int, float]], bars: list[Bar], above: bool) -> list[float]:
    """Levels not yet traded through by any later bar (excluding the signal bar)."""
    out = []
    for idx, lvl in levels:
        later = bars[idx + 1:]
        if above and all(b.high <= lvl for b in later):
            out.append(lvl)
        elif not above and all(b.low >= lvl for b in later):
            out.append(lvl)
    return out


class LiquiditySweepStrategy:
    def __init__(self, cfg: StrategyConfig | None = None):
        self.cfg = cfg or StrategyConfig()

    def liquidity_ok(self, bars: list[Bar]) -> tuple[bool, str]:
        c = self.cfg
        window = bars[-c.lookback:]
        avg_vol = sum(b.volume for b in window) / len(window)
        if avg_vol < c.min_avg_volume:
            return False, f"avg volume {avg_vol:.0f} < {c.min_avg_volume:.0f}"
        a = atr(bars, c.atr_period)
        price = bars[-1].close
        pct = a / price if price else 0.0
        if not (c.min_atr_pct <= pct <= c.max_atr_pct):
            return False, f"ATR% {pct:.4%} outside [{c.min_atr_pct:.4%}, {c.max_atr_pct:.4%}]"
        return True, ""

    def evaluate(self, symbol: str, bars: list[Bar], bias: float = 0.0) -> Signal | None:
        """`bars` must be closed bars, oldest first. The last bar is the signal bar."""
        c = self.cfg
        need = max(c.lookback, c.atr_period + 1) + 1
        if len(bars) < need:
            return None
        ok, _ = self.liquidity_ok(bars)
        if not ok:
            return None

        sig_bar = bars[-1]
        history = bars[-need:-1]
        a = atr(bars, c.atr_period)

        highs = _unswept(swing_highs(history, c.swing_strength), history, above=True)
        lows = _unswept(swing_lows(history, c.swing_strength), history, above=False)

        signal: Signal | None = None

        swept_lows = [l for l in lows if sig_bar.low < l < sig_bar.close]
        if swept_lows:
            level = min(swept_lows)  # deepest pool taken
            entry = sig_bar.close
            stop = sig_bar.low - c.stop_buffer_atr * a
            risk = entry - stop
            target = entry + c.reward_risk * risk
            opposite = [h for h in highs if h > entry]
            if opposite:
                target = min(target, min(opposite))
            if risk > 0 and (target - entry) / risk >= c.min_reward_risk:
                signal = Signal(symbol, Side.BUY, entry, stop, target,
                                reason=f"swept swing low {level:.5g}")

        swept_highs = [h for h in highs if sig_bar.close < h < sig_bar.high]
        if swept_highs and signal is None:
            level = max(swept_highs)
            entry = sig_bar.close
            stop = sig_bar.high + c.stop_buffer_atr * a
            risk = stop - entry
            target = entry - c.reward_risk * risk
            opposite = [l for l in lows if l < entry]
            if opposite:
                target = max(target, max(opposite))
            if risk > 0 and (entry - target) / risk >= c.min_reward_risk:
                signal = Signal(symbol, Side.SELL, entry, stop, target,
                                reason=f"swept swing high {level:.5g}")

        if signal is None:
            return None
        signal.bias_score = bias
        return self._apply_bias(signal, bias)

    def _apply_bias(self, signal: Signal, bias: float) -> Signal | None:
        c = self.cfg
        if c.bias_mode == "ignore":
            return signal
        agrees = (signal.side is Side.BUY and bias >= c.bias_threshold) or \
                 (signal.side is Side.SELL and bias <= -c.bias_threshold)
        opposes = (signal.side is Side.BUY and bias <= -c.bias_threshold) or \
                  (signal.side is Side.SELL and bias >= c.bias_threshold)
        if c.bias_mode == "require" and not agrees:
            return None
        if c.bias_mode == "boost" and opposes:
            return None
        signal.reason += f", quiver bias {bias:+.2f}"
        return signal
