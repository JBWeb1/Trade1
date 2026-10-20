from sqlalchemy import select

from app.markets.discovery import build_spec, classify, discover
from app.markets.service import sync_instruments
from app.models import AuditLog, Instrument
from app.mt5.gateway import MT5Gateway

from .fake_mt5 import FakeMT5, symbol


def info(**kw):
    return symbol(**kw)._asdict()


def test_classification_uses_broker_metadata_not_names():
    assert classify(info()) == ("forex", "major")
    assert classify(info(name="EURGBPz", path="", base="EUR", profit="GBP")) == ("forex", "minor")
    assert classify(info(name="USDZARm", path="", base="USD", profit="ZAR")) == ("forex", "exotic")
    assert classify(info(name="XAUUSDm", path="", base="XAU", profit="USD", trade_calc_mode=3))[0] == "metals"
    assert classify(info(name="BTCUSDm", path="Crypto\\BTCUSDm", base="BTC", trade_calc_mode=3))[0] == "crypto"
    assert classify(info(name="USOILm", path="Energies\\USOILm", base="USD", trade_calc_mode=3))[0] == "energy"
    assert classify(info(name="US30m", path="Indices\\US30m", trade_calc_mode=4))[0] == "indices"
    assert classify(info(name="AAPLm", path="Stocks\\AAPLm", base="AAPL", trade_calc_mode=3))[0] == "stocks"
    assert classify(info(name="WEIRD", path="", base="", profit="USD", trade_calc_mode=3))[0] == "other"


def test_valid_spec_has_no_issues():
    spec = build_spec(info(), "USD")
    assert spec.issues == [] and spec.tradable_metadata_ok
    assert spec.filling_modes == ["IOC"] and "SL" in spec.order_modes
    assert spec.needs_conversion is False


def test_incomplete_or_contradictory_metadata_is_flagged():
    cases = {
        "tick_value": info(trade_tick_value=0),
        "volume_step": info(volume_step=0),
        "volume_max": info(volume_max=0.001),
        "multiple": info(volume_min=0.015, volume_step=0.01),
        "digits": info(point=0.001, digits=5),
        "trade_mode": info(trade_mode=0),
        "profit currency": info(profit=""),
        "SL/TP": info(order_mode=1),
        "contract_size": info(trade_contract_size=float("nan")),
    }
    for needle, i in cases.items():
        spec = build_spec(i, "USD")
        assert any(needle in issue for issue in spec.issues), (needle, spec.issues)
        assert not spec.tradable_metadata_ok


def test_conversion_flag_for_non_account_currency():
    assert build_spec(info(name="USDJPYm", base="USD", profit="JPY"), "USD").needs_conversion


def test_discover_and_sync_is_idempotent(session, settings):
    fake = FakeMT5(symbols=[symbol(), symbol(name="BAD", trade_tick_value=0),
                            symbol(name="XAUUSDm", path="Metals\\XAUUSDm", base="XAU")])
    gw = MT5Gateway(settings, module=fake)
    assert len(discover(gw)) == 3
    s1 = sync_instruments(session, gw)
    session.flush()
    s2 = sync_instruments(session, gw)
    session.flush()
    rows = session.scalars(select(Instrument)).all()
    assert len(rows) == 3 and s1["discovered"] == s2["discovered"] == 3
    assert s1["flagged"] == 1 and s1["by_category"] == {"forex": 2, "metals": 1}
    assert {r.symbol for r in rows if not r.metadata_ok} == {"BAD"}
    assert session.scalars(select(AuditLog)).all()

    del fake.symbols["XAUUSDm"]
    s3 = sync_instruments(session, gw)
    session.flush()
    gold = session.scalars(select(Instrument).where(Instrument.symbol == "XAUUSDm")).one()
    assert s3["removed"] == 1 and not gold.metadata_ok and "no longer offered" in gold.issues[-1]


def test_hidden_symbols_are_reread_before_being_flagged(settings):
    """MT5 reports blank tick values for symbols outside Market Watch until selected."""
    class HiddenFake(FakeMT5):
        def __init__(self):
            super().__init__(symbols=[symbol(), symbol(name="GBPUSDm", base="GBP", visible=False),
                                      symbol(name="DEADm", visible=False, trade_mode=0)])
            self.selected = set()
            self.select_calls = []

        def symbol_info(self, name):
            s = self.symbols.get(name)
            if s is None or s.visible or name in self.selected:
                return s if s is None or s.visible else s._replace(visible=True)
            return s._replace(trade_tick_value=0.0)  # blank until selected

        def symbols_get(self):
            return tuple(self.symbol_info(n) for n in self.symbols)

        def symbol_select(self, name, enable):
            self.select_calls.append((name, enable))
            (self.selected.add if enable else self.selected.discard)(name)
            return True

    fake = HiddenFake()
    specs = {s.symbol: s for s in discover(MT5Gateway(settings, module=fake))}
    assert specs["GBPUSDm"].issues == [] and specs["GBPUSDm"].tick_value == 1.0
    assert not specs["GBPUSDm"].visible  # still reported as outside Market Watch
    assert any("trade_mode" in i for i in specs["DEADm"].issues)  # really disabled: stays flagged
    assert fake.selected == set()  # Market Watch left as it was
    assert ("GBPUSDm", True) in fake.select_calls and ("GBPUSDm", False) in fake.select_calls
