"""Unit tests for MockBrokerAdapter."""
from decimal import Decimal

import pytest

from src.broker.mock import MockBrokerAdapter
from src.common.models import Direction, OrderType


class TestMockBrokerAdapter:
    def setup_method(self):
        self.broker = MockBrokerAdapter(initial_cash=Decimal("10000"))
        self.broker.connect()

    def test_connect_disconnect(self):
        assert self.broker.is_connected()
        self.broker.disconnect()
        assert not self.broker.is_connected()

    def test_initial_account_state(self):
        account = self.broker.get_account()
        assert account.cash == Decimal("10000")
        assert account.net_liquidation == Decimal("10000")
        assert account.gross_position_value == Decimal("0")

    def test_buy_order(self):
        result = self.broker.submit_order(
            symbol="AAPL",
            quantity=10,
            order_type=OrderType.LIMIT,
            direction=Direction.LONG.value,
            limit_price=Decimal("100"),
        )
        assert result.is_filled
        assert result.filled_quantity == 10
        assert result.avg_fill_price == Decimal("100")

    def test_buy_reduces_cash(self):
        self.broker.submit_order(
            symbol="AAPL",
            quantity=10,
            order_type=OrderType.LIMIT,
            direction=Direction.LONG.value,
            limit_price=Decimal("100"),
        )
        account = self.broker.get_account()
        # Cash reduced by 10*100 + commission ($1 minimum)
        assert account.cash < Decimal("10000")
        assert account.cash == Decimal("10000") - Decimal("1000") - Decimal("1.00")

    def test_position_created_after_buy(self):
        self.broker.submit_order(
            symbol="AAPL",
            quantity=5,
            order_type=OrderType.LIMIT,
            direction=Direction.LONG.value,
            limit_price=Decimal("150"),
        )
        positions = self.broker.get_positions()
        assert len(positions) == 1
        assert positions[0].symbol == "AAPL"
        assert positions[0].quantity == 5

    def test_sell_closes_position(self):
        self.broker.submit_order("AAPL", 5, OrderType.LIMIT, Direction.LONG.value, limit_price=Decimal("100"))
        self.broker.submit_order("AAPL", 5, OrderType.LIMIT, "SELL", limit_price=Decimal("105"))
        positions = self.broker.get_positions()
        assert len(positions) == 0

    def test_sell_without_position_rejected(self):
        result = self.broker.submit_order("AAPL", 5, OrderType.LIMIT, "SELL", limit_price=Decimal("100"))
        assert result.status == "REJECTED"

    def test_insufficient_funds_rejected(self):
        result = self.broker.submit_order(
            symbol="AAPL",
            quantity=1000,
            order_type=OrderType.LIMIT,
            direction=Direction.LONG.value,
            limit_price=Decimal("100"),  # 1000 * 100 = $100,000 > $10,000 cash
        )
        assert result.status == "REJECTED"

    def test_cancel_order(self):
        result = self.broker.submit_order("AAPL", 5, OrderType.LIMIT, Direction.LONG.value, limit_price=Decimal("100"))
        cancelled = self.broker.cancel_order(result.broker_order_id)
        assert cancelled

    def test_order_status(self):
        result = self.broker.submit_order("AAPL", 5, OrderType.LIMIT, Direction.LONG.value, limit_price=Decimal("100"))
        status = self.broker.get_order_status(result.broker_order_id)
        assert status.is_filled

    def test_update_price(self):
        self.broker.submit_order("AAPL", 5, OrderType.LIMIT, Direction.LONG.value, limit_price=Decimal("100"))
        self.broker.update_price("AAPL", Decimal("110"))
        positions = self.broker.get_positions()
        assert positions[0].current_price == Decimal("110")

    def test_commission_calculation(self):
        # 100 shares at $1 = $100 fill — well within $10,000 budget
        result = self.broker.submit_order(
            symbol="AAPL",
            quantity=100,
            order_type=OrderType.LIMIT,
            direction=Direction.LONG.value,
            limit_price=Decimal("1"),
        )
        # 100 * $0.005 = $0.50, below $1.00 minimum → commission = $1.00
        assert result.commission == Decimal("1.00")

    def test_commission_above_minimum(self):
        # 300 shares at $1 = $300 fill — well within $10,000 budget
        result = self.broker.submit_order(
            symbol="AAPL",
            quantity=300,
            order_type=OrderType.LIMIT,
            direction=Direction.LONG.value,
            limit_price=Decimal("1"),
        )
        # 300 * $0.005 = $1.50, above $1.00 minimum → commission = $1.50
        assert result.commission == Decimal("1.50")
