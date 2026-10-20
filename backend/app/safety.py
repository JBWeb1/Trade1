"""Account-mode verification. Fails closed.

The terminal-reported `account_info().trade_mode` is the authority. A
user-supplied label, config value or frontend toggle never counts as proof
that an account is demo.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import Settings
from .mt5.constants import ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODES


@dataclass
class AccountVerification:
    verified_demo: bool
    account_mode: str  # DEMO | REAL | CONTEST | UNKNOWN
    login: int | None = None
    server: str | None = None
    company: str | None = None
    currency: str | None = None
    reasons: list[str] = field(default_factory=list)   # why verification failed
    warnings: list[str] = field(default_factory=list)  # non-blocking observations

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def verify_demo_account(account: dict | None, terminal: dict | None, settings: Settings,
                        error: str | None = None) -> AccountVerification:
    if error or not account:
        return AccountVerification(False, "UNKNOWN",
                                   reasons=[error or "no account information from the terminal"])

    raw_mode = account.get("trade_mode")
    mode = ACCOUNT_TRADE_MODES.get(raw_mode, "UNKNOWN") if isinstance(raw_mode, int) else "UNKNOWN"
    server = str(account.get("server") or "")
    v = AccountVerification(False, mode, account.get("login"), server or None,
                            account.get("company"), account.get("currency"))

    if not terminal or not terminal.get("connected"):
        v.reasons.append("terminal is not connected to the trade server")
    if not isinstance(raw_mode, int) or raw_mode != ACCOUNT_TRADE_MODE_DEMO:
        v.reasons.append(f"terminal reports account trade_mode={raw_mode!r} ({mode}), not DEMO")
    if "real" in server.lower():
        v.reasons.append(f"server name {server!r} indicates a real-money server")
    if settings.mt5_login and account.get("login") != settings.mt5_login:
        v.reasons.append(f"logged-in account {account.get('login')} does not match configured "
                         f"TRADE1_MT5_LOGIN {settings.mt5_login}")
    if server and not any(w in server.lower() for w in ("trial", "demo")):
        v.warnings.append(f"server name {server!r} does not contain 'Trial' or 'Demo'")

    v.verified_demo = not v.reasons
    return v
