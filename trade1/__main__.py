"""CLI entry point.

    python -m trade1 bias            # show Quiver Quant bias table
    python -m trade1 scan            # one pass, print signals (paper fills)
    python -m trade1 run             # continuous loop (mode from config.yaml)
"""
from __future__ import annotations

import argparse
import logging
import sys

from .broker import BrokerError, MT5Broker, PaperBroker
from .config import load_settings
from .engine import TradingEngine
from .quiver import QuiverBias, QuiverClient


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="trade1")
    ap.add_argument("command", choices=["bias", "scan", "run"])
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    s = load_settings(args.config)

    bias = None
    if s.quiver_token:
        q = s.quiver
        bias = QuiverBias(QuiverClient(s.quiver_token), q.lookback_days,
                          q.congress_weight, q.insider_weight, q.refresh_minutes)
    elif args.command == "bias" or s.strategy.bias_mode == "require":
        print("QUIVER_API_TOKEN is not set (see .env.example).", file=sys.stderr)
        return 2

    if args.command == "bias":
        scores = sorted(bias.refresh(force=True).values(), key=lambda b: b.score, reverse=True)
        print(f"{'TICKER':<8}{'SCORE':>8}{'BUYS':>6}{'SELLS':>6}")
        for b in scores:
            if b.count >= s.auto_universe.min_events:
                print(f"{b.ticker:<8}{b.score:>+8.2f}{b.buys:>6}{b.sells:>6}")
        return 0

    mt5 = MT5Broker(s.mt5_login, s.mt5_password, s.mt5_server, s.mt5.path,
                    magic=s.mt5.magic, deviation=s.mt5.deviation)
    live = args.command == "run" and s.mode == "live"
    broker = mt5 if live else PaperBroker(s.paper_equity, data_source=mt5)
    try:
        broker.connect()
    except BrokerError as exc:
        print(f"Cannot connect to MetaTrader 5: {exc}", file=sys.stderr)
        return 3

    engine = TradingEngine(s, broker, bias)
    if args.command == "scan":
        try:
            engine.step()
        finally:
            broker.shutdown()
        return 0
    engine.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
