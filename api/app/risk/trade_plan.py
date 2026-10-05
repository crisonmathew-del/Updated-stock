"""Trade plan generator (spec §6.11): entry, stop, size, targets and management rules.

- Entry: the pivot + `entry_offset_amount` ($0.10), or + `entry_offset_pct` (0.1%) when the
  pivot is at or above `entry_offset_pct_from_price` ($100). Rounded up to the cent.
- Stop: the higher (tighter) of the logical stop (just below the base's last swing low: the
  final contraction, handle, flag or gap-day low, less `stop_buffer_pct`) and the
  maximum-loss stop (`max_stop_loss_pct` below the entry). Rounded down to the cent. If the
  logical stop is the wider one, the plan is flagged "risk too wide".
- Size: (account × risk_per_trade_pct) ÷ (entry - stop) shares, capped so the position is at
  most `max_position_pct` of the account.
- Targets: 2R and 3R; partial profits at +profit_take_min..max_pct; stop to breakeven at 2R
  or +breakeven_after_gain_pct, whichever price comes first; trail on a close below the
  21-day EMA (aggressive) or 50-day SMA (standard). Reward/risk uses the first profit level.
"""

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from app.settings.schema import AppSettings


def up_to_cent(price: float) -> float:
    return math.ceil(round(price * 100, 6)) / 100


def down_to_cent(price: float) -> float:
    return math.floor(round(price * 100, 6)) / 100


@dataclass(frozen=True)
class TradePlan:
    entry: float
    stop: float
    stop_basis: str  # logical | max_loss
    logical_stop: float
    max_loss_stop: float
    risk_too_wide: bool
    risk_per_share: float
    risk_pct: float
    shares: int
    capped_by_position_limit: bool
    dollar_risk: float
    position_value: float
    position_pct: float
    buy_zone: tuple[float, float]
    target_2r: float
    target_3r: float
    profit_take: tuple[float, float]
    breakeven_at: float
    breakeven_basis: str  # 2R | gain
    trail_aggressive: float | None  # 21-day EMA
    trail_standard: float | None  # 50-day SMA
    reward_risk: float
    currency: str
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def entry_price(pivot: float, settings: AppSettings) -> float:
    if pivot >= settings.entry_offset_pct_from_price:
        return up_to_cent(pivot * (1 + settings.entry_offset_pct / 100))
    return up_to_cent(pivot + settings.entry_offset_amount)


def build_trade_plan(
    *,
    pivot: float,
    logical_low: float,
    settings: AppSettings,
    ema21: float | None = None,
    sma50: float | None = None,
) -> TradePlan:
    s = settings
    entry = entry_price(pivot, s)
    logical = down_to_cent(logical_low * (1 - s.stop_buffer_pct / 100))
    max_loss = down_to_cent(entry * (1 - s.max_stop_loss_pct / 100))
    if logical >= entry:
        logical = max_loss  # a low above the entry can't anchor a stop
    use_logical = logical >= max_loss
    stop = logical if use_logical else max_loss
    risk = round(entry - stop, 2)
    by_risk = math.floor(s.account_size * s.risk_per_trade_pct / 100 / risk)
    by_position = math.floor(s.account_size * s.max_position_pct / 100 / entry)
    shares = max(0, min(by_risk, by_position))
    first_profit = up_to_cent(entry * (1 + s.profit_take_min_pct / 100))
    two_r = round(entry + 2 * risk, 2)
    by_gain = up_to_cent(entry * (1 + s.breakeven_after_gain_pct / 100))
    by_r = round(entry + s.breakeven_after_r * risk, 2)
    notes = []
    if not use_logical:
        notes.append(
            f"Risk too wide: the logical stop {logical:.2f} is "
            f"{(entry - logical) / entry * 100:.1f}% below the entry (max "
            f"{s.max_stop_loss_pct:g}%). Wait for a tighter setup; the plan uses the "
            f"{s.max_stop_loss_pct:g}% stop."
        )
    if by_position < by_risk:
        notes.append(
            f"Size capped at {s.max_position_pct:g}% of the account ({by_position} shares); "
            f"the risk budget alone would allow {by_risk}."
        )
    return TradePlan(
        entry=entry,
        stop=stop,
        stop_basis="logical" if use_logical else "max_loss",
        logical_stop=logical,
        max_loss_stop=max_loss,
        risk_too_wide=not use_logical,
        risk_per_share=risk,
        risk_pct=round(risk / entry * 100, 2),
        shares=shares,
        capped_by_position_limit=by_position < by_risk,
        dollar_risk=round(shares * risk, 2),
        position_value=round(shares * entry, 2),
        position_pct=round(shares * entry / s.account_size * 100, 2),
        buy_zone=(round(pivot, 2), round(pivot * (1 + s.buy_zone_max_pct_above_pivot / 100), 2)),
        target_2r=two_r,
        target_3r=round(entry + 3 * risk, 2),
        profit_take=(first_profit, up_to_cent(entry * (1 + s.profit_take_max_pct / 100))),
        breakeven_at=min(by_r, by_gain),
        breakeven_basis="2R" if by_r <= by_gain else "gain",
        trail_aggressive=None if ema21 is None else round(ema21, 2),
        trail_standard=None if sma50 is None else round(sma50, 2),
        reward_risk=round((first_profit - entry) / risk, 2),
        currency=s.account_currency,
        notes=notes,
    )
