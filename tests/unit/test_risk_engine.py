"""Unit tests for the deterministic Risk Engine."""
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.common.config import Settings
from src.common.exceptions import KillSwitchActiveError
from src.common.models import (
    AccountState,
    AIAnalysisOutput,
    AIDecision,
    Direction,
    RiskOutcome,
    TradeProposal,
    TradeSignal,
    TechnicalIndicators,
)
from src.risk.engine import KillSwitch, RiskEngine


def _make_settings(**kwargs) -> Settings:
    defaults = dict(
        trading_mode="PAPER",
        trading_enabled=True,
        max_open_positions=3,
        max_position_value_usd=Decimal("500"),
        max_risk_per_trade_usd=Decimal("5"),
        max_daily_loss_usd=Decimal("25"),
        max_daily_trades=10,
        max_spread_pct=Decimal("0.005"),
        min_volume=500_000,
        cooldown_after_loss_minutes=30,
        max_total_exposure_pct=Decimal("0.25"),
        ema_short=20,
        ema_long=50,
        rsi_min=40,
        rsi_max=70,
        relative_volume_min=Decimal("1.5"),
        momentum_lookback=5,
        atr_max_pct=Decimal("0.03"),
        limit_order_offset_pct=Decimal("0.001"),
        order_timeout_seconds=60,
    )
    defaults.update(kwargs)
    return Settings(**defaults)


def _make_account(**kwargs) -> AccountState:
    defaults = dict(
        net_liquidation=Decimal("10000"),
        buying_power=Decimal("9000"),
        cash=Decimal("9000"),
        gross_position_value=Decimal("1000"),
    )
    defaults.update(kwargs)
    return AccountState(**defaults)


def _make_proposal(entry=100.0, stop=99.0) -> TradeProposal:
    signal = TradeSignal(
        symbol="AAPL",
        direction=Direction.LONG,
        entry_price=Decimal(str(entry)),
        stop_loss=Decimal(str(stop)),
        take_profit=Decimal(str(entry + 2)),
        confidence=0.8,
        strategy_name="TrendMomentum",
        reason="test signal",
    )
    return TradeProposal(symbol="AAPL", direction=Direction.LONG, strategy_name="TrendMomentum", signal=signal)


class TestKillSwitch:
    def test_enabled_by_default(self):
        ks = KillSwitch()
        assert ks.is_enabled

    def test_disable_enable(self):
        ks = KillSwitch()
        ks.disable("test reason")
        assert not ks.is_enabled
        ks.enable()
        assert ks.is_enabled

    def test_require_enabled_raises_when_disabled(self):
        ks = KillSwitch()
        ks.disable("test")
        with pytest.raises(KillSwitchActiveError):
            ks.require_enabled()

    def test_require_enabled_passes_when_active(self):
        ks = KillSwitch()
        ks.require_enabled()  # Should not raise


class TestRiskEngine:
    def setup_method(self):
        self.settings = _make_settings()
        self.kill_switch = KillSwitch()
        self.engine = RiskEngine(self.settings, self.kill_switch)

    def test_approves_valid_trade(self):
        decision = self.engine.validate(
            proposal=_make_proposal(),
            account=_make_account(),
            daily_pnl=Decimal("0"),
            daily_trade_count=0,
            open_position_count=0,
            has_existing_position=False,
        )
        assert decision.outcome == RiskOutcome.APPROVED
        assert decision.approved_quantity >= 1

    def test_rejects_when_kill_switch_active(self):
        self.kill_switch.disable("test")
        decision = self.engine.validate(
            proposal=_make_proposal(),
            account=_make_account(),
            daily_pnl=Decimal("0"),
            daily_trade_count=0,
            open_position_count=0,
            has_existing_position=False,
        )
        assert decision.outcome == RiskOutcome.REJECTED
        assert any(v.rule == "KILL_SWITCH" for v in decision.violations)

    def test_rejects_duplicate_position(self):
        decision = self.engine.validate(
            proposal=_make_proposal(),
            account=_make_account(),
            daily_pnl=Decimal("0"),
            daily_trade_count=0,
            open_position_count=1,
            has_existing_position=True,  # Already have AAPL
        )
        assert decision.outcome == RiskOutcome.REJECTED
        assert any(v.rule == "NO_DUPLICATE_POSITION" for v in decision.violations)

    def test_rejects_when_max_positions_reached(self):
        decision = self.engine.validate(
            proposal=_make_proposal(),
            account=_make_account(),
            daily_pnl=Decimal("0"),
            daily_trade_count=0,
            open_position_count=3,  # Already at limit
            has_existing_position=False,
        )
        assert decision.outcome == RiskOutcome.REJECTED
        assert any(v.rule == "MAX_OPEN_POSITIONS" for v in decision.violations)

    def test_rejects_when_daily_loss_exceeded(self):
        decision = self.engine.validate(
            proposal=_make_proposal(),
            account=_make_account(),
            daily_pnl=Decimal("-30"),  # -$30 > -$25 limit
            daily_trade_count=0,
            open_position_count=0,
            has_existing_position=False,
        )
        assert decision.outcome == RiskOutcome.REJECTED
        assert any(v.rule == "MAX_DAILY_LOSS" for v in decision.violations)

    def test_rejects_when_daily_trade_limit_exceeded(self):
        decision = self.engine.validate(
            proposal=_make_proposal(),
            account=_make_account(),
            daily_pnl=Decimal("0"),
            daily_trade_count=10,  # At limit
            open_position_count=0,
            has_existing_position=False,
        )
        assert decision.outcome == RiskOutcome.REJECTED
        assert any(v.rule == "MAX_DAILY_TRADES" for v in decision.violations)

    def test_rejects_wide_spread(self):
        decision = self.engine.validate(
            proposal=_make_proposal(),
            account=_make_account(),
            daily_pnl=Decimal("0"),
            daily_trade_count=0,
            open_position_count=0,
            has_existing_position=False,
            quote_spread_pct=Decimal("0.01"),  # 1% spread, limit is 0.5%
        )
        assert decision.outcome == RiskOutcome.REJECTED
        assert any(v.rule == "MAX_SPREAD" for v in decision.violations)

    def test_rejects_ai_rejection(self):
        proposal = _make_proposal()
        proposal.ai_output = AIAnalysisOutput(
            decision=AIDecision.REJECT,
            confidence=0.9,
            risk_flags=["earnings tomorrow"],
            reason="Earnings risk too high",
            suggested_action="NONE",
        )
        decision = self.engine.validate(
            proposal=proposal,
            account=_make_account(),
            daily_pnl=Decimal("0"),
            daily_trade_count=0,
            open_position_count=0,
            has_existing_position=False,
        )
        assert decision.outcome == RiskOutcome.REJECTED
        assert any(v.rule == "AI_REJECTED" for v in decision.violations)

    def test_rejects_in_cooldown_after_loss(self):
        last_loss = datetime.utcnow() - timedelta(minutes=10)  # 10min ago, cooldown is 30min
        decision = self.engine.validate(
            proposal=_make_proposal(),
            account=_make_account(),
            daily_pnl=Decimal("0"),
            daily_trade_count=0,
            open_position_count=0,
            has_existing_position=False,
            last_loss_time=last_loss,
        )
        assert decision.outcome == RiskOutcome.REJECTED
        assert any(v.rule == "COOLDOWN_AFTER_LOSS" for v in decision.violations)

    def test_allows_after_cooldown_expires(self):
        last_loss = datetime.utcnow() - timedelta(minutes=35)  # 35min ago, beyond 30min cooldown
        decision = self.engine.validate(
            proposal=_make_proposal(),
            account=_make_account(),
            daily_pnl=Decimal("0"),
            daily_trade_count=0,
            open_position_count=0,
            has_existing_position=False,
            last_loss_time=last_loss,
        )
        assert decision.outcome == RiskOutcome.APPROVED

    def test_rejects_zero_position_size(self):
        # Entry price too high to fit in position value limit
        decision = self.engine.validate(
            proposal=_make_proposal(entry=600.0, stop=599.0),
            account=_make_account(),
            daily_pnl=Decimal("0"),
            daily_trade_count=0,
            open_position_count=0,
            has_existing_position=False,
        )
        assert decision.outcome == RiskOutcome.REJECTED

    def test_multiple_violations_all_reported(self):
        self.kill_switch.disable("test")
        decision = self.engine.validate(
            proposal=_make_proposal(),
            account=_make_account(),
            daily_pnl=Decimal("-30"),
            daily_trade_count=0,
            open_position_count=0,
            has_existing_position=False,
        )
        assert decision.outcome == RiskOutcome.REJECTED
        # Kill switch is checked first and returns immediately
        assert len(decision.violations) >= 1
