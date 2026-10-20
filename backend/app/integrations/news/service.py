from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ...config import Settings
from ...models import AppSetting, utcnow
from . import forexfactory
from .store import save_events

LAST_FETCH_KEY = "news.last_fetch"


class NewsDisabled(RuntimeError):
    pass


class RefreshTooSoon(RuntimeError):
    pass


def refresh_from_feed(session: Session, settings: Settings, fetcher=forexfactory.fetch) -> dict:
    """Fetch the configured export feed, respecting a minimum refresh interval."""
    if not settings.news_feed_url:
        raise NewsDisabled("news integration disabled: set TRADE1_NEWS_FEED_URL")
    row = session.get(AppSetting, LAST_FETCH_KEY)
    now = utcnow()
    if row is not None:
        last = datetime.fromisoformat(row.value["at"])
        wait = last + timedelta(minutes=settings.news_min_refresh_minutes) - now
        if wait > timedelta(0):
            raise RefreshTooSoon(f"last fetch {last.isoformat()}; try again in {int(wait.total_seconds() // 60) + 1} min")
    events, report = fetcher(settings.news_feed_url)
    summary = save_events(session, events, report, source=settings.news_feed_url)
    if row is None:
        row = AppSetting(key=LAST_FETCH_KEY, value={})
        session.add(row)
    row.value = {"at": now.astimezone(timezone.utc).isoformat()}
    return summary
