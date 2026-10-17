"""Main trading loop: Quiver bias -> liquidity-sweep signal -> risk check -> MT5 order."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from .broker import Broker, BrokerError
from .config import Settings, SymbolMap
from .quiver import QuiverBias
from .risk import RiskManager
from .strategy import LiquiditySweepStrategy

log = logging.getLogger(__name__)


class TradingEngine:
    def __init__(self, settings: Settings, broker: Broker, bias: QuiverBias | None,
                 strategy: LiquiditySweepStrategy | None = None, risk: RiskManager | None = None):
        self.s = settings
        self.broker = broker
        self.bias = bias
        self.strategy = strategy or LiquiditySweepStrategy(settings.strategy)
        self.risk = risk or RiskManager(settings.risk)
        self._last_bar: dict[str, datetime] = {}

    def universe(self) -> list[SymbolMap]:
        symbols = list(self.s.symbols)
        au = self.s.auto_universe
        if au.enabled and self.bias:
            ranked = sorted((b for b in self.bias.refresh().values() if b.count >= au.min_events),
                            key=lambda b: abs(b.score), reverse=True)
            have = {m.ticker for m in symbols}
            for b in ranked:
                if len(symbols) >= len(self.s.symbols) + au.top_n:
                    break
                if b.ticker in have:
                    continue
                name = f"{au.mt5_prefix}{b.ticker}{au.mt5_suffix}"
                try:
                    self.broker.symbol_info(name)
                except BrokerError:
                    continue  # broker doesn't list this stock
                symbols.append(SymbolMap(b.ticker, name))
        return symbols

    def step(self) -> None:
        """Run one pass over all symbols. Only acts on newly closed bars."""
        equity = self.broker.equity()
        self.risk.start_of_day(datetime.now(timezone.utc).date(), equity)
        if self.bias:
            self.bias.refresh()

        for m in self.universe():
            try:
                self._process(m, equity)
            except BrokerError as exc:
                log.warning("%s: %s", m.mt5, exc)

    def _process(self, m: SymbolMap, equity: float) -> None:
        need = max(self.s.strategy.lookback, self.s.strategy.atr_period + 1) + 1
        bars = self.broker.bars(m.mt5, self.s.timeframe, need + 5)
        if len(bars) < need:
            log.debug("%s: not enough bars (%d)", m.mt5, len(bars))
            return
        last = bars[-1].time
        if self._last_bar.get(m.mt5) == last:
            return
        self._last_bar[m.mt5] = last

        score = self.bias.get(m.ticker).score if self.bias else 0.0
        signal = self.strategy.evaluate(m.mt5, bars, score)
        if signal is None:
            return
        log.info("SIGNAL %s %s entry=%.5g sl=%.5g tp=%.5g (%s)", signal.side.value.upper(),
                 m.mt5, signal.entry, signal.stop, signal.target, signal.reason)

        info = self.broker.symbol_info(m.mt5)
        quote = self.broker.quote(m.mt5)
        ok, why = self.risk.can_open(signal, self.broker.positions(), equity, quote.spread, info)
        if not ok:
            log.info("  skipped: %s", why)
            return
        volume = self.risk.position_size(signal, info, equity)
        if volume <= 0:
            log.info("  skipped: stop too wide for risk budget at min lot %.2f", info.volume_min)
            return
        res = self.broker.market_order(signal, volume)
        if res.ok:
            log.info("  FILLED ticket=%s vol=%.2f @ %s", res.ticket, volume, res.price)
        else:
            log.error("  order rejected: %s", res.message)

    def run(self) -> None:
        log.info("Engine started in %s mode, timeframe %s", self.s.mode.upper(), self.s.timeframe)
        try:
            while True:
                try:
                    self.step()
                except Exception:  # keep the bot alive; log and retry next poll
                    log.exception("step failed")
                time.sleep(self.s.poll_seconds)
        except KeyboardInterrupt:
            log.info("Stopping.")
        finally:
            self.broker.shutdown()
