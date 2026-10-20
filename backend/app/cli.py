"""Command-line tools.

    python -m app.cli mt5-check
    python -m app.cli discover
    python -m app.cli ingest --symbol EURUSDm --timeframe H1 --start 2024-01-01 --end 2025-01-01
    python -m app.cli coverage
    python -m app.cli backtest --symbol EURUSDm --timeframe H1 --htf H4 --start 2023-01-01 --end 2025-01-01
    python -m app.cli validate --symbol EURUSDm --timeframe H1 --htf H4 --start 2023-01-01 --end 2025-01-01
    python -m app.cli multi-market --symbols XAUUSDm,EURUSDm,GBPUSDm --timeframe H1 --htf H4 --start ... --end ...
    python -m app.cli portfolio --legs legs.json --start 2024-01-01 --end 2025-01-01
    python -m app.cli tick-check --backtest-id 12
    python -m app.cli demo-status | demo-run | reconcile | daily-report
    python -m app.cli emergency-stop --reason "..."
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


def _backtest_config(args):
    """Build a BacktestConfig from CLI args; None if the symbol has no stored spec."""
    from .backtest.engine import BacktestConfig, CostModel, RiskModel
    from .backtest.service import BacktestError, instrument_spec
    currency = args.currency
    if currency is None:
        with session_scope() as s:
            try:
                currency = instrument_spec(s, args.symbol).get("account_currency") or "USD"
            except BacktestError as exc:
                print(f"NOT STARTED: {exc}")
                return None
    return BacktestConfig(
        starting_balance=args.balance, account_currency=currency, leverage=args.leverage,
        costs=CostModel(commission_per_lot_per_side=args.commission, slippage_points=args.slippage,
                        min_spread_points=args.min_spread, spread_multiplier=args.spread_multiplier),
        risk=RiskModel(risk_per_trade_pct=args.risk))


def cmd_backtest(_gw, args) -> int:
    from .backtest.service import run_backtest
    cfg = _backtest_config(args)
    if cfg is None:
        return 1
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
        mc = m.get("monte_carlo")
        if mc:
            print(f"\n  Monte Carlo ({mc['simulations']} reshuffles of these trades): max drawdown median "
                  f"{mc['max_drawdown_pct']['p50']}%, 95th pct {mc['max_drawdown_pct']['p95']}%; "
                  f"losing streak 95th pct {mc['longest_losing_streak']['p95']}; "
                  f"chance of ending negative {mc['probability_of_loss'] * 100:.1f}%")
        if m.get("sample_warning"):
            print(f"\nWARNING: {m['sample_warning']}")
        for n in m["notes"]:
            print(f"NOTE: {n}")
    return 0


def cmd_validate(_gw, args) -> int:
    from .backtest.service import run_validation_report
    cfg = _backtest_config(args)
    if cfg is None:
        return 1
    params = json.loads(args.params) if args.params else {}
    print("Running validation (development / validation / out-of-sample, walk-forward, sensitivity)...")
    with session_scope() as s:
        rep = run_validation_report(s, symbol=args.symbol, timeframe=args.timeframe, htf=args.htf,
                                    start=_date(args.start), end=_date(args.end), strategy_name=args.strategy,
                                    params=params, config=cfg)
        if rep.status != "completed":
            print(f"VALIDATION FAILED TO RUN (report {rep.id}): {rep.error}")
            return 1
        r = rep.report
        print(f"\nValidation report #{rep.id}: {args.symbol} {args.timeframe} {r['strategy']['name']} "
              f"v{r['strategy']['version']} [{r['strategy']['params_hash']}]")
        print(f"Splits: {r['splits']}")
        print(f"\n  {'segment':<16}{'trades':>8}{'win%':>8}{'exp R':>9}{'PF':>8}{'ret %':>9}{'maxDD %':>9}")
        for name, m in r["segments"].items():
            wr = f"{m['win_rate'] * 100:.1f}" if m["win_rate"] is not None else "-"
            print(f"  {name:<16}{m['closed_trades']:>8}{wr:>8}{str(m['expectancy_r']):>9}"
                  f"{str(m['profit_factor']):>8}{str(m['net_return_pct']):>9}{str(m['max_drawdown_pct']):>9}")
        wf = r["walk_forward"]
        print(f"\nWalk-forward: {len(wf['folds'])} folds, {wf['oos_trades']} OOS trades, "
              f"expectancy {wf['oos_expectancy_r']} R, positive folds {wf['positive_fraction']}")
        print(f"Sensitivity (+/-{int(r['sensitivity']['step'] * 100)}%): positive fraction "
              f"{r['sensitivity']['positive_fraction']}")
        print(f"Out-of-sample t-statistic of R-multiples: {r['oos_t_stat']}")
        print("\nChecks:")
        for c in r["checks"]:
            print(f"  [{'PASS' if c['passed'] else 'FAIL'}] {c['check']}: {c['detail']}")
        print(f"\nVERDICT: {rep.verdict}")
        print(r["disclaimer"])
    return 0


def _print_research(rep) -> int:
    if rep.status != "completed":
        print(f"{rep.kind.upper()} FAILED (report {rep.id}): {rep.error}")
        return 1
    print(f"\n{rep.kind.replace('_', ' ').upper()} report #{rep.id}: {rep.summary}")
    return 0


def cmd_multi_market(_gw, args) -> int:
    from .backtest.studies import run_multi_market
    cfg = _backtest_config(argparse.Namespace(**{**vars(args), "symbol": args.symbols.split(",")[0]}))
    if cfg is None:
        return 1
    with session_scope() as s:
        rep = run_multi_market(s, symbols=[x.strip() for x in args.symbols.split(",") if x.strip()],
                               timeframe=args.timeframe, htf=args.htf, start=_date(args.start), end=_date(args.end),
                               strategy_name=args.strategy, params=json.loads(args.params) if args.params else {},
                               config=cfg, min_trades=args.min_trades)
        code = _print_research(rep)
        if code == 0:
            print(f"\n  {'symbol':<14}{'trades':>8}{'exp R':>9}{'PF':>8}{'ret %':>9}{'maxDD %':>9}")
            for r in rep.result["symbols"]:
                if "error" in r:
                    print(f"  {r['symbol']:<14}  error: {r['error']}")
                else:
                    print(f"  {r['symbol']:<14}{r['closed_trades']:>8}{str(r['expectancy_r']):>9}"
                          f"{str(r['profit_factor']):>8}{str(r['net_return_pct']):>9}{str(r['max_drawdown_pct']):>9}")
        return code


def cmd_portfolio(_gw, args) -> int:
    from pathlib import Path

    from .backtest.engine import BacktestConfig, RiskModel
    from .backtest.portfolio import PortfolioLimits
    from .backtest.studies import run_portfolio
    legs = json.loads(Path(args.legs).read_text())
    cfg = BacktestConfig(starting_balance=args.balance, risk=RiskModel(risk_per_trade_pct=args.risk))
    limits = PortfolioLimits(starting_balance=args.balance, risk_per_trade_pct=args.risk,
                             max_open_positions=args.max_positions, max_currency_exposure=args.max_currency_exposure)
    with session_scope() as s:
        rep = run_portfolio(s, legs=legs, start=_date(args.start), end=_date(args.end), config=cfg, limits=limits)
        code = _print_research(rep)
        if code == 0:
            r = rep.result
            print(f"  rejected by limits: {r['rejected']}")
            for leg in r["legs"]:
                print(f"  leg {leg['leg']}: " + (leg.get("error") or f"{leg['trades_standalone']} trades standalone, "
                                                  f"{leg['expectancy_r_standalone']} R"))
            if r.get("monte_carlo"):
                print(f"  Monte Carlo 95th-percentile drawdown: {r['monte_carlo']['max_drawdown_pct']['p95']}%")
        return code


def cmd_tick_check(gw, args) -> int:
    from .backtest.studies import run_tick_check
    with session_scope() as s:
        rep = run_tick_check(s, gw, backtest_run_id=args.backtest_id, max_trades=args.max_trades,
                             only_ambiguous=args.only_ambiguous)
        code = _print_research(rep)
        if code == 0:
            print(f"  {rep.result['coverage']}; R change if ticks are right: {rep.result['r_change_if_ticks_are_right']}")
        return code


def cmd_backup(_gw, args) -> int:
    from pathlib import Path

    from .backup import backup_database
    out = backup_database(get_settings().database_url, Path(args.dest), keep=args.keep)
    print(f"Backup written: {out}")
    return 0


def cmd_demo_status(gw, _args) -> int:
    from .execution import state
    from .execution.control import gate_requirements
    from .models import ValidationReport
    settings = get_settings()
    with session_scope() as s:
        st = state.demo_state(s)
        rep = s.get(ValidationReport, st["report_id"]) if st.get("report_id") else None
        print(f"Execution mode: {settings.execution_mode.value}   demo enabled: {bool(st.get('enabled'))}   "
              f"emergency stop: {state.emergency_active(s)}   open reconciliation: {state.unresolved_reconciliation(s)}")
        if st.get("enabled"):
            print(f"Trading {st['strategy']} [{st.get('params_hash')}] on {st['symbol']} {st['timeframe']} "
                  f"for account {st['login']}@{st['server']}; last cycle: {st.get('last_cycle')}")
        print("\nRequirements:")
        for r in gate_requirements(s, settings, gw, rep):
            print(f"  [{'PASS' if r['passed'] else 'FAIL'}] {r['requirement']}  {r['detail']}")
    return 0


def cmd_demo_run(gw, args) -> int:
    from .execution.runner import run_forever
    settings = get_settings()
    print("Demo runner started. Ctrl+C to stop. Orders only go out when every gate check passes; "
          "see `demo-status`.")
    try:
        run_forever(settings, gw, session_scope, interval_seconds=args.interval)
    except KeyboardInterrupt:
        print("Stopped.")
    return 0


def cmd_emergency_stop(gw, args) -> int:
    from .execution.control import disable_demo
    from .execution.gateway import DemoExecutionGateway
    with session_scope() as s:
        disable_demo(s, "cli", "emergency stop")
        _print(DemoExecutionGateway(get_settings(), gw).emergency_stop(s, args.reason, "cli"))
    return 0


def cmd_reconcile(gw, _args) -> int:
    from .execution.gateway import DemoExecutionGateway
    with session_scope() as s:
        _print(DemoExecutionGateway(get_settings(), gw).reconcile(s))
    return 0


def cmd_daily_report(gw, _args) -> int:
    from .alerts import notify
    from .execution import state
    from .execution.report import build_daily_report, format_report
    settings = get_settings()
    with session_scope() as s:
        if not state.demo_state(s).get("enabled_at"):
            print("Demo trading has never been enabled; nothing to report.")
            return 0
        text = format_report(build_daily_report(s, settings, gw))
    print(text)
    notify(settings, text, "info")
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
    v = sub.add_parser("validate")
    for a in p._actions:  # same options as `backtest`
        if a.dest != "help":
            v._add_action(a)
    mm = sub.add_parser("multi-market")
    for a in p._actions:
        if a.dest not in ("help", "symbol"):
            mm._add_action(a)
    mm.add_argument("--symbols", required=True, help="comma-separated, e.g. XAUUSDm,EURUSDm,GBPUSDm")
    mm.add_argument("--min-trades", type=int, default=30)
    pf = sub.add_parser("portfolio")
    pf.add_argument("--legs", required=True, help='JSON file: [{"symbol":..,"timeframe":..,"strategy":..}, ...]')
    pf.add_argument("--start", required=True)
    pf.add_argument("--end", required=True)
    pf.add_argument("--balance", type=float, default=10_000)
    pf.add_argument("--risk", type=float, default=0.25)
    pf.add_argument("--max-positions", type=int, default=3)
    pf.add_argument("--max-currency-exposure", type=int, default=2)
    tc = sub.add_parser("tick-check")
    tc.add_argument("--backtest-id", type=int, required=True)
    tc.add_argument("--max-trades", type=int, default=300)
    tc.add_argument("--only-ambiguous", action="store_true")
    bk = sub.add_parser("backup")
    bk.add_argument("--dest", default="backups")
    bk.add_argument("--keep", type=int, default=14)
    sub.add_parser("demo-status")
    dr = sub.add_parser("demo-run")
    dr.add_argument("--interval", type=int, default=20, help="seconds between cycles")
    es = sub.add_parser("emergency-stop")
    es.add_argument("--reason", default="manual emergency stop (CLI)")
    sub.add_parser("reconcile")
    sub.add_parser("daily-report")
    sub.add_parser("news-check")
    sub.add_parser("news-fetch")
    p = sub.add_parser("news-import")
    p.add_argument("--file", required=True)
    args = ap.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    gw = MT5Gateway(settings)
    handlers = {"mt5-check": cmd_mt5_check, "discover": cmd_discover, "ingest": cmd_ingest,
                "coverage": cmd_coverage, "backtest": cmd_backtest, "validate": cmd_validate,
                "multi-market": cmd_multi_market, "portfolio": cmd_portfolio, "tick-check": cmd_tick_check, "backup": cmd_backup, "demo-status": cmd_demo_status,
                "demo-run": cmd_demo_run, "emergency-stop": cmd_emergency_stop, "reconcile": cmd_reconcile,
                "daily-report": cmd_daily_report, "news-check": cmd_news_check,
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
