"""Discover and classify the instruments the connected terminal actually offers.

Nothing here assumes any particular symbol exists. Classification uses the
broker's own symbol `path`, the calculation mode and the base/profit
currencies, and falls back to "other" when unsure.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..mt5.constants import (FILLING_FLAGS, ORDER_MODE_FLAGS, SWAP_MODES, SYMBOL_CALC_MODES,
                             SYMBOL_EXECUTION_MODES, SYMBOL_TRADE_MODES, flags)

FIAT = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD", "SEK", "NOK", "DKK", "PLN",
        "HUF", "CZK", "TRY", "ZAR", "MXN", "SGD", "HKD", "CNH", "CNY", "THB", "INR", "IDR",
        "KRW", "MYR", "PHP", "ILS", "RUB", "BRL", "CLP", "COP", "KWD", "SAR", "AED", "QAR",
        "TWD", "VND", "ARS", "NGN", "KES", "EGP", "PKR", "BDT", "LKR", "UAH", "KZT", "MAD",
        "JOD", "BHD", "OMR", "GEL", "AZN", "AMD", "UZS", "RON", "BGN", "ISK", "HRK"}
MAJOR_CCY = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD"}
MAJOR_PAIRS = {("EUR", "USD"), ("GBP", "USD"), ("USD", "JPY"), ("USD", "CHF"),
               ("AUD", "USD"), ("USD", "CAD"), ("NZD", "USD")}
METALS = {"XAU", "XAG", "XPT", "XPD", "XCU"}

CATEGORIES = ("forex", "metals", "energy", "indices", "stocks", "crypto", "commodities", "other")

_PATH_KEYWORDS = (
    ("crypto", "crypto"),
    ("metal", "metals"),
    ("energ", "energy"),
    ("oil", "energy"),
    ("commodit", "commodities"),
    ("indic", "indices"),
    ("index", "indices"),
    ("stock", "stocks"),
    ("share", "stocks"),
    ("equit", "stocks"),
    ("forex", "forex"),
    ("currenc", "forex"),
)


def classify(info: dict) -> tuple[str, str | None]:
    """Return (category, subcategory)."""
    path = str(info.get("path") or "").lower()
    base = str(info.get("currency_base") or "").upper()
    quote = str(info.get("currency_profit") or "").upper()
    calc = info.get("trade_calc_mode")

    category = None
    for kw, cat in _PATH_KEYWORDS:
        if kw in path:
            category = cat
            break
    if category is None:
        if base in METALS:
            category = "metals"
        elif calc in (0, 1) and base in FIAT and quote in FIAT:
            category = "forex"
        elif calc == 4:
            category = "indices"
        elif calc in (32, 38):
            category = "stocks"
        else:
            category = "other"

    sub = None
    if category == "forex" and base in FIAT and quote in FIAT:
        if (base, quote) in MAJOR_PAIRS:
            sub = "major"
        elif base in MAJOR_CCY and quote in MAJOR_CCY:
            sub = "minor"
        else:
            sub = "exotic"
    return category, sub


@dataclass
class InstrumentSpec:
    symbol: str
    category: str
    subcategory: str | None
    description: str | None
    path: str | None
    currency_base: str | None
    currency_profit: str | None
    currency_margin: str | None
    digits: int | None
    point: float | None
    tick_size: float | None
    tick_value: float | None
    tick_value_profit: float | None
    tick_value_loss: float | None
    contract_size: float | None
    volume_min: float | None
    volume_max: float | None
    volume_step: float | None
    trade_mode: str
    execution_mode: str
    calc_mode: str
    filling_modes: list[str]
    order_modes: list[str]
    stops_level: int | None
    freeze_level: int | None
    swap_mode: str
    swap_long: float | None
    swap_short: float | None
    spread_points: int | None
    spread_floating: bool | None
    margin_initial: float | None
    margin_maintenance: float | None
    visible: bool
    account_currency: str | None
    needs_conversion: bool
    # Not provided by the MT5 Python API: commission and session hours.
    commission_known: bool = False
    sessions_known: bool = False
    issues: list[str] = field(default_factory=list)

    @property
    def tradable_metadata_ok(self) -> bool:
        return not self.issues


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def validate(spec: InstrumentSpec) -> list[str]:
    """Problems that must block automated demo orders on this symbol."""
    issues = []
    for name in ("point", "tick_size", "tick_value", "contract_size", "volume_min", "volume_step"):
        v = getattr(spec, name)
        if v is None or v <= 0:
            issues.append(f"{name} missing or non-positive ({v})")
    if spec.volume_max is None or (spec.volume_min and spec.volume_max < spec.volume_min):
        issues.append(f"volume_max {spec.volume_max} invalid vs volume_min {spec.volume_min}")
    if spec.volume_min and spec.volume_step and spec.volume_step > 0:
        ratio = spec.volume_min / spec.volume_step
        if abs(ratio - round(ratio)) > 1e-6:
            issues.append("volume_min is not a multiple of volume_step")
    if spec.digits is not None and spec.point:
        if abs(spec.point - 10 ** -spec.digits) > 10 ** -(spec.digits + 2):
            issues.append(f"point {spec.point} inconsistent with digits {spec.digits}")
    if spec.tick_size and spec.point and spec.tick_size + 1e-12 < spec.point:
        issues.append("tick_size smaller than point")
    if spec.trade_mode in ("DISABLED", "CLOSEONLY"):
        issues.append(f"trading not permitted (trade_mode={spec.trade_mode})")
    if not spec.currency_profit:
        issues.append("profit currency unknown")
    if "SL" not in spec.order_modes or "TP" not in spec.order_modes:
        if spec.order_modes:
            issues.append("broker does not allow SL/TP on this symbol")
    return issues


def build_spec(info: dict, account_currency: str | None) -> InstrumentSpec:
    category, sub = classify(info)
    profit_ccy = info.get("currency_profit") or None
    spec = InstrumentSpec(
        symbol=info["name"],
        category=category,
        subcategory=sub,
        description=info.get("description") or None,
        path=info.get("path") or None,
        currency_base=info.get("currency_base") or None,
        currency_profit=profit_ccy,
        currency_margin=info.get("currency_margin") or None,
        digits=info.get("digits"),
        point=_num(info.get("point")),
        tick_size=_num(info.get("trade_tick_size")),
        tick_value=_num(info.get("trade_tick_value")),
        tick_value_profit=_num(info.get("trade_tick_value_profit")),
        tick_value_loss=_num(info.get("trade_tick_value_loss")),
        contract_size=_num(info.get("trade_contract_size")),
        volume_min=_num(info.get("volume_min")),
        volume_max=_num(info.get("volume_max")),
        volume_step=_num(info.get("volume_step")),
        trade_mode=SYMBOL_TRADE_MODES.get(info.get("trade_mode"), "UNKNOWN"),
        execution_mode=SYMBOL_EXECUTION_MODES.get(info.get("trade_exemode"), "UNKNOWN"),
        calc_mode=SYMBOL_CALC_MODES.get(info.get("trade_calc_mode"), "UNKNOWN"),
        filling_modes=flags(info.get("filling_mode"), FILLING_FLAGS),
        order_modes=flags(info.get("order_mode"), ORDER_MODE_FLAGS),
        stops_level=info.get("trade_stops_level"),
        freeze_level=info.get("trade_freeze_level"),
        swap_mode=SWAP_MODES.get(info.get("swap_mode"), "UNKNOWN"),
        swap_long=_num(info.get("swap_long")),
        swap_short=_num(info.get("swap_short")),
        spread_points=info.get("spread"),
        spread_floating=info.get("spread_float"),
        margin_initial=_num(info.get("margin_initial")),
        margin_maintenance=_num(info.get("margin_maintenance")),
        visible=bool(info.get("visible")),
        account_currency=account_currency,
        needs_conversion=bool(account_currency and profit_ccy and profit_ccy != account_currency),
    )
    spec.issues = validate(spec)
    return spec


REFRESHABLE = ("tick_value", "tick_size", "volume_min", "volume_step", "contract_size", "point")


def discover(gateway) -> list[InstrumentSpec]:
    """Build specs for every symbol. Symbols outside Market Watch often report blank
    values (e.g. tick value 0) until selected, so those are re-read before flagging."""
    account = gateway.get_account_info()
    ccy = account.get("currency")
    specs = []
    for info in gateway.get_available_symbols():
        spec = build_spec(info, ccy)
        if spec.issues and not info.get("visible") and any(
                i.split(" ")[0] in REFRESHABLE for i in spec.issues):
            try:
                spec = build_spec(gateway.peek_symbol_info(info["name"]), ccy)
                spec.visible = False
            except Exception as exc:  # keep the original flags; note why the re-read failed
                spec.issues.append(f"could not re-read specification: {exc}")
        specs.append(spec)
    return specs
