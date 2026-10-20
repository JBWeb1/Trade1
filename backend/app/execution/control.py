"""Demo trading gate: enable / disable / status. All checks run on the server."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ExecutionMode, Settings
from ..models import AuditLog, BacktestRun, IngestionRun, Instrument, StrategyVersion, ValidationReport, utcnow
from ..mt5.gateway import MT5Gateway
from . import state

CONFIRMATION = "I understand this places automated orders on my DEMO account"


def gate_requirements(session: Session, settings: Settings, gw: MT5Gateway,
                      report: ValidationReport | None) -> list[dict]:
    req: list[dict] = []

    def add(name, ok, detail=""):
        req.append({"requirement": name, "passed": bool(ok), "detail": detail})

    add("execution mode set to DEMO on the server", settings.execution_mode == ExecutionMode.DEMO,
        f"TRADE1_EXECUTION_MODE={settings.execution_mode.value} (edit backend\\.env, restart)")
    add("validation report selected", report is not None)
    if report is not None:
        add("strategy PASSED validation", report.status == "completed" and report.verdict == "PASSED",
            f"verdict {report.verdict}")
        add("out-of-sample data used only once", report.oos_prior_uses == 0, f"previous uses: {report.oos_prior_uses}")
        cost_check = next((c for c in (report.report or {}).get("checks", [])
                           if c["check"] == "still profitable with stressed costs"), None)
        add("results include realistic and stressed costs", cost_check is not None and cost_check["passed"],
            "cost stress check" if cost_check else "report predates cost stress testing; validate again")
        wf = next((c for c in (report.report or {}).get("checks", []) if c["check"] == "walk-forward consistency"), None)
        add("walk-forward and robustness checks passed", wf is not None and wf["passed"])
        last_run = session.scalars(select(IngestionRun).where(
            IngestionRun.symbol == report.symbol, IngestionRun.timeframe == report.timeframe)
            .order_by(IngestionRun.id.desc())).first()
        add("historical data passed quality checks", last_run is not None and last_run.status in ("ok", "warning"),
            f"latest download: {last_run.status if last_run else 'none'}")
        bt = session.get(BacktestRun, report.backtest_run_id) if report.backtest_run_id else None
        add("backtest completed", bt is not None and bt.status == "completed")
    conn = gw.check_connection()
    add("MT5 connection healthy", conn.get("connected"), conn.get("error", ""))
    v = gw.verify_demo_account()
    add("terminal account positively verified as DEMO", v.verified_demo, "; ".join(v.reasons) or (v.server or ""))
    if report is not None and v.server:
        inst = session.scalars(select(Instrument).where(Instrument.symbol == report.symbol,
                                                        Instrument.broker_server == v.server)).first()
        add("instrument specification valid", inst is not None and inst.metadata_ok,
            "; ".join(inst.issues) if inst else "not discovered on this server")
    add("risk limits configured", settings.demo_risk_per_trade_pct > 0 and settings.demo_max_daily_loss_pct > 0,
        f"{settings.demo_risk_per_trade_pct}% per trade, {settings.demo_max_daily_loss_pct}% daily, "
        f"{settings.demo_max_drawdown_pct}% drawdown, {settings.demo_max_open_positions} positions")
    n = state.unresolved_reconciliation(session)
    add("no unresolved reconciliation issues", n == 0, f"{n} open")
    add("emergency stop not active", not state.emergency_active(session))
    return req


def enable_demo(session: Session, settings: Settings, gw: MT5Gateway, *, report_id: int, confirmation: str,
                actor: str) -> dict:
    report = session.get(ValidationReport, report_id)
    req = gate_requirements(session, settings, gw, report)
    req.append({"requirement": "explicit confirmation", "passed": confirmation == CONFIRMATION,
                "detail": f'type exactly: "{CONFIRMATION}"'})
    if not all(r["passed"] for r in req):
        session.add(AuditLog(actor=actor, action="demo.enable_refused",
                             details={"report_id": report_id, "failed": [r for r in req if not r["passed"]]}))
        return {"enabled": False, "requirements": req}
    version = session.get(StrategyVersion, report.strategy_version_id)
    v = gw.verify_demo_account()
    st = {"enabled": True, "report_id": report.id, "strategy_version_id": version.id, "strategy": version.name,
          "params": version.params, "params_hash": version.params_hash, "symbol": report.symbol,
          "timeframe": report.timeframe, "htf": report.htf, "login": v.login, "server": v.server,
          "risk_config": (report.config or {}).get("backtest", {}), "enabled_at": utcnow().isoformat(),
          "enabled_by": actor, "last_processed_bar": None,
          "expectation": {k: (report.report or {}).get("segments", {}).get("out_of_sample", {}).get(k)
                          for k in ("closed_trades", "win_rate", "expectancy_r", "profit_factor",
                                    "first_bar_close", "last_bar_close")}}
    state.put(session, state.DEMO_STATE, st)
    session.add(AuditLog(actor=actor, action="demo.enabled", details={k: st[k] for k in (
        "report_id", "strategy", "params_hash", "symbol", "timeframe", "login", "server")}))
    return {"enabled": True, "requirements": req, "state": st}


def disable_demo(session: Session, actor: str, reason: str = "disabled by user") -> dict:
    st = state.demo_state(session)
    st["enabled"] = False
    st["disabled_at"] = utcnow().isoformat()
    st["disabled_reason"] = reason
    state.put(session, state.DEMO_STATE, st)
    session.add(AuditLog(actor=actor, action="demo.disabled", details={"reason": reason}))
    return {"enabled": False}
