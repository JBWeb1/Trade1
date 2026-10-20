"""Forex Factory calendar adapter. The sample records follow the export's
expected shape; the live format is verified on the user's machine with
`python -m app.cli news-check`."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api import main as api
from app.api.deps import get_gateway
from app.config import Settings, get_settings
from app.integrations.news.filter import NewsBlackout
from app.integrations.news.forexfactory import FeedSchemaError, parse_payload
from app.integrations.news.service import NewsDisabled, RefreshTooSoon, refresh_from_feed
from app.integrations.news.store import coverage, load_events, save_events
from app.models import EconomicEventRow
from app.mt5.gateway import MT5Gateway

from .fake_mt5 import FakeMT5, symbol

UTC = timezone.utc
SAMPLE = [
    {"title": "Non-Farm Employment Change", "country": "USD", "date": "2025-03-07T08:30:00-05:00",
     "impact": "High", "forecast": "160K", "previous": "143K"},
    {"title": "German Factory Orders m/m", "country": "EUR", "date": "2025-03-06T02:00:00-05:00",
     "impact": "Medium", "forecast": "", "previous": "6.9%"},
    {"title": "Bank Holiday", "country": "JPY", "date": "2025-03-20T00:00:00-04:00",
     "impact": "Holiday", "forecast": "", "previous": ""},
]
NFP = datetime(2025, 3, 7, 13, 30, tzinfo=UTC)


def test_parse_json_converts_to_utc():
    events, rep = parse_payload(json.dumps(SAMPLE))
    assert rep.accepted == 3 and rep.rejected == 0
    assert events[0].event_time == NFP and events[0].impact == "high" and events[0].currency == "USD"
    assert events[1].forecast is None


def test_parse_csv_same_columns():
    csv = "Title,Country,Date,Impact,Forecast,Previous\n" \
          "CPI y/y,GBP,2025-03-19T07:00:00+00:00,High,3.0%,3.0%\n"
    events, rep = parse_payload(csv)
    assert rep.accepted == 1 and events[0].currency == "GBP"


def test_bad_records_rejected_with_reasons_not_guessed():
    bad = SAMPLE[:1] + [
        {"title": "X", "country": "USD", "date": "2025-03-07T08:30:00", "impact": "High"},  # no tz
        {"title": "Y", "country": "United States", "date": "2025-03-07T08:30:00Z", "impact": "High"},
        {"title": "Z", "country": "USD", "date": "2025-03-07T08:30:00Z", "impact": "Huge"},
    ]
    events, rep = parse_payload(json.dumps(bad))
    assert rep.accepted == 1 and rep.rejected == 3
    assert any("timezone" in r for r in rep.reasons)


def test_schema_change_or_html_fails_loudly():
    with pytest.raises(FeedSchemaError):
        parse_payload(json.dumps([{"name": "NFP", "when": "2025-03-07"}]))
    with pytest.raises(FeedSchemaError):
        parse_payload("<html><body>Just a moment...</body></html>")
    with pytest.raises(FeedSchemaError):
        parse_payload('{"not": "a list"}')


def test_blackout_window_and_currency_mapping():
    events, _ = parse_payload(json.dumps(SAMPLE))
    f = NewsBlackout(events, minutes_before=30, minutes_after=15)
    eurusd = {"category": "forex", "currency_base": "EUR", "currency_profit": "USD"}
    eurgbp = {"category": "forex", "currency_base": "EUR", "currency_profit": "GBP"}
    gold = {"category": "metals", "currency_base": "XAU", "currency_profit": "USD"}
    btc = {"category": "crypto", "currency_base": "BTC", "currency_profit": "USD"}

    assert f.check(eurusd, NFP - timedelta(minutes=20)).blocked
    assert f.check(eurusd, NFP + timedelta(minutes=10)).blocked
    assert not f.check(eurusd, NFP + timedelta(minutes=16)).blocked
    assert not f.check(eurusd, NFP - timedelta(minutes=31)).blocked
    assert f.check(gold, NFP).blocked
    assert not f.check(eurgbp, NFP).blocked                     # no USD exposure
    d = f.check(btc, NFP)
    assert not d.blocked and not d.applicable                    # crypto excluded by default
    # medium-impact events ignored unless configured
    eur_orders = datetime(2025, 3, 6, 7, 0, tzinfo=UTC)
    assert not f.check(eurusd, eur_orders).blocked
    assert NewsBlackout(events, impacts=("high", "medium")).check(eurusd, eur_orders).blocked


def test_store_is_idempotent_and_reports_coverage(session):
    events, rep = parse_payload(json.dumps(SAMPLE))
    first = save_events(session, events, rep, "test")
    second = save_events(session, events, rep, "test")
    assert first["new_events"] == 3 and second["new_events"] == 0
    assert session.scalar(select(func.count()).select_from(EconomicEventRow)) == 3
    loaded = load_events(session, NFP - timedelta(days=1), NFP + timedelta(days=1), currency="usd")
    assert [e.title for e in loaded] == ["Non-Farm Employment Change"]
    assert loaded[0].event_time.tzinfo is not None

    cov = coverage(session, datetime(2025, 1, 6, tzinfo=UTC), datetime(2025, 3, 23, tzinfo=UTC))
    assert cov["weeks_with_events"] == 2 and cov["fraction_covered"] < 0.3


def test_refresh_respects_disable_and_min_interval(session):
    off = Settings(_env_file=None)
    with pytest.raises(NewsDisabled):
        refresh_from_feed(session, off)

    on = Settings(_env_file=None, news_feed_url="https://example.invalid/feed.json")
    calls = []

    def fake_fetch(url):
        calls.append(url)
        return parse_payload(json.dumps(SAMPLE))

    assert refresh_from_feed(session, on, fetcher=fake_fetch)["accepted"] == 3
    session.flush()
    with pytest.raises(RefreshTooSoon):
        refresh_from_feed(session, on, fetcher=fake_fetch)
    assert len(calls) == 1


def test_news_api(settings, engine, session):
    events, rep = parse_payload(json.dumps(SAMPLE))
    save_events(session, events, rep, "test")
    session.commit()
    fake = FakeMT5(symbols=[symbol()])
    gw = MT5Gateway(settings, module=fake)
    api.app.dependency_overrides[get_settings] = lambda: settings
    api.app.dependency_overrides[get_gateway] = lambda: gw
    auth = {"Authorization": "Bearer test-token-123"}
    with TestClient(api.app) as c:
        c.post("/api/markets/sync", headers=auth)
        r = c.get("/api/news/events", params={"start": "2025-03-01T00:00:00Z",
                                              "end": "2025-03-31T00:00:00Z", "impact": "high"},
                  headers=auth)
        assert r.status_code == 200 and len(r.json()["items"]) == 1
        b = c.get("/api/news/blackout", params={"symbol": "EURUSDm", "at": "2025-03-07T13:20:00Z"},
                  headers=auth).json()
        assert b["blocked"] and "Non-Farm" in b["reason"]
        assert c.post("/api/news/refresh", headers=auth).status_code == 409  # no URL configured
        assert c.get("/api/news/events", params={"start": "2025-03-01T00:00:00",
                                                 "end": "2025-03-31T00:00:00Z"},
                     headers=auth).status_code == 422
    api.app.dependency_overrides.clear()
