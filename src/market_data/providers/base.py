"""Abstract market data provider interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from src.common.models import Candle, MarketStatus, Quote


class MarketDataProvider(ABC):
    """All market data implementations must satisfy this contract."""

    @abstractmethod
    def get_quote(self, symbol: str) -> Quote:
        """Return the current best bid/ask/last for a symbol."""

    @abstractmethod
    def get_candles(
        self,
        symbol: str,
        interval: str,
        lookback: int,
        end: Optional[datetime] = None,
    ) -> list[Candle]:
        """Return OHLCV candles. interval: '1m', '5m', '1h', '1d', etc."""

    @abstractmethod
    def get_market_status(self) -> MarketStatus:
        """Return whether the US equity market is currently open."""

    def calculate_spread(self, symbol: str) -> float:
        quote = self.get_quote(symbol)
        return float(quote.spread_pct)
