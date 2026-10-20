from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from sqlalchemy import func, select

from app.data.ingestion import ingest_bars, load_bars
from app.data.quality import clean_and_check
from app.models import Bar, IngestionRun

from .fake_mt5 import make_rates

UTC = timezone.utc
MON = datetime(2025, 3, 3, tzinfo=UTC)  # a Monday


def df_from(rates: pd.DataFrame) -> pd.DataFrame:
    d = rates.copy()
    d["time"] = pd.to_datetime(d["time"], unit="s", utc=True)
    return d


def test_clean_series_is_ok():
    df, rep = clean_and_check(df_from(make_rates(MON, 48, 60)), 60, MON, MON + timedelta(days=2))
    assert rep.status == "ok" and rep.usable_bars == 48 and rep.gaps_unexpected == 0


def test_duplicates_invalid_and_misaligned_rows_dropped_and_counted():
    raw = df_from(make_rates(MON, 10, 60))
    raw = pd.concat([raw, raw.iloc[[3]]])                         # duplicate
    raw.loc[raw.index[5], "high"] = raw.loc[raw.index[5], "low"] - 1  # high < low
    extra = raw.iloc[[0]].copy()
    extra["time"] = MON + timedelta(minutes=17)                   # not on an H1 boundary
    raw = pd.concat([raw, extra])
    df, rep = clean_and_check(raw, 60, MON, MON + timedelta(days=1))
    assert rep.duplicates_removed == 1
    assert rep.invalid_rows == 1
    assert rep.misaligned_rows == 1
    assert rep.usable_bars == 9 and rep.status == "warning"
    assert df["time"].is_monotonic_increasing and not df["time"].duplicated().any()


def test_gap_classification():
    # Friday 2025-03-07 20:00 -> Sunday 2025-03-09 22:00 is a weekend
    fri = datetime(2025, 3, 7, 18, tzinfo=UTC)
    weekend = pd.concat([df_from(make_rates(fri, 3, 60)),
                         df_from(make_rates(datetime(2025, 3, 9, 22, tzinfo=UTC), 3, 60))])
    _, rep = clean_and_check(weekend, 60, fri, fri + timedelta(days=3))
    assert rep.gaps_weekend == 1 and rep.gaps_unexpected == 0 and rep.status == "ok"

    # one-hour daily break, and a 10-hour hole midweek
    _, rep = clean_and_check(df_from(make_rates(MON, 48, 60, skip={5} | set(range(20, 30)))),
                             60, MON, MON + timedelta(days=2))
    assert rep.gaps_session_break == 1
    assert rep.gaps_unexpected == 1 and rep.missing_bars_unexpected == 10
    assert rep.status == "warning"


def test_empty_response_fails_and_never_fabricates():
    df, rep = clean_and_check(pd.DataFrame(), 60, MON, MON + timedelta(days=1))
    assert df.empty and rep.status == "failed" and rep.usable_bars == 0


def test_history_starting_late_is_reported():
    _, rep = clean_and_check(df_from(make_rates(MON + timedelta(days=10), 24, 60)), 60, MON,
                             MON + timedelta(days=11))
    assert rep.history_starts_late_by_days == 10
    assert any("history" in n for n in rep.notes)


def test_stale_data_detected():
    now = datetime(2025, 3, 20, 12, tzinfo=UTC)
    _, rep = clean_and_check(df_from(make_rates(MON, 24, 60)), 60, MON, now, now=now)
    assert rep.stale


# ---------------------------------------------------------------- ingestion + storage

def test_ingestion_stores_bars_and_is_idempotent(session, gateway, fake):
    fake.set_bars("EURUSDm", fake.TIMEFRAME_M15, make_rates(MON, 4 * 24 * 3, 15))
    end = MON + timedelta(days=3)
    run1 = ingest_bars(session, gateway, "EURUSDm", "M15", MON, end)
    assert run1.status == "ok" and run1.bars_inserted == 288 and run1.bars_already_present == 0
    assert run1.source["provider"] == "MetaTrader5" and run1.source["server"] == "Exness-MT5Trial7"

    run2 = ingest_bars(session, gateway, "EURUSDm", "M15", MON, end)
    assert run2.bars_inserted == 0 and run2.bars_already_present == 288
    assert session.scalar(select(func.count()).select_from(Bar)) == 288

    df = load_bars(session, "EURUSDm", "M15")
    assert len(df) == 288 and str(df["time"].dt.tz) == "UTC" and df["time"].iloc[0] == MON


def test_ingestion_chunks_large_ranges(session, gateway, fake, monkeypatch):
    import app.data.ingestion as ing
    monkeypatch.setattr(ing, "BARS_PER_REQUEST", 100)
    fake.set_bars("EURUSDm", fake.TIMEFRAME_M1, make_rates(MON, 1000, 1))
    run = ingest_bars(session, gateway, "EURUSDm", "M1", MON, MON + timedelta(minutes=999))
    assert run.bars_inserted == 1000
    assert sum(1 for c in fake.calls if c[0] == "copy_rates_range") >= 10


def test_ingestion_failures_are_recorded(session, gateway, fake):
    run = ingest_bars(session, gateway, "NOPE", "H1", MON, MON + timedelta(days=1))
    assert run.status == "failed" and "not found" in run.error

    run = ingest_bars(session, gateway, "EURUSDm", "H1", MON, MON + timedelta(days=1))
    assert run.status == "failed" and run.bars_inserted == 0  # MT5 returned nothing

    fake.fail_rates = True
    run = ingest_bars(session, gateway, "EURUSDm", "H1", MON, MON + timedelta(days=1))
    assert run.status == "failed" and "copy_rates_range" in run.error
    assert session.scalar(select(func.count()).select_from(IngestionRun)) == 3


def test_ingestion_validates_input(session, gateway):
    with pytest.raises(ValueError):
        ingest_bars(session, gateway, "EURUSDm", "H1", datetime(2025, 1, 2), datetime(2025, 1, 3))
    with pytest.raises(ValueError):
        ingest_bars(session, gateway, "EURUSDm", "H1", MON, MON)
    with pytest.raises(ValueError):
        ingest_bars(session, gateway, "EURUSDm", "W1", MON, MON + timedelta(days=1))
