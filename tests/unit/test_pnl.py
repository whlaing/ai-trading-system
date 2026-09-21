"""Unit tests for P/L calculations in the mock broker."""
from decimal import Decimal

import pytest

from src.broker.mock import MockBrokerAdapter
from src.common.models import Direction, OrderType


class TestPnLCalculations:
    def setup_method(self):
        self.broker = MockBrokerAdapter(initial_cash=Decimal("10000"))
        self.broker.connect()

    def test_profitable_round_trip(self):
        # Buy 10 shares at $100
        self.broker.submit_order("AAPL", 10, OrderType.LIMIT, Direction.LONG.value, limit_price=Decimal("100"))
        # Sell 10 shares at $110
        result = self.broker.submit_order("AAPL", 10, OrderType.LIMIT, "SELL", limit_price=Decimal("110"))

        account = self.broker.get_account()
        # Starting $10,000
        # Buy: -$1,000 (shares) - $1.00 (commission)
        # Sell: +$1,100 (proceeds) - $1.00 (commission)
        # Net gain: $100 - $2 commission = $98
        expected_cash = Decimal("10000") - Decimal("1000") - Decimal("1.00") + Decimal("1100") - Decimal("1.00")
        assert account.cash == expected_cash
        assert account.cash == Decimal("10098")

    def test_losing_round_trip(self):
        self.broker.submit_order("AAPL", 10, OrderType.LIMIT, Direction.LONG.value, limit_price=Decimal("100"))
        self.broker.submit_order("AAPL", 10, OrderType.LIMIT, "SELL", limit_price=Decimal("95"))

        account = self.broker.get_account()
        # Buy: -$1,000 - $1.00
        # Sell: +$950 - $1.00
        # Net loss: -$50 - $2 commission = -$52
        expected_cash = Decimal("10000") - Decimal("1000") - Decimal("1.00") + Decimal("950") - Decimal("1.00")
        assert account.cash == expected_cash
        assert account.cash == Decimal("9948")

    def test_no_positions_after_full_close(self):
        self.broker.submit_order("AAPL", 5, OrderType.LIMIT, Direction.LONG.value, limit_price=Decimal("100"))
        self.broker.submit_order("AAPL", 5, OrderType.LIMIT, "SELL", limit_price=Decimal("105"))
        assert self.broker.get_positions() == []

    def test_partial_close_reduces_position(self):
        self.broker.submit_order("AAPL", 10, OrderType.LIMIT, Direction.LONG.value, limit_price=Decimal("100"))
        self.broker.submit_order("AAPL", 6, OrderType.LIMIT, "SELL", limit_price=Decimal("105"))
        positions = self.broker.get_positions()
        assert len(positions) == 1
        assert positions[0].quantity == 4

    def test_multiple_symbols(self):
        self.broker.submit_order("AAPL", 5, OrderType.LIMIT, Direction.LONG.value, limit_price=Decimal("100"))
        self.broker.submit_order("MSFT", 3, OrderType.LIMIT, Direction.LONG.value, limit_price=Decimal("200"))
        positions = self.broker.get_positions()
        assert len(positions) == 2
        symbols = {p.symbol for p in positions}
        assert symbols == {"AAPL", "MSFT"}
