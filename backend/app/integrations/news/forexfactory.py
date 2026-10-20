"""Forex Factory economic calendar adapter (optional).

Source: the calendar *export* Forex Factory offers for the current week
(JSON / CSV), configured via TRADE1_NEWS_FEED_URL, or a file you saved
yourself. The HTML calendar page is never scraped.

The expected record shape is validated strictly, field by field. Anything that
does not match is rejected with a reason rather than guessed at. Run
`python -m app.cli news-check` on your machine to confirm the live feed
matches before relying on it.

Limitations:
  * The export covers the current week only, so news history accumulates from
    the day you start collecting. Backtests can apply the news filter only to
    periods covered by stored events (see store.coverage()).
  * Only the schedule (time, currency, impact, title, forecast, previous) is
    stored. Released "actual" values are not used, to avoid look-ahead bias.
  * Check Forex Factory's terms of use for your use case; keep request
    frequency low (the adapter enforces a minimum refresh interval).
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

PROVIDER = "forexfactory"
REQUIRED_FIELDS = ("title", "country", "date", "impact")
IMPACTS = {"high": "high", "medium": "medium", "low": "low", "holiday": "holiday",
           "non-economic": "low"}


class FeedSchemaError(ValueError):
    pass


@dataclass(frozen=True)
class EconomicEvent:
    provider: str
    event_time: datetime  # UTC
    currency: str         # ISO code, or "ALL"
    title: str
    impact: str           # high | medium | low | holiday
    forecast: str | None = None
    previous: str | None = None


@dataclass
class ParseReport:
    received: int = 0
    accepted: int = 0
    rejected: int = 0
    reasons: list[str] = field(default_factory=list)


def _parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp has no timezone offset; refusing to guess")
    return dt.astimezone(timezone.utc)


def parse_records(records: list[dict]) -> tuple[list[EconomicEvent], ParseReport]:
    rep = ParseReport(received=len(records))
    if records and not isinstance(records[0], dict):
        raise FeedSchemaError("feed is not a list of objects")
    if records:
        missing = [f for f in REQUIRED_FIELDS if f not in records[0]]
        if missing:
            raise FeedSchemaError(f"feed records lack required fields {missing}; "
                                  f"got {sorted(records[0])}. The provider format may have changed.")
    events = []
    for i, r in enumerate(records):
        try:
            ccy = str(r["country"]).strip().upper()
            if not (len(ccy) == 3 and ccy.isalpha()):
                raise ValueError(f"country/currency {ccy!r} is not a 3-letter code")
            impact = IMPACTS.get(str(r["impact"]).strip().lower())
            if impact is None:
                raise ValueError(f"unknown impact {r['impact']!r}")
            title = str(r["title"]).strip()
            if not title:
                raise ValueError("empty title")
            events.append(EconomicEvent(PROVIDER, _parse_time(r["date"]), ccy, title[:200], impact,
                                        (str(r.get("forecast") or "").strip() or None),
                                        (str(r.get("previous") or "").strip() or None)))
        except (KeyError, ValueError) as exc:
            rep.rejected += 1
            if len(rep.reasons) < 20:
                rep.reasons.append(f"record {i}: {exc}")
    rep.accepted = len(events)
    return events, rep


def parse_payload(text: str, content_type: str = "") -> tuple[list[EconomicEvent], ParseReport]:
    """Accept the JSON export, or a CSV with the same column names."""
    stripped = text.lstrip()
    if "json" in content_type or stripped[:1] in ("[", "{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise FeedSchemaError(f"invalid JSON: {exc}") from exc
        if not isinstance(data, list):
            raise FeedSchemaError("expected a JSON list of events")
        return parse_records(data)
    if stripped.startswith("<"):
        raise FeedSchemaError("received HTML/XML, not the JSON or CSV export; check the URL")
    rows = list(csv.DictReader(io.StringIO(text)))
    rows = [{k.strip().lower(): v for k, v in row.items() if k} for row in rows]
    return parse_records(rows)


def fetch(url: str, timeout: float = 20.0) -> tuple[list[EconomicEvent], ParseReport]:
    import httpx
    resp = httpx.get(url, timeout=timeout, follow_redirects=True,
                     headers={"User-Agent": "Trade1-Trading-AI/0.2 (calendar export reader)"})
    if resp.status_code != 200:
        raise FeedSchemaError(f"GET {url} returned HTTP {resp.status_code}")
    return parse_payload(resp.text, resp.headers.get("content-type", ""))
