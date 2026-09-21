"""Core domain models used across all modules."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"  # reserved for future phases


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class TradeStatus(str, Enum):
    PROPOSED = "PROPOSED"
    AI_ANALYSED = "AI_ANALYSED"
    RISK_APPROVED = "RISK_APPROVED"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_ACKNOWLEDGED = "ORDER_ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    EXIT_PENDING = "EXIT_PENDING"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


class AIDecision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    UNCERTAIN = "UNCERTAIN"


class RiskOutcome(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class NotificationEvent(str, Enum):
    TRADE_OPENED = "TRADE_OPENED"
    TRADE_CLOSED = "TRADE_CLOSED"
    STOP_LOSS_TRIGGERED = "STOP_LOSS_TRIGGERED"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    TRADING_DISABLED = "TRADING_DISABLED"
    IBKR_DISCONNECTED = "IBKR_DISCONNECTED"
    UNEXPECTED_POSITION = "UNEXPECTED_POSITION"
    SYSTEM_ERROR = "SYSTEM_ERROR"
    KILL_SWITCH_ACTIVATED = "KILL_SWITCH_ACTIVATED"


# ---------------------------------------------------------------------------
# Market Data
# ---------------------------------------------------------------------------

class Quote(BaseModel):
    symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume: int
    timestamp: datetime

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid

    @property
    def spread_pct(self) -> Decimal:
        if self.mid == 0:
            return Decimal("999")
        return self.spread / self.mid

    @property
    def age_seconds(self) -> float:
        return (datetime.utcnow() - self.timestamp).total_seconds()

    @property
    def is_stale(self, max_age: float = 30.0) -> bool:
        return self.age_seconds > max_age


class Candle(BaseModel):
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    interval: str


class MarketStatus(BaseModel):
    is_open: bool
    session: str  # "PRE", "REGULAR", "POST", "CLOSED"
    next_open: Optional[datetime] = None
    next_close: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Technical Indicators
# ---------------------------------------------------------------------------

class TechnicalIndicators(BaseModel):
    symbol: str
    timestamp: datetime
    price: Decimal
    ema_short: Optional[Decimal] = None
    ema_long: Optional[Decimal] = None
    sma_20: Optional[Decimal] = None
    rsi_14: Optional[Decimal] = None
    atr_14: Optional[Decimal] = None
    volume: int = 0
    avg_volume_20: Optional[Decimal] = None
    relative_volume: Optional[Decimal] = None
    momentum_5d: Optional[Decimal] = None
    volatility_20d: Optional[Decimal] = None
    spread_pct: Optional[Decimal] = None

    @property
    def is_bullish_trend(self) -> bool:
        if self.ema_short is None or self.ema_long is None:
            return False
        return self.price > self.ema_short > self.ema_long


# ---------------------------------------------------------------------------
# Trade Signal
# ---------------------------------------------------------------------------

class TradeSignal(BaseModel):
    symbol: str
    direction: Direction
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    confidence: float = Field(ge=0.0, le=1.0)
    strategy_name: str
    reason: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    indicators: Optional[TechnicalIndicators] = None


# ---------------------------------------------------------------------------
# AI Analysis
# ---------------------------------------------------------------------------

class AIAnalysisInput(BaseModel):
    symbol: str
    price: Decimal
    indicators: TechnicalIndicators
    signal: TradeSignal
    news_summary: Optional[str] = None
    earnings_upcoming: bool = False
    market_regime: Optional[str] = None


class AIAnalysisOutput(BaseModel):
    decision: AIDecision
    confidence: float = Field(ge=0.0, le=1.0)
    risk_flags: list[str] = Field(default_factory=list)
    reason: str
    suggested_action: str
    raw_response: Optional[str] = None
    model: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: Decimal = Decimal("0")


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------

class RiskViolation(BaseModel):
    rule: str
    message: str
    value: Optional[str] = None
    limit: Optional[str] = None


class RiskDecision(BaseModel):
    outcome: RiskOutcome
    violations: list[RiskViolation] = Field(default_factory=list)
    approved_quantity: int = 0
    approved_entry: Optional[Decimal] = None
    approved_stop: Optional[Decimal] = None
    approved_target: Optional[Decimal] = None


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------

class Position(BaseModel):
    symbol: str
    quantity: int
    avg_cost: Decimal
    current_price: Decimal
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    trade_id: Optional[str] = None
    opened_at: Optional[datetime] = None

    @property
    def market_value(self) -> Decimal:
        return Decimal(str(self.quantity)) * self.current_price

    @property
    def unrealized_pnl(self) -> Decimal:
        return Decimal(str(self.quantity)) * (self.current_price - self.avg_cost)

    @property
    def unrealized_pnl_pct(self) -> Decimal:
        if self.avg_cost == 0:
            return Decimal("0")
        return (self.current_price - self.avg_cost) / self.avg_cost


class AccountState(BaseModel):
    net_liquidation: Decimal
    buying_power: Decimal
    cash: Decimal
    gross_position_value: Decimal
    realized_pnl_today: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    positions: list[Position] = Field(default_factory=list)
    currency: str = "USD"
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Trade Proposal & Record
# ---------------------------------------------------------------------------

class TradeProposal(BaseModel):
    trade_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    symbol: str
    direction: Direction
    strategy_name: str
    signal: TradeSignal
    ai_output: Optional[AIAnalysisOutput] = None
    risk_decision: Optional[RiskDecision] = None
    quantity: int = 0
    entry_price: Decimal = Decimal("0")
    stop_loss: Decimal = Decimal("0")
    take_profit: Decimal = Decimal("0")
    order_type: OrderType = OrderType.LIMIT
    status: TradeStatus = TradeStatus.PROPOSED
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TradeRecord(BaseModel):
    trade_id: str
    symbol: str
    strategy: str
    direction: Direction
    signal_timestamp: datetime
    ai_decision: Optional[AIDecision] = None
    ai_confidence: Optional[float] = None
    ai_reason: Optional[str] = None
    entry_proposal: Optional[Decimal] = None
    actual_entry: Optional[Decimal] = None
    quantity: int = 0
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    exit_price: Optional[Decimal] = None
    commission: Decimal = Decimal("0")
    estimated_slippage: Decimal = Decimal("0")
    realized_pnl: Optional[Decimal] = None
    mfe: Optional[Decimal] = None  # max favorable excursion
    mae: Optional[Decimal] = None  # max adverse excursion
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    broker_order_id: Optional[str] = None
    status: TradeStatus = TradeStatus.PROPOSED
    rejection_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------

class PerformanceMetrics(BaseModel):
    period_start: datetime
    period_end: datetime
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    avg_winner: Decimal = Decimal("0")
    avg_loser: Decimal = Decimal("0")
    profit_factor: float = 0.0
    expectancy_per_trade: Decimal = Decimal("0")
    total_return: Decimal = Decimal("0")
    annualized_return: float = 0.0
    max_drawdown: Decimal = Decimal("0")
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    total_commission: Decimal = Decimal("0")
    total_slippage: Decimal = Decimal("0")
    avg_holding_period_hours: float = 0.0

    @property
    def loss_rate(self) -> float:
        return 1.0 - self.win_rate
