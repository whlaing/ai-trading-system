"""
AI Analysis Agent.

Receives only pre-filtered trade candidates and returns a structured
APPROVE / REJECT / UNCERTAIN decision with reasoning.

The AI layer is a advisory opinion — the deterministic Risk Engine
retains final authority. If the AI is unavailable, existing positions
continue to be monitored and protected but no new AI-dependent trades
are opened.
"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Optional

import anthropic

from src.common.config import Settings
from src.common.exceptions import AIInvalidResponseError, AITimeoutError
from src.common.logging import get_logger
from src.common.models import AIAnalysisInput, AIAnalysisOutput, AIDecision

log = get_logger(__name__)

_SYSTEM_PROMPT = """\
You are a quantitative trading analysis assistant.

Your role is to review a pre-filtered trade candidate and provide a
structured risk assessment. You are NOT the final decision-maker —
a deterministic risk engine makes the final call.

Evaluate the provided data and respond with ONLY a valid JSON object.
Do not include any text outside the JSON object.

Required JSON structure:
{
  "decision": "APPROVE" | "REJECT" | "UNCERTAIN",
  "confidence": <float 0.0-1.0>,
  "risk_flags": [<list of concern strings, may be empty>],
  "reason": "<concise explanation>",
  "suggested_action": "LONG" | "NONE"
}

Rules:
- APPROVE only when there are genuine positive signals
- REJECT when there are clear negative signals or meaningful risk flags
- UNCERTAIN when evidence is mixed or insufficient
- Be conservative; capital protection is paramount
- Never suggest SHORT positions (not supported in MVP)
"""


class AIAnalysisAgent:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Optional[anthropic.Anthropic] = None

    def _get_client(self) -> anthropic.Anthropic:
        if self._client is None:
            if not self._settings.anthropic_api_key:
                raise AIInvalidResponseError("ANTHROPIC_API_KEY not configured")
            self._client = anthropic.Anthropic(api_key=self._settings.anthropic_api_key)
        return self._client

    def analyse(self, input_data: AIAnalysisInput) -> AIAnalysisOutput:
        if not self._settings.ai_enabled:
            log.info("ai.disabled", symbol=input_data.symbol)
            return AIAnalysisOutput(
                decision=AIDecision.UNCERTAIN,
                confidence=0.0,
                risk_flags=["AI analysis disabled"],
                reason="AI analysis is currently disabled",
                suggested_action="NONE",
            )

        prompt = self._build_prompt(input_data)

        try:
            client = self._get_client()
            response = client.messages.create(
                model=self._settings.ai_model,
                max_tokens=self._settings.ai_max_tokens,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                timeout=self._settings.ai_timeout,
            )
        except anthropic.APITimeoutError as exc:
            raise AITimeoutError(f"AI API timed out for {input_data.symbol}") from exc
        except anthropic.APIError as exc:
            raise AIInvalidResponseError(f"AI API error: {exc}") from exc

        raw_text = response.content[0].text if response.content else ""
        output = self._parse_response(raw_text, response, input_data.symbol)

        log.info(
            "ai.analysis",
            symbol=input_data.symbol,
            decision=output.decision,
            confidence=output.confidence,
            risk_flags=output.risk_flags,
            prompt_tokens=output.prompt_tokens,
            completion_tokens=output.completion_tokens,
            cost_usd=str(output.cost_usd),
        )
        return output

    def _build_prompt(self, data: AIAnalysisInput) -> str:
        t = data.indicators
        lines = [
            f"Symbol: {data.symbol}",
            f"Timestamp: {datetime.utcnow().isoformat()}",
            f"Current Price: ${float(data.price):.2f}",
            "",
            "=== Technical Indicators ===",
            f"EMA(20): {float(t.ema_short):.2f}" if t.ema_short else "EMA(20): N/A",
            f"EMA(50): {float(t.ema_long):.2f}" if t.ema_long else "EMA(50): N/A",
            f"RSI(14): {float(t.rsi_14):.1f}" if t.rsi_14 else "RSI(14): N/A",
            f"ATR(14): {float(t.atr_14):.4f}" if t.atr_14 else "ATR(14): N/A",
            f"Relative Volume: {float(t.relative_volume):.2f}x" if t.relative_volume else "Relative Volume: N/A",
            f"5d Momentum: {float(t.momentum_5d):.3%}" if t.momentum_5d else "5d Momentum: N/A",
            f"20d Volatility: {float(t.volatility_20d):.3%}" if t.volatility_20d else "20d Volatility: N/A",
            f"Spread: {float(t.spread_pct):.4%}" if t.spread_pct else "Spread: N/A",
            "",
            "=== Strategy Signal ===",
            f"Strategy: {data.signal.strategy_name}",
            f"Direction: {data.signal.direction}",
            f"Proposed Entry: ${float(data.signal.entry_price):.2f}",
            f"Stop Loss: ${float(data.signal.stop_loss):.2f}",
            f"Take Profit: ${float(data.signal.take_profit):.2f}",
            f"Strategy Confidence: {data.signal.confidence:.2f}",
            f"Signal Reason: {data.signal.reason}",
        ]

        if data.news_summary:
            lines += ["", "=== Recent News ===", data.news_summary]

        if data.earnings_upcoming:
            lines += ["", "WARNING: Earnings event is upcoming. Exercise additional caution."]

        if data.market_regime:
            lines += ["", f"Market Regime: {data.market_regime}"]

        return "\n".join(lines)

    def _parse_response(
        self,
        raw_text: str,
        response: anthropic.types.Message,
        symbol: str,
    ) -> AIAnalysisOutput:
        # Extract token counts and estimate cost
        prompt_tokens = response.usage.input_tokens if response.usage else 0
        completion_tokens = response.usage.output_tokens if response.usage else 0
        # Approximate cost for Claude Sonnet (adjust per current pricing)
        cost = Decimal(str(prompt_tokens)) * Decimal("0.000003") + Decimal(str(completion_tokens)) * Decimal("0.000015")

        if not raw_text.strip():
            raise AIInvalidResponseError(f"AI returned empty response for {symbol}")

        # Extract JSON from the response (handle markdown code blocks)
        json_text = raw_text.strip()
        if "```" in json_text:
            start = json_text.find("{")
            end = json_text.rfind("}") + 1
            if start >= 0 and end > start:
                json_text = json_text[start:end]

        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as exc:
            raise AIInvalidResponseError(
                f"AI returned invalid JSON for {symbol}: {exc}. Raw: {raw_text[:200]}"
            ) from exc

        # Validate required fields
        decision_str = data.get("decision", "")
        try:
            decision = AIDecision(decision_str)
        except ValueError:
            raise AIInvalidResponseError(
                f"AI returned unknown decision '{decision_str}' for {symbol}"
            )

        confidence = float(data.get("confidence", 0.0))
        if not 0.0 <= confidence <= 1.0:
            raise AIInvalidResponseError(f"AI confidence {confidence} out of range for {symbol}")

        return AIAnalysisOutput(
            decision=decision,
            confidence=confidence,
            risk_flags=data.get("risk_flags", []),
            reason=str(data.get("reason", "")),
            suggested_action=str(data.get("suggested_action", "NONE")),
            raw_response=raw_text,
            model=response.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
        )
