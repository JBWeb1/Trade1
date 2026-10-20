"""Optional Telegram alerts. Never raises: an alert failure must not stop the system."""
from __future__ import annotations

import logging

from .config import Settings
from .logging_setup import register_secret

log = logging.getLogger(__name__)

ICONS = {"info": "ℹ️", "warning": "⚠️", "critical": "🛑", "fill": "✅"}


def notify(settings: Settings, text: str, level: str = "info", transport=None) -> bool:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return False
    token = settings.telegram_bot_token.get_secret_value()
    register_secret(token)
    try:
        import httpx
        with httpx.Client(timeout=10, transport=transport) as client:
            r = client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                            json={"chat_id": settings.telegram_chat_id,
                                  "text": f"{ICONS.get(level, '')} Trade1 [DEMO]: {text}"[:4000],
                                  "disable_web_page_preview": True})
        if r.status_code != 200:
            log.warning("telegram alert failed: HTTP %s", r.status_code)
            return False
        return True
    except Exception as exc:  # network down, bad token, ...
        log.warning("telegram alert failed: %s", exc)
        return False
