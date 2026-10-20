from datetime import datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.mt5.gateway import MT5Error, MT5Gateway, MT5NotAvailable
from app.safety import verify_demo_account

from .fake_mt5 import FakeMT5, account, make_rates

UTC = timezone.utc


def test_initialize_failure_raises_with_code(settings):
    gw = MT5Gateway(settings, module=FakeMT5(init_ok=False))
    with pytest.raises(MT5Error) as e:
        gw.get_account_info()
    assert e.value.code == -10004


def test_missing_package_gives_clear_error(settings):
    gw = MT5Gateway(settings)
    try:
        import MetaTrader5  # noqa: F401
        pytest.skip("real package installed")
    except ImportError:
        pass
    with pytest.raises(MT5NotAvailable):
        gw.get_terminal_info()


def test_check_connection_never_raises(settings):
    assert MT5Gateway(settings, module=FakeMT5(init_ok=False)).check_connection()["connected"] is False
    assert MT5Gateway(settings, module=FakeMT5(connected=False)).check_connection()["connected"] is False


def test_account_info_strips_personal_fields(gateway):
    acct = gateway.get_account_info()
    assert "name" not in acct and acct["trade_mode"] == 0


def test_password_passed_to_initialize_but_not_kept_in_info(fake):
    s = Settings(_env_file=None, mt5_login=111, mt5_password="pw-xyz-123", mt5_server="Exness-MT5Trial7")
    gw = MT5Gateway(s, module=fake)
    gw.initialize_terminal()
    kwargs = fake.calls[0][1]
    assert kwargs["password"] == "pw-xyz-123" and kwargs["login"] == 111
    assert "pw-xyz-123" not in repr(s)


def test_bars_are_converted_to_utc_with_server_offset():
    fake = FakeMT5()
    # server clock is UTC+2: bar stamped 02:00 server time is 00:00 UTC
    start_utc = datetime(2025, 3, 3, 0, 0, tzinfo=UTC)
    fake.set_bars("EURUSDm", fake.TIMEFRAME_H1, make_rates(start_utc + timedelta(hours=2), 5, 60))
    gw = MT5Gateway(Settings(_env_file=None, mt5_server_utc_offset_hours=2), module=fake)
    df = gw.get_historical_bars("EURUSDm", "H1", start_utc, start_utc + timedelta(hours=4))
    assert df["time"].iloc[0] == start_utc
    assert str(df["time"].dt.tz) == "UTC"
    # request was translated to server time
    _, _, _, req_start, _ = fake.calls[-1]
    assert req_start == start_utc + timedelta(hours=2)


def test_naive_datetimes_rejected(gateway):
    with pytest.raises(ValueError):
        gateway.get_historical_bars("EURUSDm", "H1", datetime(2025, 1, 1), datetime(2025, 1, 2))


def test_unknown_symbol_and_timeframe(gateway):
    with pytest.raises(MT5Error):
        gateway.get_symbol_info("NOPE")
    with pytest.raises(ValueError):
        gateway.get_historical_bars("EURUSDm", "D1", datetime(2025, 1, 1, tzinfo=UTC),
                                    datetime(2025, 1, 2, tzinfo=UTC))


def test_calculators(gateway):
    assert gateway.calculate_margin("EURUSDm", "buy", 0.1, 1.1) == pytest.approx(55.0)
    assert gateway.calculate_profit_in_account_currency("EURUSDm", "sell", 1, 1.1, 1.099) == pytest.approx(100)
    with pytest.raises(ValueError):
        gateway.calculate_margin("EURUSDm", "long", 1, 1.1)


def test_gateway_has_no_order_sending_api(gateway):
    for name in dir(gateway):
        assert "order_send" not in name and "order_check" not in name and "market_order" not in name


# ---------------------------------------------------------------- demo verification

def _verify(acct, connected=True, **settings):
    s = Settings(_env_file=None, **settings)
    return verify_demo_account(acct, {"connected": connected}, s)


def test_demo_account_verified(gateway):
    v = gateway.verify_demo_account()
    assert v.verified_demo and v.account_mode == "DEMO" and not v.reasons


@pytest.mark.parametrize("mode", [1, 2, None, "0", 99])
def test_non_demo_trade_modes_rejected(mode):
    v = _verify(account(trade_mode=mode)._asdict())
    assert not v.verified_demo


def test_real_server_name_rejected_even_if_mode_says_demo():
    v = _verify(account(trade_mode=0, server="Exness-MT5Real8")._asdict())
    assert not v.verified_demo and any("real-money" in r for r in v.reasons)


def test_disconnected_or_missing_account_rejected():
    assert not _verify(account()._asdict(), connected=False).verified_demo
    assert not _verify(None).verified_demo
    v = MT5Gateway(Settings(_env_file=None), module=FakeMT5(init_ok=False)).verify_demo_account()
    assert not v.verified_demo and v.account_mode == "UNKNOWN"


def test_account_mismatch_rejected():
    v = _verify(account(login=999)._asdict(), mt5_login=111)
    assert not v.verified_demo and any("does not match" in r for r in v.reasons)


def test_unusual_server_name_is_warning_only():
    v = _verify(account(server="SomeBroker-Server")._asdict())
    assert v.verified_demo and v.warnings
