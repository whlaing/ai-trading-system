"""Market scanner — reduces the full universe to a short candidate list."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from src.common.config import Settings
from src.common.logging import get_logger
from src.common.models import TechnicalIndicators
from src.market_data.service import MarketDataService
from src.scanner import indicators as ind

log = get_logger(__name__)


class MarketScanner:
    """
    Periodically evaluates the configured universe and returns candidates
    that warrant deeper strategy and AI analysis.

    The scanner deliberately does NOT call the AI; it applies only
    quantitative filters so that expensive AI calls are minimised.
    """

    def __init__(self, market_data: MarketDataService, settings: Settings) -> None:
        self._data = market_data
        self._settings = settings

    def scan(self) -> list[TechnicalIndicators]:
        """Return a ranked list of candidate symbols (up to max_candidates)."""
        candidates: list[tuple[float, TechnicalIndicators]] = []

        for symbol in self._settings.universe:
            try:
                tech = self._compute_indicators(symbol)
                if tech is None:
                    continue
                score = self._score(tech)
                if score > 0:
                    candidates.append((score, tech))
                    log.debug("scanner.candidate", symbol=symbol, score=round(score, 3))
            except Exception as exc:
                log.warning("scanner.symbol_error", symbol=symbol, error=str(exc))

        candidates.sort(key=lambda x: x[0], reverse=True)
        selected = [tech for _, tech in candidates[: self._settings.max_candidates]]
        log.info("scanner.scan_complete", total=len(self._settings.universe), candidates=len(selected))
        return selected

    def _compute_indicators(self, symbol: str) -> Optional[TechnicalIndicators]:
        candles = self._data.get_candles(symbol, interval="1d", lookback=60)
        if len(candles) < 52:
            log.warning("scanner.insufficient_candles", symbol=symbol, count=len(candles))
            return None

        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        volumes = [c.volume for c in candles]

        quote = self._data.get_quote(symbol)

        ema_short = ind.ema(closes, self._settings.ema_short)
        ema_long = ind.ema(closes, self._settings.ema_long)
        sma_20 = ind.sma(closes, 20)
        rsi_val = ind.rsi(closes, 14)
        atr_val = ind.atr(highs, lows, closes, 14)
        avg_vol = ind.average_volume(volumes, 20)
        rel_vol = ind.relative_volume(quote.volume, avg_vol) if avg_vol else None
        mom = ind.momentum(closes, self._settings.momentum_lookback)
        vol = ind.historical_volatility(closes, 20)

        return TechnicalIndicators(
            symbol=symbol,
            timestamp=datetime.utcnow(),
            price=quote.last,
            ema_short=ema_short,
            ema_long=ema_long,
            sma_20=sma_20,
            rsi_14=rsi_val,
            atr_14=atr_val,
            volume=quote.volume,
            avg_volume_20=avg_vol,
            relative_volume=rel_vol,
            momentum_5d=mom,
            volatility_20d=vol,
            spread_pct=quote.spread_pct,
        )

    def _score(self, tech: TechnicalIndicators) -> float:
        """
        Returns a positive score only when minimum quality thresholds are met.
        Higher score = better candidate quality.
        """
        if tech.ema_short is None or tech.ema_long is None:
            return 0.0
        if tech.rsi_14 is None:
            return 0.0
        if tech.relative_volume is None:
            return 0.0
        if tech.spread_pct is None:
            return 0.0
        if tech.atr_14 is None:
            return 0.0

        # Trend check
        if not tech.is_bullish_trend:
            return 0.0

        # Spread filter (eliminates wide spreads)
        if tech.spread_pct > self._settings.max_spread_pct:
            return 0.0

        # Minimum volume
        if tech.volume < self._settings.min_volume:
            return 0.0

        # Minimum relative volume
        if tech.relative_volume < self._settings.relative_volume_min:
            return 0.0

        # RSI filter
        rsi = float(tech.rsi_14)
        if not (self._settings.rsi_min <= rsi <= self._settings.rsi_max):
            return 0.0

        # ATR as % of price – reject extremely volatile
        atr_pct = float(tech.atr_14) / float(tech.price)
        if Decimal(str(atr_pct)) > self._settings.atr_max_pct:
            return 0.0

        # Score components (all positive = candidate passes)
        trend_score = float(tech.ema_short - tech.ema_long) / float(tech.ema_long)
        relvol_score = float(tech.relative_volume)
        momentum_score = float(tech.momentum_5d) if tech.momentum_5d else 0.0

        return trend_score + relvol_score * 0.5 + momentum_score * 10
