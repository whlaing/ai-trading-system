"""
Deterministic Risk Engine — the most important component.

ALL risk rules are enforced here, regardless of what the AI recommends.
AI cannot override this engine. If ANY mandatory rule fails, the trade
is rejected and cannot proceed.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from src.common.config import Settings
from src.common.exceptions import KillSwitchActiveError
from src.common.logging import get_logger
from src.common.models import (
    AccountState,
    AIDecision,
    RiskDecision,
    RiskOutcome,
    RiskViolation,
    TradeProposal,
)
from src.risk.position_sizing import calculate_position_size

log = get_logger(__name__)


class KillSwitch:
    """Thread-safe global kill switch for trading operations."""

    def __init__(self, initial_state: bool = True) -> None:
        self._enabled = initial_state
        self._reason: Optional[str] = None
        self._disabled_at: Optional[datetime] = None

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def disable(self, reason: str) -> None:
        self._enabled = False
        self._reason = reason
        self._disabled_at = datetime.utcnow()
        log.warning("kill_switch.disabled", reason=reason)

    def enable(self) -> None:
        self._enabled = True
        self._reason = None
        self._disabled_at = None
        log.info("kill_switch.enabled")

    def require_enabled(self) -> None:
        if not self._enabled:
            raise KillSwitchActiveError(
                f"Kill switch active: {self._reason or 'reason unknown'}"
            )


class RiskEngine:
    """
    Validates a trade proposal against all configured risk parameters.

    The validate() method returns a RiskDecision. If outcome is REJECTED,
    the list of violations explains the reasons.

    This engine is STATELESS for the core calculations — account state
    and daily P/L are passed in explicitly to keep the engine testable.
    """

    def __init__(self, settings: Settings, kill_switch: KillSwitch) -> None:
        self._s = settings
        self._kill_switch = kill_switch

    def validate(
        self,
        proposal: TradeProposal,
        account: AccountState,
        daily_pnl: Decimal,
        daily_trade_count: int,
        open_position_count: int,
        has_existing_position: bool,
        last_loss_time: Optional[datetime] = None,
        quote_spread_pct: Optional[Decimal] = None,
        quote_volume: Optional[int] = None,
    ) -> RiskDecision:
        violations: list[RiskViolation] = []

        # --- Kill switch ---
        if not self._kill_switch.is_enabled:
            violations.append(
                RiskViolation(rule="KILL_SWITCH", message="Global trading kill switch is active")
            )
            return RiskDecision(outcome=RiskOutcome.REJECTED, violations=violations)

        # --- Market hours ---
        if not self._s.is_backtest:
            from src.market_data.providers.ibkr_provider import IBKRMarketDataProvider
            now_utc = datetime.utcnow()
            market_open = now_utc.replace(hour=14, minute=30, second=0, microsecond=0)
            market_close = now_utc.replace(hour=20, minute=55, second=0, microsecond=0)
            is_weekend = now_utc.weekday() >= 5
            in_session = market_open <= now_utc <= market_close and not is_weekend
            if not in_session:
                violations.append(
                    RiskViolation(rule="MARKET_HOURS_ONLY", message="Market is not in regular trading session")
                )

        # --- Live mode guard ---
        if self._s.is_live and not self._s.trading_enabled:
            violations.append(
                RiskViolation(rule="LIVE_TRADING_DISABLED", message="Live trading is explicitly disabled")
            )

        # --- Duplicate position ---
        if has_existing_position:
            violations.append(
                RiskViolation(
                    rule="NO_DUPLICATE_POSITION",
                    message=f"Already have an open position in {proposal.symbol}",
                )
            )

        # --- Max open positions ---
        if open_position_count >= self._s.max_open_positions:
            violations.append(
                RiskViolation(
                    rule="MAX_OPEN_POSITIONS",
                    message=f"Open positions {open_position_count} >= limit {self._s.max_open_positions}",
                    value=str(open_position_count),
                    limit=str(self._s.max_open_positions),
                )
            )

        # --- Max daily trades ---
        if daily_trade_count >= self._s.max_daily_trades:
            violations.append(
                RiskViolation(
                    rule="MAX_DAILY_TRADES",
                    message=f"Daily trades {daily_trade_count} >= limit {self._s.max_daily_trades}",
                    value=str(daily_trade_count),
                    limit=str(self._s.max_daily_trades),
                )
            )

        # --- Max daily loss ---
        if daily_pnl <= -self._s.max_daily_loss_usd:
            violations.append(
                RiskViolation(
                    rule="MAX_DAILY_LOSS",
                    message=f"Daily P/L {daily_pnl} has breached limit {-self._s.max_daily_loss_usd}",
                    value=str(daily_pnl),
                    limit=str(-self._s.max_daily_loss_usd),
                )
            )

        # --- Spread filter ---
        if quote_spread_pct is not None and quote_spread_pct > self._s.max_spread_pct:
            violations.append(
                RiskViolation(
                    rule="MAX_SPREAD",
                    message=f"Spread {float(quote_spread_pct):.4%} exceeds limit {float(self._s.max_spread_pct):.4%}",
                    value=str(quote_spread_pct),
                    limit=str(self._s.max_spread_pct),
                )
            )

        # --- Minimum liquidity ---
        if quote_volume is not None and quote_volume < self._s.min_volume:
            violations.append(
                RiskViolation(
                    rule="MIN_LIQUIDITY",
                    message=f"Volume {quote_volume} below minimum {self._s.min_volume}",
                    value=str(quote_volume),
                    limit=str(self._s.min_volume),
                )
            )

        # --- Cooldown after loss ---
        if last_loss_time is not None:
            cooldown_end = last_loss_time + timedelta(minutes=self._s.cooldown_after_loss_minutes)
            if datetime.utcnow() < cooldown_end:
                remaining = int((cooldown_end - datetime.utcnow()).total_seconds() / 60)
                violations.append(
                    RiskViolation(
                        rule="COOLDOWN_AFTER_LOSS",
                        message=f"In cooldown after loss, {remaining}min remaining",
                    )
                )

        # --- AI decision must not be REJECT ---
        if proposal.ai_output and proposal.ai_output.decision == AIDecision.REJECT:
            violations.append(
                RiskViolation(
                    rule="AI_REJECTED",
                    message=f"AI rejected trade: {proposal.ai_output.reason}",
                )
            )

        # If there are any violations so far, reject immediately
        if violations:
            log.info(
                "risk.rejected",
                symbol=proposal.symbol,
                violations=[v.rule for v in violations],
            )
            return RiskDecision(outcome=RiskOutcome.REJECTED, violations=violations)

        # --- Position sizing ---
        shares = calculate_position_size(
            account_value=account.net_liquidation,
            entry_price=proposal.signal.entry_price,
            stop_price=proposal.signal.stop_loss,
            max_risk_usd=self._s.max_risk_per_trade_usd,
            max_position_value_usd=self._s.max_position_value_usd,
        )

        if shares < 1:
            violations.append(
                RiskViolation(
                    rule="MAX_POSITION_VALUE",
                    message="Calculated position size is zero — entry price too high for risk parameters",
                )
            )
            return RiskDecision(outcome=RiskOutcome.REJECTED, violations=violations)

        # --- Total exposure check ---
        proposed_value = Decimal(str(shares)) * proposal.signal.entry_price
        current_exposure = account.gross_position_value
        total_exposure = current_exposure + proposed_value
        max_exposure = account.net_liquidation * self._s.max_total_exposure_pct
        if total_exposure > max_exposure:
            violations.append(
                RiskViolation(
                    rule="MAX_TOTAL_EXPOSURE",
                    message=f"Total exposure ${total_exposure:.0f} would exceed limit ${max_exposure:.0f}",
                    value=str(total_exposure),
                    limit=str(max_exposure),
                )
            )
            return RiskDecision(outcome=RiskOutcome.REJECTED, violations=violations)

        log.info(
            "risk.approved",
            symbol=proposal.symbol,
            shares=shares,
            entry=str(proposal.signal.entry_price),
            stop=str(proposal.signal.stop_loss),
            target=str(proposal.signal.take_profit),
            risk_usd=str(Decimal(str(shares)) * (proposal.signal.entry_price - proposal.signal.stop_loss)),
        )

        return RiskDecision(
            outcome=RiskOutcome.APPROVED,
            violations=[],
            approved_quantity=shares,
            approved_entry=proposal.signal.entry_price,
            approved_stop=proposal.signal.stop_loss,
            approved_target=proposal.signal.take_profit,
        )
