"""Strategy interface — all strategies must implement this contract."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from src.common.models import TechnicalIndicators, TradeSignal


class Strategy(ABC):
    """
    Every strategy receives a TechnicalIndicators snapshot and returns
    either a TradeSignal or None if no trade is warranted.

    The same strategy instance is used in backtesting and live/paper
    trading — no separate implementations.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique human-readable strategy identifier."""

    @abstractmethod
    def evaluate(self, indicators: TechnicalIndicators) -> Optional[TradeSignal]:
        """
        Evaluate the current market snapshot and return a signal if conditions
        are met, or None if no trade should be proposed.
        """
