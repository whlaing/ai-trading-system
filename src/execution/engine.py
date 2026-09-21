"""
Execution Engine — orchestrates the full trade lifecycle.

Flow:
  TradeSignal → AI Analysis → Risk Engine → Order Submission → Monitor → Close

Every state transition is persisted. No assumptions are made about
order fills — the engine reconciles against broker state.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from src.ai.agent import AIAnalysisAgent
from src.broker.base import BrokerAdapter
from src.common.config import Settings
from src.common.exceptions import (
    AIInvalidResponseError,
    AITimeoutError,
    BrokerExecutionError,
    KillSwitchActiveError,
)
from src.common.logging import get_logger, bind_trade_context, clear_trade_context
from src.common.models import (
    AIAnalysisInput,
    Direction,
    OrderType,
    RiskOutcome,
    TradeProposal,
    TradeRecord,
    TradeSignal,
    TradeStatus,
)
from src.market_data.service import MarketDataService
from src.persistence.database import get_db
from src.persistence.repositories import AuditRepository, CostRepository, TradeRepository
from src.risk.engine import KillSwitch, RiskEngine

log = get_logger(__name__)


class ExecutionEngine:
    def __init__(
        self,
        broker: BrokerAdapter,
        market_data: MarketDataService,
        risk_engine: RiskEngine,
        ai_agent: AIAnalysisAgent,
        kill_switch: KillSwitch,
        settings: Settings,
    ) -> None:
        self._broker = broker
        self._data = market_data
        self._risk = risk_engine
        self._ai = ai_agent
        self._ks = kill_switch
        self._s = settings
        self._last_loss_time: Optional[datetime] = None

    def process_signal(self, signal: TradeSignal) -> Optional[TradeRecord]:
        """
        Process a trade signal through AI analysis, risk validation,
        and order submission. Returns the final TradeRecord.
        """
        proposal = TradeProposal(
            symbol=signal.symbol,
            direction=signal.direction,
            strategy_name=signal.strategy_name,
            signal=signal,
        )

        bind_trade_context(trade_id=proposal.trade_id, symbol=signal.symbol, strategy=signal.strategy_name)
        log.info("execution.signal_received", direction=signal.direction, price=str(signal.entry_price))

        try:
            record = TradeRecord(
                trade_id=proposal.trade_id,
                symbol=signal.symbol,
                strategy=signal.strategy_name,
                direction=signal.direction,
                signal_timestamp=signal.timestamp,
                entry_proposal=signal.entry_price,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                status=TradeStatus.PROPOSED,
            )

            with get_db() as db:
                trade_repo = TradeRepository(db)
                audit_repo = AuditRepository(db)
                cost_repo = CostRepository(db)

                trade_repo.save(record)
                audit_repo.log_event("execution", "signal_received", f"Signal for {signal.symbol}", trade_id=record.trade_id, symbol=signal.symbol, strategy=signal.strategy_name)

            # --- Kill switch check ---
            try:
                self._ks.require_enabled()
            except KillSwitchActiveError as exc:
                return self._reject(record, str(exc), "KILL_SWITCH")

            # --- AI Analysis ---
            record = self._run_ai_analysis(proposal, record)
            if record.status == TradeStatus.REJECTED:
                return record

            # --- Risk Engine ---
            record = self._run_risk_check(proposal, record)
            if record.status == TradeStatus.REJECTED:
                return record

            # --- Order Submission ---
            record = self._submit_order(proposal, record)
            return record

        except Exception as exc:
            log.error("execution.unexpected_error", error=str(exc), exc_info=True)
            return self._reject(record, f"Unexpected error: {exc}", "ERROR")
        finally:
            clear_trade_context()

    def _run_ai_analysis(self, proposal: TradeProposal, record: TradeRecord) -> TradeRecord:
        if not self._s.ai_enabled:
            record.status = TradeStatus.AI_ANALYSED
            self._save(record)
            return record

        try:
            ai_input = AIAnalysisInput(
                symbol=proposal.symbol,
                price=proposal.signal.entry_price,
                indicators=proposal.signal.indicators,
                signal=proposal.signal,
            )
            ai_output = self._ai.analyse(ai_input)
            proposal.ai_output = ai_output

            record.ai_decision = ai_output.decision
            record.ai_confidence = ai_output.confidence
            record.ai_reason = ai_output.reason
            record.status = TradeStatus.AI_ANALYSED

            with get_db() as db:
                TradeRepository(db).save(record)
                AuditRepository(db).log_event(
                    "ai",
                    "analysis_complete",
                    f"AI decision: {ai_output.decision} conf={ai_output.confidence:.2f}",
                    trade_id=record.trade_id,
                    symbol=record.symbol,
                    details={"decision": ai_output.decision.value, "confidence": ai_output.confidence, "risk_flags": ai_output.risk_flags},
                )
                CostRepository(db).record_cost("AI_API", ai_output.cost_usd, f"AI analysis for {proposal.symbol}", record.trade_id)

        except (AITimeoutError, AIInvalidResponseError) as exc:
            log.error("execution.ai_error", error=str(exc))
            return self._reject(record, str(exc), "AI_ERROR")

        return record

    def _run_risk_check(self, proposal: TradeProposal, record: TradeRecord) -> TradeRecord:
        with get_db() as db:
            trade_repo = TradeRepository(db)
            daily_pnl = trade_repo.get_daily_realized_pnl()
            daily_count = trade_repo.count_trades_today()
            open_trades = trade_repo.get_open_trades()
            has_position = trade_repo.has_open_position(proposal.symbol)

        account = self._broker.get_account()

        try:
            quote = self._data.get_quote(proposal.symbol)
            spread_pct = quote.spread_pct
            volume = quote.volume
        except Exception:
            spread_pct = None
            volume = None

        risk_decision = self._risk.validate(
            proposal=proposal,
            account=account,
            daily_pnl=daily_pnl,
            daily_trade_count=daily_count,
            open_position_count=len(open_trades),
            has_existing_position=has_position,
            last_loss_time=self._last_loss_time,
            quote_spread_pct=spread_pct,
            quote_volume=volume,
        )
        proposal.risk_decision = risk_decision

        if risk_decision.outcome == RiskOutcome.REJECTED:
            violations_str = "; ".join(v.rule for v in risk_decision.violations)
            return self._reject(record, violations_str, "RISK_REJECTED")

        proposal.quantity = risk_decision.approved_quantity
        proposal.entry_price = risk_decision.approved_entry
        proposal.stop_loss = risk_decision.approved_stop
        proposal.take_profit = risk_decision.approved_target
        record.quantity = risk_decision.approved_quantity
        record.stop_loss = risk_decision.approved_stop
        record.take_profit = risk_decision.approved_target
        record.status = TradeStatus.RISK_APPROVED

        with get_db() as db:
            TradeRepository(db).save(record)
            AuditRepository(db).log_event(
                "risk",
                "approved",
                f"Risk approved: {risk_decision.approved_quantity} shares",
                trade_id=record.trade_id,
                symbol=record.symbol,
            )

        return record

    def _submit_order(self, proposal: TradeProposal, record: TradeRecord) -> TradeRecord:
        # Apply limit price offset (buy slightly above mid to improve fill probability)
        offset = proposal.entry_price * self._s.limit_order_offset_pct
        limit_price = (proposal.entry_price + offset).quantize(Decimal("0.01"))

        try:
            result = self._broker.submit_order(
                symbol=proposal.symbol,
                quantity=proposal.quantity,
                order_type=OrderType.LIMIT,
                direction=proposal.direction.value,
                limit_price=limit_price,
                stop_price=proposal.stop_loss,
                trade_id=proposal.trade_id,
            )
        except BrokerExecutionError as exc:
            return self._reject(record, str(exc), "BROKER_ERROR")

        record.broker_order_id = result.broker_order_id
        record.submitted_at = datetime.utcnow()

        if result.is_filled:
            record.actual_entry = result.avg_fill_price
            record.commission = result.commission
            record.filled_at = datetime.utcnow()
            record.status = TradeStatus.FILLED
            event = "order_filled"
            msg = f"Filled {result.filled_quantity} @ {result.avg_fill_price}"
        elif result.is_cancelled or result.status == "REJECTED":
            return self._reject(record, result.message or "Broker rejected order", "BROKER_REJECTED")
        else:
            record.status = TradeStatus.ORDER_ACKNOWLEDGED
            event = "order_submitted"
            msg = f"Order submitted, status={result.status}"

        with get_db() as db:
            TradeRepository(db).save(record)
            AuditRepository(db).log_event(
                "execution",
                event,
                msg,
                trade_id=record.trade_id,
                symbol=record.symbol,
            )

        log.info("execution.order_submitted", symbol=proposal.symbol, order_id=result.broker_order_id, status=result.status)
        return record

    def _reject(self, record: TradeRecord, reason: str, event: str) -> TradeRecord:
        record.status = TradeStatus.REJECTED
        record.rejection_reason = reason
        with get_db() as db:
            TradeRepository(db).save(record)
            AuditRepository(db).log_event(
                "execution",
                event,
                reason,
                trade_id=record.trade_id,
                symbol=record.symbol,
                severity="WARNING",
            )
        log.info("execution.rejected", symbol=record.symbol, reason=reason, event=event)
        return record

    def _save(self, record: TradeRecord) -> None:
        with get_db() as db:
            TradeRepository(db).save(record)

    def close_position(self, symbol: str, trade_id: str, reason: str = "strategy_exit") -> Optional[TradeRecord]:
        """Submit a closing order for an open position."""
        with get_db() as db:
            trade_repo = TradeRepository(db)
            record = trade_repo.get_by_id(trade_id)

        if record is None:
            log.error("execution.close_not_found", trade_id=trade_id)
            return None

        bind_trade_context(trade_id=trade_id, symbol=symbol)
        record.status = TradeStatus.EXIT_PENDING
        self._save(record)

        try:
            quote = self._data.get_quote(symbol)
            # Close slightly below bid for faster fill
            limit_price = (quote.bid * Decimal("0.999")).quantize(Decimal("0.01"))

            result = self._broker.submit_order(
                symbol=symbol,
                quantity=record.quantity,
                order_type=OrderType.LIMIT,
                direction="SELL",
                limit_price=limit_price,
                trade_id=trade_id,
            )

            if result.is_filled and result.avg_fill_price:
                cost = record.actual_entry or record.entry_proposal or Decimal("0")
                record.exit_price = result.avg_fill_price
                record.commission = record.commission + result.commission
                qty = Decimal(str(record.quantity))
                record.realized_pnl = (result.avg_fill_price - cost) * qty - record.commission
                record.closed_at = datetime.utcnow()
                record.status = TradeStatus.CLOSED

                if record.realized_pnl < Decimal("0"):
                    self._last_loss_time = datetime.utcnow()

                log.info(
                    "execution.position_closed",
                    symbol=symbol,
                    exit_price=str(result.avg_fill_price),
                    pnl=str(record.realized_pnl),
                    reason=reason,
                )
            else:
                record.status = TradeStatus.EXIT_PENDING

            self._save(record)
            return record

        except Exception as exc:
            log.error("execution.close_error", error=str(exc), exc_info=True)
            record.status = TradeStatus.ERROR
            record.rejection_reason = str(exc)
            self._save(record)
            return record
        finally:
            clear_trade_context()
