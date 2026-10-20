"""Daily forward-test report: what the demo did, compared with what validation predicted."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import AuditLog, OrderIntent
from ..mt5.gateway import MT5Gateway
from . import state

DEAL_ENTRY_OUT = (1, 3)  # OUT, OUT_BY


def _closed_trades(gw: MT5Gateway, settings: Settings, start: datetime, end: datetime) -> list[dict]:
    deals = [d for d in gw.get_historical_deals(start, end) if d.get("magic") == settings.demo_magic]
    by_pos: dict[int, dict] = {}
    for d in deals:
        p = by_pos.setdefault(d.get("position_id"), {"position": d.get("position_id"), "symbol": d.get("symbol"),
                                                      "net": 0.0, "closed": False})
        p["net"] += float(d.get("profit", 0)) + float(d.get("commission", 0)) + float(d.get("swap", 0)) \
            + float(d.get("fee", 0) or 0)
        if d.get("entry") in DEAL_ENTRY_OUT:
            p["closed"] = True
    return [p for p in by_pos.values() if p["closed"]]


def build_daily_report(session: Session, settings: Settings, gw: MT5Gateway, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    st = state.demo_state(session)
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    enabled_at = datetime.fromisoformat(st["enabled_at"]) if st.get("enabled_at") else day_start
    risk = {i.position_ticket: (i.signal or {}).get("planned_risk_money")
            for i in session.scalars(select(OrderIntent).where(OrderIntent.position_ticket.is_not(None),
                                                               OrderIntent.kind == "open"))}

    def summarize(trades: list[dict]) -> dict:
        rs = [t["net"] / risk[t["position"]] for t in trades if risk.get(t["position"])]
        n = len(trades)
        return {"closed_trades": n, "net": round(sum(t["net"] for t in trades), 2),
                "win_rate": round(sum(1 for t in trades if t["net"] > 0) / n, 4) if n else None,
                "expectancy_r": round(sum(rs) / len(rs), 4) if rs else None}

    today = summarize(_closed_trades(gw, settings, day_start, now + timedelta(minutes=1)))
    since = summarize(_closed_trades(gw, settings, enabled_at, now + timedelta(minutes=1)))
    intents_today = session.scalars(select(OrderIntent).where(OrderIntent.created_at >= day_start)).all()
    counts: dict[str, int] = {}
    for i in intents_today:
        counts[i.status] = counts.get(i.status, 0) + 1
    exp = st.get("expectation") or {}
    days = max((now - enabled_at).days, 1)
    oos_days = None
    if exp.get("first_bar_close") and exp.get("last_bar_close"):
        oos_days = max((datetime.fromisoformat(exp["last_bar_close"]) - datetime.fromisoformat(exp["first_bar_close"])).days, 1)
    expected_trades = round((exp.get("closed_trades") or 0) / oos_days * days, 1) if oos_days else None
    acct = gw.get_account_info()
    report = {
        "date": day_start.date().isoformat(), "symbol": st.get("symbol"), "strategy": st.get("strategy"),
        "account": {"login": acct.get("login"), "server": acct.get("server"), "equity": acct.get("equity"),
                    "balance": acct.get("balance"), "currency": acct.get("currency")},
        "today": today, "since_enabled": since, "orders_today": counts,
        "expected_from_validation": {"win_rate": exp.get("win_rate"), "expectancy_r": exp.get("expectancy_r"),
                                     "trades_by_now": expected_trades},
        "unresolved_reconciliation": state.unresolved_reconciliation(session),
        "emergency_stop": state.emergency_active(session),
        "note": "Forward-test results on a DEMO account. Small samples vary a lot; compare after weeks, not days.",
    }
    session.add(AuditLog(actor="system", action="report.daily", details=report))
    return report


def format_report(r: dict) -> str:
    t, s, e = r["today"], r["since_enabled"], r["expected_from_validation"]

    def pct(x):
        return "-" if x is None else f"{x * 100:.0f}%"
    return (f"Daily report {r['date']} - {r['strategy']} on {r['symbol']}\n"
            f"Equity {r['account']['equity']} {r['account']['currency']}\n"
            f"Today: {t['closed_trades']} closed, net {t['net']}, win {pct(t['win_rate'])}\n"
            f"Since enabled: {s['closed_trades']} closed (expected ~{e['trades_by_now']}), "
            f"win {pct(s['win_rate'])} vs {pct(e['win_rate'])} expected, "
            f"{s['expectancy_r']} R vs {e['expectancy_r']} R expected\n"
            f"Orders today: {r['orders_today'] or 'none'}\n"
            f"Reconciliation open: {r['unresolved_reconciliation']}; emergency stop: {r['emergency_stop']}")
