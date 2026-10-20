"""Bar-based, event-driven backtest engine for one symbol.

EXECUTION MODEL (bar-based simulation, NOT tick-accurate):
  * Stored bars are BID prices. Ask = bid + spread. Spread per bar is
    max(recorded bar spread, min_spread_points) x spread_multiplier, or a
    fixed value. MT5's per-bar spread is typically the bar's minimum, so the
    recorded value can understate real costs; the floor/multiplier exist for that.
  * Signals are computed at the close of bar i and filled at the OPEN of bar
    i+1: buys at ask + slippage, sells at bid - slippage.
  * Stops and targets are checked against each bar's high/low (bid for longs,
    ask for shorts). If both are inside one bar, the STOP is assumed hit first.
    If a bar OPENS beyond the stop, the fill is at that open (gap) less slippage.
    Take-profits fill at the target price (limit order, no slippage).
  * Position size: risk_pct of current equity at the stop, including round-trip
    commission, rounded DOWN to the volume step. If the broker minimum volume
    would exceed the risk budget, the trade is rejected (never up-sized).
  * Money conversion uses the instrument's tick_value captured at discovery
    time. For symbols quoted in another currency than the account, that is the
    conversion rate of that day, applied to all history (approximation; flagged).
  * Margin: notional_in_account_ccy / leverage must not exceed free equity.
  * Swap: charged per UTC day boundary crossed while open, only when the
    symbol's swap_mode is POINTS (triple on the configured weekday);
    otherwise swap is reported as not modelled.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime

import pandas as pd
from pydantic import BaseModel, Field

from .strategies.base import SignalIntent, Strategy


class CostModel(BaseModel):
    model_config = {"extra": "forbid"}
    spread_mode: str = Field("recorded", pattern="^(recorded|fixed)$")
    fixed_spread_points: float = Field(0, ge=0)
    min_spread_points: float = Field(0, ge=0)
    spread_multiplier: float = Field(1.0, ge=1.0, le=10)
    commission_per_lot_per_side: float = Field(0.0, ge=0)  # account currency
    slippage_points: float = Field(0, ge=0)


class RiskModel(BaseModel):
    model_config = {"extra": "forbid"}
    risk_per_trade_pct: float = Field(0.25, gt=0, le=5)
    min_reward_risk: float = Field(1.5, ge=0)
    max_daily_loss_pct: float = Field(1.0, gt=0, le=100)
    max_drawdown_pct: float = Field(20.0, gt=0, le=100)   # equity lockout for the rest of the test
    max_consecutive_losses: int = Field(4, ge=1)
    cooldown_bars_after_losses: int = Field(24, ge=0)
    max_holding_bars: int | None = Field(None, ge=1)


class BacktestConfig(BaseModel):
    model_config = {"extra": "forbid"}
    starting_balance: float = Field(10_000, gt=0)
    account_currency: str = "USD"
    leverage: float = Field(100, gt=0, le=5000)
    costs: CostModel = CostModel()
    risk: RiskModel = RiskModel()
    swap_triple_weekday: int = Field(2, ge=0, le=6)  # Wednesday (0=Monday)


@dataclass
class Trade:
    side: str
    entry_time: datetime
    entry_price: float
    volume: float
    stop_loss: float
    take_profit: float
    rationale: str
    signal_time: datetime
    exit_time: datetime | None = None
    exit_price: float | None = None
    exit_reason: str = ""
    gross_pnl: float = 0.0
    commission: float = 0.0
    swap: float = 0.0
    spread_cost: float = 0.0
    slippage_cost: float = 0.0
    net_pnl: float = 0.0
    bars_held: int = 0
    ambiguous_bar: bool = False
    planned_risk_money: float = 0.0
    entry_index: int = -1

    def as_dict(self) -> dict:
        d = asdict(self)
        for k in ("entry_time", "exit_time", "signal_time"):
            d[k] = d[k].isoformat() if d[k] else None
        return d


@dataclass
class Rejection:
    time: datetime
    reason: str
    side: str | None = None


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity: pd.DataFrame            # time, equity, balance (bar closes)
    rejections: list[Rejection]
    bars: int
    notes: list[str] = field(default_factory=list)


class SpecError(ValueError):
    pass


def _spec_values(spec: dict) -> dict:
    need = ("point", "tick_size", "tick_value", "volume_min", "volume_max", "volume_step")
    missing = [k for k in need if not spec.get(k) or spec[k] <= 0]
    if missing:
        raise SpecError(f"instrument spec incomplete: {missing}")
    return spec


class BacktestEngine:
    def __init__(self, strategy: Strategy, spec: dict, config: BacktestConfig, primary_minutes: int):
        self.s = strategy
        self.spec = _spec_values(spec)
        self.cfg = config
        self.tf = primary_minutes

    # ------------------------------------------------------------- helpers
    def _spread(self, row) -> float:
        c = self.cfg.costs
        pts = c.fixed_spread_points if c.spread_mode == "fixed" else max(float(row.spread_points), c.min_spread_points)
        return pts * c.spread_multiplier * self.spec["point"]

    def _money(self, price_diff: float, volume: float) -> float:
        return price_diff / self.spec["tick_size"] * self.spec["tick_value"] * volume

    def _size(self, entry: float, stop: float, equity: float) -> float:
        sp = self.spec
        risk_money = equity * self.cfg.risk.risk_per_trade_pct / 100
        per_lot = self._money(abs(entry - stop), 1.0) + 2 * self.cfg.costs.commission_per_lot_per_side
        if per_lot <= 0:
            return 0.0
        steps = math.floor(risk_money / per_lot / sp["volume_step"] + 1e-9)
        vol = round(steps * sp["volume_step"], 8)
        if vol < sp["volume_min"]:
            return 0.0
        return min(vol, sp["volume_max"])

    def _margin(self, price: float, volume: float) -> float:
        return self._money(price, volume) / self.cfg.leverage

    # ------------------------------------------------------------- run
    def run(self, df: pd.DataFrame) -> BacktestResult:
        cfg, sp = self.cfg, self.spec
        point = sp["point"]
        slip = cfg.costs.slippage_points * point
        comm = cfg.costs.commission_per_lot_per_side
        stops_level = (sp.get("stops_level") or 0) * point
        swap_points = sp.get("swap_mode") == "POINTS"
        notes = ["bar-based simulation (OHLC), not tick-accurate; stop assumed first when a bar "
                 "touches both stop and target"]
        if sp.get("needs_conversion"):
            notes.append("profit currency differs from account currency: converted with the "
                         "discovery-time tick_value (approximation)")
        if not swap_points:
            notes.append(f"swap not modelled (swap_mode={sp.get('swap_mode')})")
        if cfg.costs.commission_per_lot_per_side == 0:
            notes.append("commission set to 0 - correct for Exness Standard accounts, not for Raw/Zero")

        rows = list(df.itertuples(index=False))
        n = len(rows)
        balance = cfg.starting_balance
        trades: list[Trade] = []
        rejections: list[Rejection] = []
        equity_rows = []
        pos: Trade | None = None
        pending: SignalIntent | None = None
        pending_time = None
        pending_exit: str | None = None
        day = None
        day_start_equity = balance
        peak_equity = balance
        dd_locked = False
        consecutive_losses = 0
        cooldown_until = -1
        warm = self.s.warmup_bars()

        def close(t: Trade, price: float, when, reason: str, extra_cost: float = 0.0):
            nonlocal balance, consecutive_losses, cooldown_until
            direction = 1 if t.side == "buy" else -1
            t.exit_price, t.exit_time, t.exit_reason = price, when, reason
            t.bars_held = i - t.entry_index + 1
            t.gross_pnl = self._money((price - t.entry_price) * direction, t.volume)
            t.commission += comm * t.volume
            t.slippage_cost += extra_cost
            t.net_pnl = t.gross_pnl - t.commission - t.swap
            balance += t.net_pnl
            trades.append(t)
            if t.net_pnl < 0:
                consecutive_losses += 1
                if consecutive_losses >= cfg.risk.max_consecutive_losses:
                    cooldown_until = i + cfg.risk.cooldown_bars_after_losses
                    consecutive_losses = 0
            else:
                consecutive_losses = 0

        for i, row in enumerate(rows):
            spread = self._spread(row)
            bar_day = row.time.date()

            # --- swap at each UTC day boundary while a position is open
            if pos is not None and day is not None and bar_day != day and swap_points:
                nights = (bar_day - day).days
                for k in range(nights):
                    wd = (pd.Timestamp(day) + pd.Timedelta(days=k)).weekday()
                    mult = 3 if wd == cfg.swap_triple_weekday else 1
                    pts = sp.get("swap_long" if pos.side == "buy" else "swap_short") or 0.0
                    pos.swap -= self._money(pts * point * mult, pos.volume)  # swap>0 is income

            if bar_day != day:
                day = bar_day
                day_start_equity = equity_rows[-1][1] if equity_rows else balance

            # --- 1. discretionary exit decided at the previous close
            if pos is not None and pending_exit:
                px = row.open - slip if pos.side == "buy" else row.open + spread + slip
                close(pos, px, row.time, pending_exit, self._money(slip, pos.volume))
                pos, pending_exit = None, None

            # --- 2. fill a pending entry at this bar's open
            if pending is not None:
                sig, pending = pending, None
                buy = sig.side == "buy"
                entry = row.open + spread + slip if buy else row.open - slip
                if sig.target_r and (entry - sig.stop_loss) * (1 if buy else -1) > 0:
                    r = abs(entry - sig.stop_loss) * sig.target_r
                    sig.take_profit = entry + r if buy else entry - r
                valid = (sig.stop_loss < entry < sig.take_profit) if buy else (sig.take_profit < entry < sig.stop_loss)
                rr = abs(sig.take_profit - entry) / abs(entry - sig.stop_loss) if valid else 0
                equity_now = balance
                if not valid:
                    rejections.append(Rejection(row.time, "gap at open invalidated the signal", sig.side))
                elif rr + 1e-9 < cfg.risk.min_reward_risk:
                    rejections.append(Rejection(row.time, "reward:risk at fill below minimum", sig.side))
                elif abs(entry - sig.stop_loss) < stops_level or abs(sig.take_profit - entry) < stops_level:
                    rejections.append(Rejection(row.time, "stop/target closer than broker stops level", sig.side))
                else:
                    vol = self._size(entry, sig.stop_loss, equity_now)
                    if vol <= 0:
                        rejections.append(Rejection(row.time, "minimum volume exceeds risk budget", sig.side))
                    elif self._margin(entry, vol) > equity_now:
                        rejections.append(Rejection(row.time, "insufficient margin", sig.side))
                    else:
                        pos = Trade(sig.side, row.time, entry, vol, sig.stop_loss, sig.take_profit,
                                    sig.rationale, pending_time,
                                    commission=comm * vol,
                                    spread_cost=self._money(spread, vol) if buy else 0.0,
                                    slippage_cost=self._money(slip, vol),
                                    planned_risk_money=self._money(abs(entry - sig.stop_loss), vol))
                        pos.entry_index = i

            # --- 3. stops / targets inside this bar
            if pos is not None:
                opened_now = pos.entry_index == i
                if pos.side == "buy":
                    if not opened_now and row.open <= pos.stop_loss:
                        close(pos, row.open - slip, row.time, "stop loss (gap)", self._money(slip, pos.volume))
                        pos = None
                    else:
                        hit_sl, hit_tp = row.low <= pos.stop_loss, row.high >= pos.take_profit
                        if hit_sl:
                            pos.ambiguous_bar = hit_tp
                            close(pos, pos.stop_loss - slip, row.time, "stop loss", self._money(slip, pos.volume))
                            pos = None
                        elif hit_tp:
                            close(pos, pos.take_profit, row.time, "take profit")
                            pos = None
                else:
                    ask_open, ask_high, ask_low = row.open + spread, row.high + spread, row.low + spread
                    if not opened_now and ask_open >= pos.stop_loss:
                        p = ask_open + slip
                        pos.spread_cost += self._money(spread, pos.volume)
                        close(pos, p, row.time, "stop loss (gap)", self._money(slip, pos.volume))
                        pos = None
                    else:
                        hit_sl, hit_tp = ask_high >= pos.stop_loss, ask_low <= pos.take_profit
                        if hit_sl or hit_tp:
                            pos.spread_cost += self._money(spread, pos.volume)
                        if hit_sl:
                            pos.ambiguous_bar = hit_tp
                            close(pos, pos.stop_loss + slip, row.time, "stop loss", self._money(slip, pos.volume))
                            pos = None
                        elif hit_tp:
                            close(pos, pos.take_profit, row.time, "take profit")
                            pos = None

            # --- 4. time stop at this bar's close
            if pos is not None:
                pos.bars_held = i - pos.entry_index + 1
                mh = cfg.risk.max_holding_bars
                if mh and pos.bars_held >= mh:
                    if pos.side == "buy":
                        close(pos, row.close - slip, row.time + pd.Timedelta(minutes=self.tf),
                              "max holding time", self._money(slip, pos.volume))
                    else:
                        pos.spread_cost += self._money(spread, pos.volume)
                        close(pos, row.close + spread + slip, row.time + pd.Timedelta(minutes=self.tf),
                              "max holding time", self._money(slip, pos.volume))
                    pos = None

            # --- 5. mark to market at the close
            unreal = 0.0
            if pos is not None:
                mark = row.close if pos.side == "buy" else row.close + spread
                unreal = self._money((mark - pos.entry_price) * (1 if pos.side == "buy" else -1), pos.volume) \
                    - pos.commission - pos.swap
            equity = balance + unreal
            close_time = row.time + pd.Timedelta(minutes=self.tf)
            equity_rows.append((close_time, equity, balance))
            peak_equity = max(peak_equity, equity)
            if not dd_locked and (peak_equity - equity) / peak_equity * 100 >= cfg.risk.max_drawdown_pct:
                dd_locked = True
                rejections.append(Rejection(close_time, "equity drawdown lockout engaged"))

            # --- 6. decisions at the close of bar i (acted on at bar i+1)
            if i >= n - 1 or i < warm:
                continue
            if pos is not None:
                reason = self.s.calculate_exit(i, pos.side)
                if reason:
                    pending_exit = reason
                continue
            sig = self.s.generate_signal(i)
            if sig is None:
                continue
            if dd_locked:
                rejections.append(Rejection(close_time, "equity drawdown lockout", sig.side))
            elif day_start_equity > 0 and (day_start_equity - equity) / day_start_equity * 100 >= cfg.risk.max_daily_loss_pct:
                rejections.append(Rejection(close_time, "daily loss limit reached", sig.side))
            elif i < cooldown_until:
                rejections.append(Rejection(close_time, "cooldown after consecutive losses", sig.side))
            else:
                pending, pending_time = sig, close_time

        if pos is not None:
            last = rows[-1]
            spread = self._spread(last)
            px = last.close if pos.side == "buy" else last.close + spread
            if pos.side == "sell":
                pos.spread_cost += self._money(spread, pos.volume)
            close(pos, px, last.time + pd.Timedelta(minutes=self.tf), "end of data")

        eq = pd.DataFrame(equity_rows, columns=["time", "equity", "balance"])
        return BacktestResult(trades, eq, rejections, n, notes)
