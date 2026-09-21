"""Data access repositories."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from src.common.logging import get_logger
from src.common.models import AIDecision, TradeRecord, TradeStatus
from src.persistence.models import AuditLogORM, KillSwitchStateORM, OperationalCostORM, TradeORM

log = get_logger(__name__)


class TradeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, record: TradeRecord) -> None:
        existing = self._session.get(TradeORM, record.trade_id)
        if existing:
            self._update_orm(existing, record)
        else:
            orm = self._to_orm(record)
            self._session.add(orm)
        self._session.flush()

    def get_by_id(self, trade_id: str) -> Optional[TradeRecord]:
        orm = self._session.get(TradeORM, trade_id)
        return self._from_orm(orm) if orm else None

    def get_open_trades(self) -> list[TradeRecord]:
        open_statuses = [
            TradeStatus.RISK_APPROVED.value,
            TradeStatus.ORDER_SUBMITTED.value,
            TradeStatus.ORDER_ACKNOWLEDGED.value,
            TradeStatus.PARTIALLY_FILLED.value,
            TradeStatus.FILLED.value,
            TradeStatus.EXIT_PENDING.value,
        ]
        rows = self._session.query(TradeORM).filter(TradeORM.status.in_(open_statuses)).all()
        return [self._from_orm(r) for r in rows]

    def get_trades_today(self) -> list[TradeRecord]:
        start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        rows = (
            self._session.query(TradeORM)
            .filter(TradeORM.created_at >= start)
            .all()
        )
        return [self._from_orm(r) for r in rows]

    def count_trades_today(self) -> int:
        start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        return self._session.query(TradeORM).filter(TradeORM.created_at >= start).count()

    def get_daily_realized_pnl(self) -> Decimal:
        start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        rows = (
            self._session.query(TradeORM.realized_pnl)
            .filter(TradeORM.closed_at >= start, TradeORM.realized_pnl.isnot(None))
            .all()
        )
        return sum((Decimal(str(r[0])) for r in rows), Decimal("0"))

    def get_closed_trades(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        symbol: Optional[str] = None,
        strategy: Optional[str] = None,
    ) -> list[TradeRecord]:
        query = self._session.query(TradeORM).filter(
            TradeORM.status == TradeStatus.CLOSED.value
        )
        if start:
            query = query.filter(TradeORM.closed_at >= start)
        if end:
            query = query.filter(TradeORM.closed_at <= end)
        if symbol:
            query = query.filter(TradeORM.symbol == symbol)
        if strategy:
            query = query.filter(TradeORM.strategy == strategy)
        return [self._from_orm(r) for r in query.all()]

    def has_open_position(self, symbol: str) -> bool:
        filled_statuses = [
            TradeStatus.FILLED.value,
            TradeStatus.EXIT_PENDING.value,
            TradeStatus.PARTIALLY_FILLED.value,
        ]
        return (
            self._session.query(TradeORM)
            .filter(TradeORM.symbol == symbol, TradeORM.status.in_(filled_statuses))
            .count()
            > 0
        )

    def _to_orm(self, record: TradeRecord) -> TradeORM:
        return TradeORM(
            id=record.trade_id,
            symbol=record.symbol,
            strategy=record.strategy,
            direction=record.direction.value,
            status=record.status.value,
            signal_timestamp=record.signal_timestamp,
            ai_decision=record.ai_decision.value if record.ai_decision else None,
            ai_confidence=record.ai_confidence,
            ai_reason=record.ai_reason,
            entry_proposal=float(record.entry_proposal) if record.entry_proposal else None,
            actual_entry=float(record.actual_entry) if record.actual_entry else None,
            stop_loss=float(record.stop_loss) if record.stop_loss else None,
            take_profit=float(record.take_profit) if record.take_profit else None,
            exit_price=float(record.exit_price) if record.exit_price else None,
            quantity=record.quantity,
            commission=float(record.commission),
            estimated_slippage=float(record.estimated_slippage),
            realized_pnl=float(record.realized_pnl) if record.realized_pnl is not None else None,
            mfe=float(record.mfe) if record.mfe is not None else None,
            mae=float(record.mae) if record.mae is not None else None,
            submitted_at=record.submitted_at,
            filled_at=record.filled_at,
            closed_at=record.closed_at,
            broker_order_id=record.broker_order_id,
            rejection_reason=record.rejection_reason,
        )

    def _update_orm(self, orm: TradeORM, record: TradeRecord) -> None:
        orm.status = record.status.value
        orm.ai_decision = record.ai_decision.value if record.ai_decision else None
        orm.ai_confidence = record.ai_confidence
        orm.ai_reason = record.ai_reason
        orm.actual_entry = float(record.actual_entry) if record.actual_entry else None
        orm.exit_price = float(record.exit_price) if record.exit_price else None
        orm.quantity = record.quantity
        orm.commission = float(record.commission)
        orm.estimated_slippage = float(record.estimated_slippage)
        orm.realized_pnl = float(record.realized_pnl) if record.realized_pnl is not None else None
        orm.mfe = float(record.mfe) if record.mfe is not None else None
        orm.mae = float(record.mae) if record.mae is not None else None
        orm.submitted_at = record.submitted_at
        orm.filled_at = record.filled_at
        orm.closed_at = record.closed_at
        orm.broker_order_id = record.broker_order_id
        orm.rejection_reason = record.rejection_reason

    def _from_orm(self, orm: TradeORM) -> TradeRecord:
        from src.common.models import Direction
        return TradeRecord(
            trade_id=orm.id,
            symbol=orm.symbol,
            strategy=orm.strategy,
            direction=Direction(orm.direction),
            status=TradeStatus(orm.status),
            signal_timestamp=orm.signal_timestamp,
            ai_decision=AIDecision(orm.ai_decision) if orm.ai_decision else None,
            ai_confidence=orm.ai_confidence,
            ai_reason=orm.ai_reason,
            entry_proposal=Decimal(str(orm.entry_proposal)) if orm.entry_proposal else None,
            actual_entry=Decimal(str(orm.actual_entry)) if orm.actual_entry else None,
            stop_loss=Decimal(str(orm.stop_loss)) if orm.stop_loss else None,
            take_profit=Decimal(str(orm.take_profit)) if orm.take_profit else None,
            exit_price=Decimal(str(orm.exit_price)) if orm.exit_price else None,
            quantity=orm.quantity,
            commission=Decimal(str(orm.commission)),
            estimated_slippage=Decimal(str(orm.estimated_slippage)),
            realized_pnl=Decimal(str(orm.realized_pnl)) if orm.realized_pnl is not None else None,
            mfe=Decimal(str(orm.mfe)) if orm.mfe is not None else None,
            mae=Decimal(str(orm.mae)) if orm.mae is not None else None,
            submitted_at=orm.submitted_at,
            filled_at=orm.filled_at,
            closed_at=orm.closed_at,
            broker_order_id=orm.broker_order_id,
            rejection_reason=orm.rejection_reason,
        )


class AuditRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def log_event(
        self,
        component: str,
        event: str,
        message: str,
        trade_id: Optional[str] = None,
        symbol: Optional[str] = None,
        strategy: Optional[str] = None,
        severity: str = "INFO",
        details: Optional[dict] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        entry = AuditLogORM(
            trade_id=trade_id,
            symbol=symbol,
            strategy=strategy,
            component=component,
            event=event,
            severity=severity,
            message=message,
            details=json.dumps(details) if details else None,
            correlation_id=correlation_id,
        )
        self._session.add(entry)
        self._session.flush()

    def get_trade_history(self, trade_id: str) -> list[AuditLogORM]:
        return (
            self._session.query(AuditLogORM)
            .filter(AuditLogORM.trade_id == trade_id)
            .order_by(AuditLogORM.created_at.asc())
            .all()
        )


class CostRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record_cost(
        self,
        cost_type: str,
        amount_usd: Decimal,
        description: Optional[str] = None,
        trade_id: Optional[str] = None,
    ) -> None:
        entry = OperationalCostORM(
            date=datetime.utcnow(),
            cost_type=cost_type,
            amount_usd=float(amount_usd),
            description=description,
            trade_id=trade_id,
        )
        self._session.add(entry)
        self._session.flush()

    def get_daily_costs(self, date: Optional[datetime] = None) -> dict[str, Decimal]:
        start = (date or datetime.utcnow()).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        rows = (
            self._session.query(OperationalCostORM.cost_type, OperationalCostORM.amount_usd)
            .filter(OperationalCostORM.date >= start, OperationalCostORM.date < end)
            .all()
        )
        totals: dict[str, Decimal] = {}
        for cost_type, amount in rows:
            totals[cost_type] = totals.get(cost_type, Decimal("0")) + Decimal(str(amount))
        return totals
