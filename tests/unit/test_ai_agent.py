"""Unit tests for AI analysis agent — edge cases and failure modes."""
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.ai.agent import AIAnalysisAgent
from src.common.config import Settings
from src.common.exceptions import AIInvalidResponseError, AITimeoutError
from src.common.models import AIDecision, Direction, TechnicalIndicators, TradeSignal


def _make_settings(**kwargs) -> Settings:
    defaults = dict(
        trading_mode="PAPER",
        ai_provider="openai",
        openai_api_key="sk-test-key",
        ai_model="gpt-4o-mini",
        ai_max_tokens=1024,
        ai_timeout=30,
        ai_enabled=True,
    )
    defaults.update(kwargs)
    return Settings(**defaults)


def _make_signal() -> TradeSignal:
    return TradeSignal(
        symbol="AAPL",
        direction=Direction.LONG,
        entry_price=Decimal("150"),
        stop_loss=Decimal("147"),
        take_profit=Decimal("156"),
        confidence=0.8,
        strategy_name="TrendMomentum",
        reason="test",
        indicators=TechnicalIndicators(
            symbol="AAPL",
            timestamp=datetime.utcnow(),
            price=Decimal("150"),
        ),
    )


class TestAIAgentDisabled:
    def test_returns_uncertain_when_disabled(self):
        settings = _make_settings(ai_enabled=False)
        agent = AIAnalysisAgent(settings)
        from src.common.models import AIAnalysisInput
        output = agent.analyse(AIAnalysisInput(
            symbol="AAPL",
            price=Decimal("150"),
            indicators=TechnicalIndicators(symbol="AAPL", timestamp=datetime.utcnow(), price=Decimal("150")),
            signal=_make_signal(),
        ))
        assert output.decision == AIDecision.UNCERTAIN
        assert "disabled" in output.reason.lower()


class TestAIAgentResponseParsing:
    def setup_method(self):
        self.settings = _make_settings()
        self.agent = AIAnalysisAgent(self.settings)

    # Helper: _parse_response(raw_text, prompt_tokens, completion_tokens, model, symbol)

    def test_raises_on_empty_response(self):
        with pytest.raises(AIInvalidResponseError, match="empty response"):
            self.agent._parse_response("", 100, 50, "gpt-4o-mini", "AAPL")

    def test_raises_on_invalid_json(self):
        with pytest.raises(AIInvalidResponseError, match="invalid JSON"):
            self.agent._parse_response("not json at all", 100, 50, "gpt-4o-mini", "AAPL")

    def test_raises_on_unknown_decision(self):
        with pytest.raises(AIInvalidResponseError, match="unknown decision"):
            self.agent._parse_response(
                '{"decision": "MAYBE", "confidence": 0.5, "risk_flags": [], "reason": "x", "suggested_action": "LONG"}',
                100, 50, "gpt-4o-mini", "AAPL",
            )

    def test_raises_on_confidence_out_of_range(self):
        with pytest.raises(AIInvalidResponseError, match="out of range"):
            self.agent._parse_response(
                '{"decision": "APPROVE", "confidence": 1.5, "risk_flags": [], "reason": "x", "suggested_action": "LONG"}',
                100, 50, "gpt-4o-mini", "AAPL",
            )

    def test_parses_valid_approve(self):
        valid_json = '{"decision": "APPROVE", "confidence": 0.75, "risk_flags": [], "reason": "Good signal", "suggested_action": "LONG"}'
        output = self.agent._parse_response(valid_json, 100, 50, "gpt-4o-mini", "AAPL")
        assert output.decision == AIDecision.APPROVE
        assert output.confidence == 0.75
        assert output.risk_flags == []

    def test_parses_reject_with_flags(self):
        valid_json = '{"decision": "REJECT", "confidence": 0.9, "risk_flags": ["earnings tomorrow", "high volatility"], "reason": "Too risky", "suggested_action": "NONE"}'
        output = self.agent._parse_response(valid_json, 100, 50, "gpt-4o-mini", "AAPL")
        assert output.decision == AIDecision.REJECT
        assert len(output.risk_flags) == 2

    def test_extracts_json_from_markdown_code_block(self):
        markdown = '```json\n{"decision": "APPROVE", "confidence": 0.8, "risk_flags": [], "reason": "Good", "suggested_action": "LONG"}\n```'
        output = self.agent._parse_response(markdown, 100, 50, "gpt-4o-mini", "AAPL")
        assert output.decision == AIDecision.APPROVE

    def test_cost_is_calculated(self):
        valid_json = '{"decision": "APPROVE", "confidence": 0.7, "risk_flags": [], "reason": "ok", "suggested_action": "LONG"}'
        output = self.agent._parse_response(valid_json, 100, 50, "gpt-4o-mini", "AAPL")
        assert output.cost_usd > Decimal("0")
        assert output.prompt_tokens == 100
        assert output.completion_tokens == 50

    def test_anthropic_provider_settings(self):
        """Settings with anthropic provider should not raise on construction."""
        settings = _make_settings(
            ai_provider="anthropic",
            anthropic_api_key="sk-ant-test",
            ai_model="claude-haiku-4-5-20251001",
        )
        agent = AIAnalysisAgent(settings)
        assert agent._s.ai_provider == "anthropic"

    def test_cost_gpt4o_mini_cheaper_than_gpt4o(self):
        valid_json = '{"decision": "APPROVE", "confidence": 0.7, "risk_flags": [], "reason": "ok", "suggested_action": "LONG"}'
        mini_output = self.agent._parse_response(valid_json, 1000, 500, "gpt-4o-mini", "AAPL")
        gpt4_output = self.agent._parse_response(valid_json, 1000, 500, "gpt-4o", "AAPL")
        assert mini_output.cost_usd < gpt4_output.cost_usd
