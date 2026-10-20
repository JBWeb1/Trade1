"""Command-line tools.

    python -m app.cli mt5-check
    python -m app.cli discover
    python -m app.cli ingest --symbol EURUSDm --timeframe H1 --start 2024-01-01 --end 2025-01-01
    python -m app.cli coverage
    python -m app.cli backtest --symbol EURUSDm --timeframe H1 --htf H4 --start 2023-01-01 --end 2025-01-01
    python -m app.cli news-check            # fetch + validate the configured news feed (no save)
    python -m app.cli news-fetch            # fetch and store
    python -m app.cli news-import --file ff_calendar_thisweek.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from .config import get_settings
from .data.ingestion import coverage, ingest_bars
from .db import session_scope
from .logging_setup import configure_logging
from .markets.service import sync_instruments
from .mt5.clock import estimate_server_offset_hours
from .mt5.constants import Timeframe
from .mt5.gateway import MT5Error, MT5Gateway


def _date(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_mt5_check(gw: MT5Gateway, _args) -> int:
    conn = gw.check_connection()
    print("Connection:")
    _print(conn)
    if not conn.get("connected"):
        print("\nNOT CONNECTED. Open MT5, log in to the Exness demo account, and check "
              "that the bottom-right status shows a ping, not 'No connection'.")
        return 1
    acct = gw.get_account_info()
    ver = gw.verify_demo_account()
    print("\nAccount:")
    _print({k: acct.get(k) for k in ("login", "server", "company", "currency", "leverage",
                                     "balance", "equity", "trade_mode")})
    print("\nDemo verification:")
    _print(ver.as_dict())
    offset = estimate_server_offset_hours(gw)
    cfg = gw.settings.mt5_server_utc_offset_hours
    print(f"\nServer clock offset: estimated {offset if offset is not None else 'unknown (markets closed)'}"
          f" h, configured {cfg} h")
    if offset is not None and abs(offset - cfg) >= 0.5:
        print("WARNING: set TRADE1_MT5_SERVER_UTC_OFFSET_HOURS to the estimated value.")
    print("\nRESULT:", "VERIFIED DEMO ACCOUNT" if ver.verified_demo else "NOT VERIFIED AS DEMO")
    return 0 if ver.verified_demo else 2


def cmd_discover(gw: MT5Gateway, args) -> int:
    with session_scope() as s:
        summary = sync_instruments(s, gw)
    _print(summary)
    return 0


def cmd_ingest(gw: MT5Gateway, args) -> int:
    with session_scope() as s:
        run = ingest_bars(s, gw, args.symbol, Timeframe(args.timeframe), _date(args.start), _date(args.end))
        out = {"run_id": run.id, "status": run.status, "error": run.error,
               "bars_received": run.bars_received, "usable": run.bars_valid,
               "inserted": run.bars_inserted, "already_present": run.bars_already_present,
               "first_bar": run.first_bar, "last_bar": run.last_bar,
               "quality_notes": (run.quality or {}).get("notes")}
    _print(out)
    return 0 if out["status"] in ("ok", "warning") else 1


def cmd_coverage(_gw, _args) -> int:
    with session_scope() as s:
        _print(coverage(s))
    return 0


def cmd_backtest(_gw, args) -> int:
    from .backtest.engine import BacktestConfig, CostModel, RiskModel
    from .backtest.service import BacktestError, instrument_spec, run_backtest
    currency = args.currency
    if currency is None:
        with session_scope() as s:
            try:
                currency = instrument_spec(s, args.symbol).get("account_currency") or "USD"
            except BacktestError as exc:
                print(f"BACKTEST NOT STARTED: {exc}")
                return 1
    cfg = BacktestConfig(
        starting_balance=args.balance, account_currency=currency, leverage=args.leverage,
        costs=CostModel(commission_per_lot_per_side=args.commission, slippage_points=args.slippage,
                        min_spread_points=args.min_spread, spread_multiplier=args.spread_multiplier),
        risk=RiskModel(risk_per_trade_pct=args.risk))
    params = json.loads(args.params) if args.params else {}
    with session_scope() as s:
        run = run_backtest(s, symbol=args.symbol, timeframe=args.timeframe, htf=args.htf,
                           start=_date(args.start), end=_date(args.end), strategy_name=args.strategy,
                           params=params, config=cfg)
        if run.status != "completed":
            print(f"BACKTEST FAILED (run {run.id}): {run.error}")
            return 1
        m = run.metrics
        print(f"\n{m['label']}\n")
        print(f"Run #{run.id}  {args.symbol} {args.timeframe}" + (f" (+{args.htf} filter)" if args.htf else "")
              + f"  {args.start} -> {args.end}  strategy {m['strategy']['name']} v{m['strategy']['version']}"
              f" [{m['strategy']['params_hash']}]")
        rows = [("Closed trades", m["closed_trades"]), ("Win rate", m["win_rate"]),
                ("Average win / loss", f"{m['average_win']} / {m['average_loss']}"),
                ("Expectancy per trade (after costs)", m["expectancy_per_trade"]),
                ("Expectancy in R", m["expectancy_r"]), ("Profit factor", m["profit_factor"]),
                ("Net profit / return %", f"{m['net_profit']} / {m['net_return_pct']}%"),
                ("Max drawdown %", m.get("max_drawdown_pct")),
                ("Max drawdown duration (days)", m.get("max_drawdown_duration_days")),
                ("Sharpe (daily, annualised, rf=0)", m.get("sharpe_daily_annualised")),
                ("Longest losing streak", m["longest_losing_streak"]),
                ("Exposure %", m["exposure_pct"]), ("Costs", m["costs"]),
                ("Exit reasons", m["exit_reasons"]), ("Rejected signals", m["rejections"])]
        for k, v in rows:
            print(f"  {k:<38} {v}")
        if m.get("sample_warning"):
            print(f"\nWARNING: {m['sample_warning']}")
        for n in m["notes"]:
            print(f"NOTE: {n}")
    return 0


def cmd_news_check(_gw, _args) -> int:
    from .integrations.news import forexfactory
    url = get_settings().news_feed_url
    if not url:
        print("TRADE1_NEWS_FEED_URL is not set.")
        return 1
    try:
        events, rep = forexfactory.fetch(url)
    except Exception as exc:  # report every failure plainly
        print(f"FEED CHECK FAILED: {exc}")
        return 1
    _print({"url": url, "received": rep.received, "accepted": rep.accepted, "rejected": rep.rejected,
            "rejection_reasons": rep.reasons[:10],
            "sample": [e.__dict__ for e in events[:3]]})
    ok = rep.accepted > 0 and rep.rejected == 0
    print("RESULT:", "FEED FORMAT MATCHES" if ok else "CHECK THE REJECTIONS ABOVE")
    return 0 if ok else 2


def cmd_news_fetch(_gw, _args) -> int:
    from .integrations.news.service import refresh_from_feed
    with session_scope() as s:
        _print(refresh_from_feed(s, get_settings()))
    return 0


def cmd_news_import(_gw, args) -> int:
    from pathlib import Path

    from .integrations.news import forexfactory
    from .integrations.news.store import save_events
    events, rep = forexfactory.parse_payload(Path(args.file).read_text(encoding="utf-8-sig"))
    with session_scope() as s:
        _print(save_events(s, events, rep, source=f"file:{Path(args.file).name}"))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("mt5-check")
    sub.add_parser("discover")
    p = sub.add_parser("ingest")
    p.add_argument("--symbol", required=True)
    p.add_argument("--timeframe", required=True, choices=[t.value for t in Timeframe])
    p.add_argument("--start", required=True, help="UTC date/time, e.g. 2024-01-01")
    p.add_argument("--end", required=True)
    sub.add_parser("coverage")
    p = sub.add_parser("backtest")
    p.add_argument("--symbol", required=True)
    p.add_argument("--timeframe", required=True, choices=[t.value for t in Timeframe])
    p.add_argument("--htf", choices=[t.value for t in Timeframe], default=None)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--strategy", default="trend_continuation")
    p.add_argument("--params", default="", help='JSON, e.g. {"fast_ema": 20}')
    p.add_argument("--balance", type=float, default=10_000)
    p.add_argument("--currency", default=None, help="defaults to the MT5 account currency")
    p.add_argument("--leverage", type=float, default=100)
    p.add_argument("--risk", type=float, default=0.25, help="percent of equity per trade")
    p.add_argument("--commission", type=float, default=0.0, help="per lot per side, account currency")
    p.add_argument("--slippage", type=float, default=0.0, help="points")
    p.add_argument("--min-spread", type=float, default=0.0, help="points")
    p.add_argument("--spread-multiplier", type=float, default=1.0)
    sub.add_parser("news-check")
    sub.add_parser("news-fetch")
    p = sub.add_parser("news-import")
    p.add_argument("--file", required=True)
    args = ap.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    gw = MT5Gateway(settings)
    handlers = {"mt5-check": cmd_mt5_check, "discover": cmd_discover, "ingest": cmd_ingest,
                "coverage": cmd_coverage, "backtest": cmd_backtest, "news-check": cmd_news_check,
                "news-fetch": cmd_news_fetch, "news-import": cmd_news_import}
    try:
        return handlers[args.cmd](gw, args)
    except MT5Error as exc:
        print(f"MT5 error: {exc}", file=sys.stderr)
        return 3
    except (RuntimeError, ValueError, OSError) as exc:  # news disabled, bad feed, bad file, bad dates
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        gw.shutdown_terminal()


if __name__ == "__main__":
    sys.exit(main())
