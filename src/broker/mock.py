"""
MockBrokerAdapter — in-memory broker for unit testing and backtesting.

Provides deterministic, side-effect-free order simulation.
All fills are instantaneous at the requested price.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Optional

from src.broker.base import BrokerAdapter, OrderResult
from src.common.models import AccountState, Direction, OrderType, Position


class MockBrokerAdapter(BrokerAdapter):
    COMMISSION_PER_SHARE = Decimal("0.005")  # $0.005/share (IBKR-like)
    MIN_COMMISSION = Decimal("1.00")

    def __init__(self, initial_cash: Decimal = Decimal("10000")) -> None:
        self._cash = initial_cash
        self._positions: dict[str, Position] = {}
        self._orders: dict[str, dict] = {}
        self._connected = False

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def get_account(self) -> AccountState:
        gross_value = sum(p.market_value for p in self._positions.values())
        return AccountState(
            net_liquidation=self._cash + gross_value,
            buying_power=self._cash,
            cash=self._cash,
            gross_position_value=gross_value,
        )

    def get_positions(self) -> list[Position]:
        return list(self._positions.values())

    def get_open_orders(self) -> list[dict]:
        return [o for o in self._orders.values() if o["status"] == "PENDING"]

    def submit_order(
        self,
        symbol: str,
        quantity: int,
        order_type: OrderType,
        direction: str,
        limit_price: Optional[Decimal] = None,
        stop_price: Optional[Decimal] = None,
        trade_id: Optional[str] = None,
    ) -> OrderResult:
        fill_price = limit_price if limit_price else Decimal("100")
        commission = max(
            self.MIN_COMMISSION,
            self.COMMISSION_PER_SHARE * Decimal(str(quantity)),
        )

        order_id = str(uuid.uuid4())[:8]

        if direction == Direction.LONG.value:
            cost = fill_price * Decimal(str(quantity)) + commission
            if cost > self._cash:
                return OrderResult(
                    broker_order_id=order_id,
                    status="REJECTED",
                    message="Insufficient funds",
                )
            self._cash -= cost
            if symbol in self._positions:
                pos = self._positions[symbol]
                total_qty = pos.quantity + quantity
                avg = (pos.avg_cost * Decimal(str(pos.quantity)) + fill_price * Decimal(str(quantity))) / Decimal(str(total_qty))
                self._positions[symbol] = Position(
                    symbol=symbol,
                    quantity=total_qty,
                    avg_cost=avg,
                    current_price=fill_price,
                    stop_loss=stop_price,
                    trade_id=trade_id,
                )
            else:
                self._positions[symbol] = Position(
                    symbol=symbol,
                    quantity=quantity,
                    avg_cost=fill_price,
                    current_price=fill_price,
                    stop_loss=stop_price,
                    trade_id=trade_id,
                )
        else:
            # SELL / close
            if symbol not in self._positions:
                return OrderResult(broker_order_id=order_id, status="REJECTED", message="No position to close")
            proceeds = fill_price * Decimal(str(quantity)) - commission
            self._cash += proceeds
            pos = self._positions[symbol]
            remaining = pos.quantity - quantity
            if remaining <= 0:
                del self._positions[symbol]
            else:
                self._positions[symbol] = Position(
                    symbol=symbol,
                    quantity=remaining,
                    avg_cost=pos.avg_cost,
                    current_price=fill_price,
                    trade_id=pos.trade_id,
                )

        order = {
            "order_id": order_id,
            "symbol": symbol,
            "quantity": quantity,
            "direction": direction,
            "fill_price": fill_price,
            "commission": commission,
            "status": "FILLED",
            "trade_id": trade_id,
        }
        self._orders[order_id] = order

        return OrderResult(
            broker_order_id=order_id,
            status="Filled",
            filled_quantity=quantity,
            avg_fill_price=fill_price,
            commission=commission,
        )

    def cancel_order(self, broker_order_id: str) -> bool:
        if broker_order_id in self._orders:
            self._orders[broker_order_id]["status"] = "CANCELLED"
            return True
        return False

    def get_order_status(self, broker_order_id: str) -> OrderResult:
        if broker_order_id not in self._orders:
            return OrderResult(broker_order_id=broker_order_id, status="NOT_FOUND")
        order = self._orders[broker_order_id]
        return OrderResult(
            broker_order_id=broker_order_id,
            status=order["status"],
            filled_quantity=order["quantity"] if order["status"] == "FILLED" else 0,
            avg_fill_price=order.get("fill_price"),
            commission=order.get("commission", Decimal("0")),
        )

    def get_executions(self, broker_order_id: str) -> list[dict]:
        if broker_order_id not in self._orders:
            return []
        return [self._orders[broker_order_id]]

    def update_price(self, symbol: str, price: Decimal) -> None:
        """Update current price for a position (used in backtest simulation)."""
        if symbol in self._positions:
            pos = self._positions[symbol]
            self._positions[symbol] = Position(
                symbol=pos.symbol,
                quantity=pos.quantity,
                avg_cost=pos.avg_cost,
                current_price=price,
                stop_loss=pos.stop_loss,
                take_profit=pos.take_profit,
                trade_id=pos.trade_id,
                opened_at=pos.opened_at,
            )
