"""Abstract broker adapter interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Optional

from src.common.models import AccountState, OrderType, Position


class OrderResult:
    def __init__(
        self,
        broker_order_id: str,
        status: str,
        filled_quantity: int = 0,
        avg_fill_price: Optional[Decimal] = None,
        commission: Decimal = Decimal("0"),
        message: str = "",
    ) -> None:
        self.broker_order_id = broker_order_id
        self.status = status
        self.filled_quantity = filled_quantity
        self.avg_fill_price = avg_fill_price
        self.commission = commission
        self.message = message

    @property
    def is_filled(self) -> bool:
        return self.status in ("Filled", "FILLED")

    @property
    def is_partially_filled(self) -> bool:
        return self.status in ("PartiallyFilled", "PARTIALLY_FILLED")

    @property
    def is_cancelled(self) -> bool:
        return self.status in ("Cancelled", "CANCELLED")


class BrokerAdapter(ABC):
    """
    All broker implementations must satisfy this interface.
    Strategy and execution code communicates ONLY through this abstraction.
    """

    @abstractmethod
    def connect(self) -> None:
        """Establish broker connection."""

    @abstractmethod
    def disconnect(self) -> None:
        """Close broker connection cleanly."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Return True if connection is alive."""

    @abstractmethod
    def get_account(self) -> AccountState:
        """Return current account value, positions, and buying power."""

    @abstractmethod
    def get_positions(self) -> list[Position]:
        """Return all currently open positions."""

    @abstractmethod
    def get_open_orders(self) -> list[dict]:
        """Return all open/pending orders."""

    @abstractmethod
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
        """Submit an order and return the result."""

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel an open order. Returns True if successfully cancelled."""

    @abstractmethod
    def get_order_status(self, broker_order_id: str) -> OrderResult:
        """Return the current status of an order."""

    @abstractmethod
    def get_executions(self, broker_order_id: str) -> list[dict]:
        """Return execution/fill records for a given order."""
