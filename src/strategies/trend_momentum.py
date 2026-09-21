"""
Trend + Momentum + Volume strategy.

Entry conditions (all must be true):
  1. Price > EMA_SHORT
  2. EMA_SHORT > EMA_LONG  (bullish trend alignment)
  3. RSI in [rsi_min, rsi_max]  (not overbought/oversold)
  4. Relative volume >= threshold  (participation)
  5. Spread% <= max_spread  (liquidity)
  6. Positive short-term momentum  (direction confirmation)
  7. ATR% <= max_atr  (volatility within tolerance)

Stop loss: ATR-based (entry - atr_multiplier * ATR)
Take profit: risk/reward multiple of stop distance
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from src.common.config import Settings
from src.common.logging import get_logger
from src.common.models import Direction, TechnicalIndicators, TradeSignal
from src.strategies.base import Strategy

log = get_logger(__name__)

_ATR_STOP_MULTIPLIER = Decimal("1.5")
_RISK_REWARD_RATIO = Decimal("2.0")


class TrendMomentumStrategy(Strategy):
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    @property
    def name(self) -> str:
        return "TrendMomentum"

    def evaluate(self, indicators: TechnicalIndicators) -> Optional[TradeSignal]:
        reasons: list[str] = []
        fails: list[str] = []

        t = indicators

        # Guard: all required indicators must be present
        if any(
            v is None
            for v in [t.ema_short, t.ema_long, t.rsi_14, t.relative_volume, t.spread_pct, t.atr_14]
        ):
            return None

        # 1. Trend alignment
        if t.price > t.ema_short > t.ema_long:
            reasons.append("bullish trend (price>EMA20>EMA50)")
        else:
            fails.append("trend not aligned")

        # 2. RSI in range
        rsi_val = float(t.rsi_14)
        if self._s.rsi_min <= rsi_val <= self._s.rsi_max:
            reasons.append(f"RSI {rsi_val:.1f} in range")
        else:
            fails.append(f"RSI {rsi_val:.1f} out of range [{self._s.rsi_min},{self._s.rsi_max}]")

        # 3. Relative volume
        rel_vol = float(t.relative_volume)
        if rel_vol >= float(self._s.relative_volume_min):
            reasons.append(f"relative volume {rel_vol:.1f}x")
        else:
            fails.append(f"relative volume {rel_vol:.1f}x < {self._s.relative_volume_min}")

        # 4. Spread
        if t.spread_pct <= self._s.max_spread_pct:
            reasons.append("spread within limits")
        else:
            fails.append(f"spread {float(t.spread_pct):.4f} > limit")

        # 5. Momentum
        if t.momentum_5d is not None and t.momentum_5d > Decimal("0"):
            reasons.append(f"positive momentum {float(t.momentum_5d):.3%}")
        else:
            fails.append("no positive momentum")

        # 6. ATR check
        atr_pct = float(t.atr_14) / float(t.price)
        if Decimal(str(atr_pct)) <= self._s.atr_max_pct:
            reasons.append(f"ATR {atr_pct:.2%} within tolerance")
        else:
            fails.append(f"ATR {atr_pct:.2%} too high")

        if fails:
            log.debug(
                "strategy.no_signal",
                symbol=t.symbol,
                strategy=self.name,
                fails=fails,
            )
            return None

        # Calculate stop and target from ATR
        stop_loss = t.price - _ATR_STOP_MULTIPLIER * t.atr_14
        stop_distance = t.price - stop_loss
        take_profit = t.price + _RISK_REWARD_RATIO * stop_distance

        # Confidence is a simple proxy based on how many conditions are met
        confidence = len(reasons) / 6.0

        signal = TradeSignal(
            symbol=t.symbol,
            direction=Direction.LONG,
            entry_price=t.price,
            stop_loss=stop_loss.quantize(Decimal("0.01")),
            take_profit=take_profit.quantize(Decimal("0.01")),
            confidence=min(confidence, 1.0),
            strategy_name=self.name,
            reason="; ".join(reasons),
            timestamp=datetime.utcnow(),
            indicators=t,
        )

        log.info(
            "strategy.signal",
            symbol=t.symbol,
            strategy=self.name,
            direction=signal.direction,
            confidence=round(confidence, 3),
            entry=str(signal.entry_price),
            stop=str(signal.stop_loss),
            target=str(signal.take_profit),
        )
        return signal
