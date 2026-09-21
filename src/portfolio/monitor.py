"""
Position Monitor — tracks open positions and triggers exits.

Responsibilities:
- Check stop-loss and take-profit levels for all open positions
- Time-based exits (end of day)
- Reconcile internal database against broker positions
- Trigger emergency liquidation if configured conditions are met
- Feed MAE/MFE tracking
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from src.broker.base import BrokerAdapter
from src.common.logging import get_logger
from src.common.models import TradeStatus
from src.execution.engine import ExecutionEngine
from src.market_data.service import MarketDataService
from src.persistence.database import get_db
from src.persistence.repositories import AuditRepository, TradeRepository
from src.risk.engine import KillSwitch

log = get_logger(__name__)


class PositionMonitor:
    def __init__(
        self,
        broker: BrokerAdapter,
        market_data: MarketDataService,
        execution: ExecutionEngine,
        kill_switch: KillSwitch,
    ) -> None:
        self._broker = broker
        self._data = market_data
        self._execution = execution
        self._ks = kill_switch

    def monitor_all(self) -> None:
        """Check all open positions for exit conditions."""
        with get_db() as db:
            open_trades = TradeRepository(db).get_open_trades()

        for record in open_trades:
            if record.status not in (TradeStatus.FILLED, TradeStatus.EXIT_PENDING):
                continue
            if record.status == TradeStatus.EXIT_PENDING:
                self._check_pending_exit(record)
                continue

            try:
                quote = self._data.get_quote(record.symbol)
                current_price = quote.last
            except Exception as exc:
                log.warning("monitor.quote_error", symbol=record.symbol, error=str(exc))
                continue

            self._update_mfe_mae(record, current_price)

            exit_reason = self._check_exit_conditions(record, current_price)
            if exit_reason:
                log.info("monitor.exit_triggered", symbol=record.symbol, reason=exit_reason, price=str(current_price))
                self._execution.close_position(record.symbol, record.trade_id, exit_reason)

    def reconcile(self) -> list[str]:
        """
        Compare internal DB positions against broker positions.
        Returns a list of discrepancy descriptions.
        Discrepancies trigger a kill switch or alert.
        """
        discrepancies: list[str] = []

        with get_db() as db:
            open_trades = TradeRepository(db).get_open_trades()

        db_symbols = {t.symbol for t in open_trades if t.status == TradeStatus.FILLED}
        broker_positions = {p.symbol for p in self._broker.get_positions() if p.quantity > 0}

        # In DB but not at broker
        for symbol in db_symbols - broker_positions:
            msg = f"RECONCILIATION: {symbol} exists in DB but not at broker"
            discrepancies.append(msg)
            log.error("reconciliation.db_not_broker", symbol=symbol)

        # At broker but not in DB
        for symbol in broker_positions - db_symbols:
            msg = f"RECONCILIATION: {symbol} exists at broker but not in DB"
            discrepancies.append(msg)
            log.error("reconciliation.broker_not_db", symbol=symbol)
            # Disable new trading until reconciled
            self._ks.disable(f"Unexpected broker position: {symbol}")

        return discrepancies

    def _check_exit_conditions(self, record, current_price: Decimal) -> Optional[str]:
        entry = record.actual_entry or record.entry_proposal
        if entry is None:
            return None

        # Stop loss
        if record.stop_loss and current_price <= record.stop_loss:
            return "stop_loss"

        # Take profit
        if record.take_profit and current_price >= record.take_profit:
            return "take_profit"

        # End-of-day time exit (close 5 minutes before market close)
        now = datetime.utcnow()
        eod_close = now.replace(hour=20, minute=55, second=0, microsecond=0)
        if now >= eod_close:
            return "end_of_day"

        # Strategy invalidation: if price falls more than 2x stop distance below entry
        stop_dist = entry - record.stop_loss if record.stop_loss else Decimal("0")
        if stop_dist > 0 and (entry - current_price) > 2 * stop_dist:
            return "strategy_invalidated"

        return None

    def _update_mfe_mae(self, record, current_price: Decimal) -> None:
        entry = record.actual_entry or record.entry_proposal
        if entry is None:
            return
        gain = current_price - entry
        with get_db() as db:
            db_record = TradeRepository(db).get_by_id(record.trade_id)
            if db_record is None:
                return
            if db_record.mfe is None or gain > db_record.mfe:
                db_record.mfe = gain
            if db_record.mae is None or gain < db_record.mae:
                db_record.mae = gain
            TradeRepository(db).save(db_record)

    def _check_pending_exit(self, record) -> None:
        """Check status of a pending exit order and update record."""
        if not record.broker_order_id:
            return
        result = self._broker.get_order_status(record.broker_order_id)
        if result.is_filled and result.avg_fill_price:
            with get_db() as db:
                db_record = TradeRepository(db).get_by_id(record.trade_id)
                if db_record:
                    db_record.exit_price = result.avg_fill_price
                    db_record.closed_at = datetime.utcnow()
                    db_record.status = TradeStatus.CLOSED
                    TradeRepository(db).save(db_record)
