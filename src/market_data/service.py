"""MarketDataService — single entry point for all market data needs."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from src.common.logging import get_logger
from src.common.models import Candle, MarketStatus, Quote
from src.market_data.providers.base import MarketDataProvider

log = get_logger(__name__)


class MarketDataService:
    """
    Decouples strategy/scanner code from specific data providers.
    Swap providers without touching any strategy logic.
    """

    def __init__(self, provider: MarketDataProvider) -> None:
        self._provider = provider

    def get_quote(self, symbol: str) -> Quote:
        log.debug("market_data.get_quote", symbol=symbol)
        return self._provider.get_quote(symbol)

    def get_candles(
        self,
        symbol: str,
        interval: str = "1d",
        lookback: int = 60,
        end: Optional[datetime] = None,
    ) -> list[Candle]:
        log.debug("market_data.get_candles", symbol=symbol, interval=interval, lookback=lookback)
        return self._provider.get_candles(symbol, interval, lookback, end)

    def get_market_status(self) -> MarketStatus:
        return self._provider.get_market_status()

    def calculate_spread(self, symbol: str) -> float:
        return self._provider.calculate_spread(symbol)

    def is_market_open(self) -> bool:
        return self._provider.get_market_status().is_open
