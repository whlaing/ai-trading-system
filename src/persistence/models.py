"""SQLAlchemy ORM models."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.persistence.database import Base


class TradeORM(Base):
    __tablename__ = "trades"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    strategy: Mapped[str] = mapped_column(String(100), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)

    # Signal
    signal_timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # AI analysis
    ai_decision: Mapped[str | None] = mapped_column(String(20))
    ai_confidence: Mapped[float | None] = mapped_column(Float)
    ai_reason: Mapped[str | None] = mapped_column(Text)
    ai_cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 6))

    # Prices
    entry_proposal: Mapped[float | None] = mapped_column(Numeric(18, 6))
    actual_entry: Mapped[float | None] = mapped_column(Numeric(18, 6))
    stop_loss: Mapped[float | None] = mapped_column(Numeric(18, 6))
    take_profit: Mapped[float | None] = mapped_column(Numeric(18, 6))
    exit_price: Mapped[float | None] = mapped_column(Numeric(18, 6))

    # Quantity & P/L
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    commission: Mapped[float] = mapped_column(Numeric(10, 4), default=0)
    estimated_slippage: Mapped[float] = mapped_column(Numeric(10, 4), default=0)
    realized_pnl: Mapped[float | None] = mapped_column(Numeric(12, 4))
    mfe: Mapped[float | None] = mapped_column(Numeric(12, 4))
    mae: Mapped[float | None] = mapped_column(Numeric(12, 4))

    # Timestamps
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Broker
    broker_order_id: Mapped[str | None] = mapped_column(String(50))
    rejection_reason: Mapped[str | None] = mapped_column(Text)


class AuditLogORM(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_id: Mapped[str | None] = mapped_column(String(36), index=True)
    symbol: Mapped[str | None] = mapped_column(String(20))
    strategy: Mapped[str | None] = mapped_column(String(100))
    component: Mapped[str] = mapped_column(String(100), nullable=False)
    event: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), default="INFO")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[str | None] = mapped_column(Text)  # JSON blob
    correlation_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class OperationalCostORM(Base):
    __tablename__ = "operational_costs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    date: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    cost_type: Mapped[str] = mapped_column(String(50), nullable=False)
    amount_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    trade_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class KillSwitchStateORM(Base):
    __tablename__ = "kill_switch_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    trading_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    updated_by: Mapped[str | None] = mapped_column(String(100))
