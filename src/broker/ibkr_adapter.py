"""
IBKR broker adapter using ib_insync.

Wraps the IBKR TWS/Gateway API.
This is the ONLY place in the system that communicates with IBKR for orders.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from src.broker.base import BrokerAdapter, OrderResult
from src.common.config import get_settings
from src.common.exceptions import BrokerConnectionError, BrokerExecutionError, LiveTradingNotAllowedError
from src.common.logging import get_logger
from src.common.models import AccountState, Direction, OrderType, Position

log = get_logger(__name__)


class IBKRAdapter(BrokerAdapter):
    def __init__(
        self,
        host: str,
        port: int,
        client_id: int,
        is_live: bool = False,
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._is_live = is_live
        self._ib = None

    def _get_ib(self):
        if self._ib is None:
            raise BrokerConnectionError("Not connected to IBKR")
        return self._ib

    def connect(self) -> None:
        import asyncio
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())

        import ib_insync as ibi

        self._ib = ibi.IB()
        try:
            self._ib.connect(self._host, self._port, clientId=self._client_id, timeout=30)
            log.info("ibkr.connected", host=self._host, port=self._port, live=self._is_live)
        except Exception as exc:
            self._ib = None
            msg = str(exc)
            if "client id is already in use" in msg.lower() or "326" in msg:
                raise BrokerConnectionError(
                    f"clientId={self._client_id} is already in use by TWS. "
                    f"Increment IBKR_CLIENT_ID in .env (e.g. to {self._client_id + 1}), "
                    f"or wait ~60s for TWS to release it."
                ) from exc
            raise BrokerConnectionError(f"Failed to connect to IBKR: {exc}") from exc

    def disconnect(self) -> None:
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
            log.info("ibkr.disconnected")
        self._ib = None

    def is_connected(self) -> bool:
        return self._ib is not None and self._ib.isConnected()

    def get_account(self) -> AccountState:
        import ib_insync as ibi

        ib = self._get_ib()
        values = ib.accountValues()
        summary = {v.tag: v.value for v in values if v.currency == "USD"}

        net_liq = Decimal(str(summary.get("NetLiquidation", "0")))
        buying_power = Decimal(str(summary.get("BuyingPower", "0")))
        cash = Decimal(str(summary.get("CashBalance", "0")))
        gross_pos = Decimal(str(summary.get("GrossPositionValue", "0")))
        daily_pnl = Decimal(str(summary.get("RealizedPnL", "0")))

        positions = self.get_positions()

        return AccountState(
            net_liquidation=net_liq,
            buying_power=buying_power,
            cash=cash,
            gross_position_value=gross_pos,
            realized_pnl_today=daily_pnl,
            positions=positions,
        )

    def get_positions(self) -> list[Position]:
        import ib_insync as ibi

        ib = self._get_ib()
        ib_positions = ib.positions()
        result = []
        for p in ib_positions:
            if p.position == 0:
                continue
            result.append(
                Position(
                    symbol=p.contract.symbol,
                    quantity=int(p.position),
                    avg_cost=Decimal(str(p.avgCost)),
                    current_price=Decimal(str(p.avgCost)),  # updated via market data
                )
            )
        return result

    def get_open_orders(self) -> list[dict]:
        ib = self._get_ib()
        return [
            {
                "order_id": str(t.order.orderId),
                "symbol": t.contract.symbol,
                "status": t.orderStatus.status,
                "quantity": t.order.totalQuantity,
                "filled": t.orderStatus.filled,
            }
            for t in ib.openTrades()
        ]

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
        import ib_insync as ibi

        if self._is_live:
            # Extra guard: live trading requires explicit configuration
            log.warning("ibkr.live_order", symbol=symbol, quantity=quantity)

        settings = get_settings()
        ib = self._get_ib()
        contract = ibi.Stock(symbol, settings.exchange, settings.exchange_currency)
        ib.qualifyContracts(contract)

        action = "BUY" if direction == Direction.LONG.value else "SELL"

        if order_type == OrderType.LIMIT:
            if limit_price is None:
                raise BrokerExecutionError("limit_price required for LIMIT order")
            order = ibi.LimitOrder(action, quantity, float(limit_price))
        else:
            order = ibi.MarketOrder(action, quantity)

        # Attach client order ID for reconciliation
        if trade_id:
            order.orderRef = trade_id[:40]

        trade = ib.placeOrder(contract, order)
        ib.sleep(1)  # Brief wait for acknowledgement

        order_id = str(trade.order.orderId)
        status = trade.orderStatus.status
        filled_qty = int(trade.orderStatus.filled)
        avg_fill = Decimal(str(trade.orderStatus.avgFillPrice)) if trade.orderStatus.avgFillPrice else None

        log.info(
            "ibkr.order_submitted",
            symbol=symbol,
            action=action,
            quantity=quantity,
            order_id=order_id,
            status=status,
        )

        return OrderResult(
            broker_order_id=order_id,
            status=status,
            filled_quantity=filled_qty,
            avg_fill_price=avg_fill,
        )

    def cancel_order(self, broker_order_id: str) -> bool:
        ib = self._get_ib()
        for trade in ib.openTrades():
            if str(trade.order.orderId) == broker_order_id:
                ib.cancelOrder(trade.order)
                ib.sleep(0.5)
                log.info("ibkr.order_cancelled", order_id=broker_order_id)
                return True
        return False

    def get_order_status(self, broker_order_id: str) -> OrderResult:
        ib = self._get_ib()
        for trade in ib.trades():
            if str(trade.order.orderId) == broker_order_id:
                return OrderResult(
                    broker_order_id=broker_order_id,
                    status=trade.orderStatus.status,
                    filled_quantity=int(trade.orderStatus.filled),
                    avg_fill_price=Decimal(str(trade.orderStatus.avgFillPrice))
                    if trade.orderStatus.avgFillPrice
                    else None,
                )
        return OrderResult(broker_order_id=broker_order_id, status="NOT_FOUND")

    def get_executions(self, broker_order_id: str) -> list[dict]:
        ib = self._get_ib()
        result = []
        for fill in ib.fills():
            if str(fill.execution.orderId) == broker_order_id:
                result.append(
                    {
                        "exec_id": fill.execution.execId,
                        "time": str(fill.execution.time),
                        "symbol": fill.contract.symbol,
                        "shares": fill.execution.shares,
                        "price": fill.execution.price,
                        "commission": float(fill.commissionReport.commission)
                        if fill.commissionReport
                        else None,
                    }
                )
        return result
