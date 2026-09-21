"""
Risk-based position sizing.

Size is always derived from the maximum acceptable loss per trade,
NOT from a fixed dollar allocation. This prevents large positions
on high-volatility instruments.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

from src.common.exceptions import PositionSizingError
from src.common.logging import get_logger

log = get_logger(__name__)


def calculate_position_size(
    account_value: Decimal,
    entry_price: Decimal,
    stop_price: Decimal,
    max_risk_usd: Decimal,
    max_position_value_usd: Decimal,
) -> int:
    """
    Calculate the maximum number of shares that respects:
      1. The maximum dollar risk per trade (primary constraint)
      2. The maximum position value (secondary constraint)

    Returns 0 if no viable position can be constructed.
    """
    if entry_price <= Decimal("0"):
        raise PositionSizingError("Entry price must be positive")
    if stop_price <= Decimal("0"):
        raise PositionSizingError("Stop price must be positive")
    if stop_price >= entry_price:
        raise PositionSizingError(
            f"Stop price {stop_price} must be below entry price {entry_price}"
        )
    if max_risk_usd <= Decimal("0"):
        raise PositionSizingError("max_risk_usd must be positive")

    risk_per_share = entry_price - stop_price

    if risk_per_share <= Decimal("0"):
        raise PositionSizingError("Risk per share is zero or negative — check stop placement")

    # Primary constraint: risk-based size
    max_shares_by_risk = int((max_risk_usd / risk_per_share).to_integral_value(rounding=ROUND_DOWN))

    # Secondary constraint: maximum position value
    max_shares_by_value = int(
        (max_position_value_usd / entry_price).to_integral_value(rounding=ROUND_DOWN)
    )

    shares = min(max_shares_by_risk, max_shares_by_value)

    log.debug(
        "position_sizing",
        entry=str(entry_price),
        stop=str(stop_price),
        risk_per_share=str(risk_per_share),
        max_risk=str(max_risk_usd),
        max_by_risk=max_shares_by_risk,
        max_by_value=max_shares_by_value,
        final_shares=shares,
    )
    return max(0, shares)
