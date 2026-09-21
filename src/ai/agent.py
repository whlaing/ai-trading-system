"""
AI Analysis Agent.

Supports OpenAI and Anthropic as providers, selected by AI_PROVIDER in config.

The AI layer is advisory — the deterministic Risk Engine retains final authority.
If the AI is unavailable, no new AI-dependent trades are opened but existing
positions continue to be monitored and protected.
"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Optional

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

# Approximate cost per token (input, output) by known model prefixes
_COST_TABLE: dict[str, tuple[float, float]] = {
    "gpt-4o-mini":           (0.00000015,  0.00000060),
    "gpt-4o":                (0.0000025,   0.000010),
    "gpt-4-turbo":           (0.000010,    0.000030),
    "o1-mini":               (0.0000030,   0.000012),
    "o1":                    (0.000015,    0.000060),
    "claude-opus":           (0.000015,    0.000075),
    "claude-sonnet":         (0.000003,    0.000015),
    "claude-haiku":          (0.00000025,  0.00000125),
}


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> Decimal:
    for prefix, (in_price, out_price) in _COST_TABLE.items():
        if model.lower().startswith(prefix):
            cost = prompt_tokens * in_price + completion_tokens * out_price
            return Decimal(str(round(cost, 8)))
    # Unknown model — use a conservative fallback
    return Decimal(str(round(prompt_tokens * 0.000003 + completion_tokens * 0.000015, 8)))


class AIAnalysisAgent:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._client: Optional[object] = None

    # ── Public interface ─────────────────────────────────────────────────────

    def analyse(self, input_data: AIAnalysisInput) -> AIAnalysisOutput:
        if not self._s.ai_enabled:
            log.info("ai.disabled", symbol=input_data.symbol)
            return AIAnalysisOutput(
                decision=AIDecision.UNCERTAIN,
                confidence=0.0,
                risk_flags=["AI analysis disabled"],
                reason="AI analysis is currently disabled",
                suggested_action="NONE",
            )

        prompt = self._build_prompt(input_data)

        provider = self._s.ai_provider.lower()
        if provider == "openai":
            raw_text, prompt_tokens, completion_tokens, model = self._call_openai(prompt)
        elif provider == "anthropic":
            raw_text, prompt_tokens, completion_tokens, model = self._call_anthropic(prompt)
        else:
            raise AIInvalidResponseError(f"Unknown AI_PROVIDER '{provider}'. Use 'openai' or 'anthropic'.")

        output = self._parse_response(raw_text, prompt_tokens, completion_tokens, model, input_data.symbol)

        log.info(
            "ai.analysis",
            symbol=input_data.symbol,
            provider=provider,
            model=model,
            decision=output.decision,
            confidence=output.confidence,
            risk_flags=output.risk_flags,
            prompt_tokens=output.prompt_tokens,
            completion_tokens=output.completion_tokens,
            cost_usd=str(output.cost_usd),
        )
        return output

    # ── Provider calls ───────────────────────────────────────────────────────

    def _call_openai(self, prompt: str) -> tuple[str, int, int, str]:
        try:
            import openai
        except ImportError:
            raise AIInvalidResponseError(
                "openai package not installed. Run: pip install openai"
            )

        if not self._s.openai_api_key:
            raise AIInvalidResponseError("OPENAI_API_KEY not configured")

        if self._client is None:
            self._client = openai.OpenAI(api_key=self._s.openai_api_key)

        try:
            resp = self._client.chat.completions.create(
                model=self._s.ai_model,
                max_tokens=self._s.ai_max_tokens,
                timeout=self._s.ai_timeout,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
            )
        except openai.APITimeoutError as exc:
            raise AITimeoutError(f"OpenAI API timed out") from exc
        except openai.APIError as exc:
            raise AIInvalidResponseError(f"OpenAI API error: {exc}") from exc

        raw_text = resp.choices[0].message.content or ""
        pt = resp.usage.prompt_tokens if resp.usage else 0
        ct = resp.usage.completion_tokens if resp.usage else 0
        return raw_text, pt, ct, resp.model

    def _call_anthropic(self, prompt: str) -> tuple[str, int, int, str]:
        try:
            import anthropic
        except ImportError:
            raise AIInvalidResponseError(
                "anthropic package not installed. Run: pip install anthropic"
            )

        if not self._s.anthropic_api_key:
            raise AIInvalidResponseError("ANTHROPIC_API_KEY not configured")

        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self._s.anthropic_api_key)

        try:
            resp = self._client.messages.create(
                model=self._s.ai_model,
                max_tokens=self._s.ai_max_tokens,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                timeout=self._s.ai_timeout,
            )
        except anthropic.APITimeoutError as exc:
            raise AITimeoutError(f"Anthropic API timed out") from exc
        except anthropic.APIError as exc:
            raise AIInvalidResponseError(f"Anthropic API error: {exc}") from exc

        raw_text = resp.content[0].text if resp.content else ""
        pt = resp.usage.input_tokens if resp.usage else 0
        ct = resp.usage.output_tokens if resp.usage else 0
        return raw_text, pt, ct, resp.model

    # ── Shared parser ────────────────────────────────────────────────────────

    def _parse_response(
        self,
        raw_text: str,
        prompt_tokens: int,
        completion_tokens: int,
        model: str,
        symbol: str,
    ) -> AIAnalysisOutput:
        if not raw_text.strip():
            raise AIInvalidResponseError(f"AI returned empty response for {symbol}")

        # Strip markdown code fences if present
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
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=_estimate_cost(model, prompt_tokens, completion_tokens),
        )

    # ── Prompt builder ───────────────────────────────────────────────────────

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
