from __future__ import annotations

from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuditLog, Instrument, utcnow
from .discovery import discover


def sync_instruments(session: Session, gateway) -> dict:
    """Upsert the terminal's current symbol list. Symbols that disappeared are
    kept but flagged, so history referencing them stays explainable."""
    server = gateway.get_account_info().get("server") or "unknown"
    specs = discover(gateway)
    existing = {i.symbol: i for i in session.scalars(
        select(Instrument).where(Instrument.broker_server == server))}
    seen = set()
    for spec in specs:
        seen.add(spec.symbol)
        data = asdict(spec)
        row = existing.get(spec.symbol)
        if row is None:
            row = Instrument(broker_server=server, symbol=spec.symbol)
            session.add(row)
        row.category = spec.category
        row.subcategory = spec.subcategory
        row.description = spec.description
        row.spec = data
        row.issues = spec.issues
        row.metadata_ok = spec.tradable_metadata_ok
        row.updated_at = utcnow()
    missing = [s for s in existing if s not in seen]
    for s in missing:
        row = existing[s]
        row.metadata_ok = False
        if "no longer offered by the broker" not in row.issues:
            row.issues = [*row.issues, "no longer offered by the broker"]
    summary = {
        "server": server,
        "discovered": len(specs),
        "flagged": sum(1 for s in specs if s.issues),
        "removed": len(missing),
        "by_category": {},
    }
    for s in specs:
        summary["by_category"][s.category] = summary["by_category"].get(s.category, 0) + 1
    session.add(AuditLog(actor="system", action="instruments.sync", details=summary))
    return summary
