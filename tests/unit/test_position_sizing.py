"""Unit tests for position sizing."""
from decimal import Decimal

import pytest

from src.risk.position_sizing import calculate_position_size
from src.common.exceptions import PositionSizingError


def test_basic_calculation():
    shares = calculate_position_size(
        account_value=Decimal("10000"),
        entry_price=Decimal("100"),
        stop_price=Decimal("99"),
        max_risk_usd=Decimal("5"),
        max_position_value_usd=Decimal("500"),
    )
    assert shares == 5  # risk/share=$1, max_risk=$5 → 5 shares


def test_position_value_constraint():
    # Even though risk allows 50 shares, position value is capped
    shares = calculate_position_size(
        account_value=Decimal("10000"),
        entry_price=Decimal("100"),
        stop_price=Decimal("99"),
        max_risk_usd=Decimal("50"),
        max_position_value_usd=Decimal("300"),
    )
    assert shares == 3  # $300 / $100 = 3 shares


def test_risk_constraint_binding():
    shares = calculate_position_size(
        account_value=Decimal("10000"),
        entry_price=Decimal("100"),
        stop_price=Decimal("98"),
        max_risk_usd=Decimal("5"),
        max_position_value_usd=Decimal("500"),
    )
    assert shares == 2  # risk/share=$2, max_risk=$5 → 2 shares


def test_returns_zero_when_entry_exceeds_budget():
    shares = calculate_position_size(
        account_value=Decimal("10000"),
        entry_price=Decimal("600"),
        stop_price=Decimal("599"),
        max_risk_usd=Decimal("5"),
        max_position_value_usd=Decimal("500"),
    )
    assert shares == 0  # $600 > $500 position limit → 0


def test_raises_if_stop_above_entry():
    with pytest.raises(PositionSizingError, match="below entry"):
        calculate_position_size(
            account_value=Decimal("10000"),
            entry_price=Decimal("100"),
            stop_price=Decimal("101"),  # stop ABOVE entry
            max_risk_usd=Decimal("5"),
            max_position_value_usd=Decimal("500"),
        )


def test_raises_if_zero_entry():
    with pytest.raises(PositionSizingError):
        calculate_position_size(
            account_value=Decimal("10000"),
            entry_price=Decimal("0"),
            stop_price=Decimal("0"),
            max_risk_usd=Decimal("5"),
            max_position_value_usd=Decimal("500"),
        )


def test_large_stop_distance_reduces_shares():
    shares_tight = calculate_position_size(
        account_value=Decimal("10000"),
        entry_price=Decimal("100"),
        stop_price=Decimal("99"),  # $1 risk per share
        max_risk_usd=Decimal("10"),
        max_position_value_usd=Decimal("1000"),
    )
    shares_wide = calculate_position_size(
        account_value=Decimal("10000"),
        entry_price=Decimal("100"),
        stop_price=Decimal("95"),  # $5 risk per share
        max_risk_usd=Decimal("10"),
        max_position_value_usd=Decimal("1000"),
    )
    assert shares_tight > shares_wide  # tighter stop → more shares


def test_fractional_truncation():
    # $5 risk / $3 per share = 1.67 → should truncate to 1
    shares = calculate_position_size(
        account_value=Decimal("10000"),
        entry_price=Decimal("100"),
        stop_price=Decimal("97"),
        max_risk_usd=Decimal("5"),
        max_position_value_usd=Decimal("500"),
    )
    assert shares == 1
