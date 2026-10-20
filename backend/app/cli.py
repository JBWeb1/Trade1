"""Command-line tools.

    python -m app.cli mt5-check
    python -m app.cli discover
    python -m app.cli ingest --symbol EURUSDm --timeframe H1 --start 2024-01-01 --end 2025-01-01
    python -m app.cli coverage
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
    sub.add_parser("news-check")
    sub.add_parser("news-fetch")
    p = sub.add_parser("news-import")
    p.add_argument("--file", required=True)
    args = ap.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    gw = MT5Gateway(settings)
    handlers = {"mt5-check": cmd_mt5_check, "discover": cmd_discover, "ingest": cmd_ingest,
                "coverage": cmd_coverage, "news-check": cmd_news_check,
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
