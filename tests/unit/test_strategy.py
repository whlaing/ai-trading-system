"""Unit tests for the TrendMomentum strategy."""
from datetime import datetime
from decimal import Decimal

import pytest

from src.common.config import Settings
from src.common.models import Direction, TechnicalIndicators
from src.strategies.trend_momentum import TrendMomentumStrategy


def _make_settings() -> Settings:
    return Settings(
        trading_mode="PAPER",
        ema_short=20,
        ema_long=50,
        rsi_min=40,
        rsi_max=70,
        relative_volume_min=Decimal("1.5"),
        max_spread_pct=Decimal("0.005"),
        atr_max_pct=Decimal("0.03"),
        momentum_lookback=5,
    )


def _make_indicators(**overrides) -> TechnicalIndicators:
    defaults = dict(
        symbol="AAPL",
        timestamp=datetime.utcnow(),
        price=Decimal("150"),
        ema_short=Decimal("148"),  # price > ema_short
        ema_long=Decimal("145"),   # ema_short > ema_long (bullish)
        rsi_14=Decimal("55"),      # within [40, 70]
        atr_14=Decimal("2.00"),    # 2/150 = 1.3% ATR, below 3%
        volume=1_000_000,
        avg_volume_20=Decimal("500000"),
        relative_volume=Decimal("2.0"),  # above 1.5 threshold
        momentum_5d=Decimal("0.02"),     # positive momentum
        spread_pct=Decimal("0.001"),     # tight spread
    )
    defaults.update(overrides)
    return TechnicalIndicators(**defaults)


class TestTrendMomentumStrategy:
    def setup_method(self):
        self.settings = _make_settings()
        self.strategy = TrendMomentumStrategy(self.settings)

    def test_signal_when_all_conditions_met(self):
        indicators = _make_indicators()
        signal = self.strategy.evaluate(indicators)
        assert signal is not None
        assert signal.direction == Direction.LONG
        assert signal.stop_loss < signal.entry_price
        assert signal.take_profit > signal.entry_price

    def test_no_signal_when_trend_not_aligned(self):
        # Price below EMA short
        indicators = _make_indicators(price=Decimal("140"), ema_short=Decimal("148"))
        signal = self.strategy.evaluate(indicators)
        assert signal is None

    def test_no_signal_when_ema_short_below_ema_long(self):
        indicators = _make_indicators(ema_short=Decimal("143"), ema_long=Decimal("145"))
        signal = self.strategy.evaluate(indicators)
        assert signal is None

    def test_no_signal_when_rsi_overbought(self):
        indicators = _make_indicators(rsi_14=Decimal("75"))  # above max 70
        signal = self.strategy.evaluate(indicators)
        assert signal is None

    def test_no_signal_when_rsi_oversold(self):
        indicators = _make_indicators(rsi_14=Decimal("35"))  # below min 40
        signal = self.strategy.evaluate(indicators)
        assert signal is None

    def test_no_signal_when_low_relative_volume(self):
        indicators = _make_indicators(relative_volume=Decimal("1.0"))  # below 1.5
        signal = self.strategy.evaluate(indicators)
        assert signal is None

    def test_no_signal_when_spread_too_wide(self):
        indicators = _make_indicators(spread_pct=Decimal("0.01"))  # 1%, above 0.5% limit
        signal = self.strategy.evaluate(indicators)
        assert signal is None

    def test_no_signal_when_negative_momentum(self):
        indicators = _make_indicators(momentum_5d=Decimal("-0.01"))
        signal = self.strategy.evaluate(indicators)
        assert signal is None

    def test_no_signal_when_high_volatility(self):
        # ATR = $6 on $150 stock = 4%, above 3% limit
        indicators = _make_indicators(atr_14=Decimal("6.0"))
        signal = self.strategy.evaluate(indicators)
        assert signal is None

    def test_stop_is_atr_based(self):
        indicators = _make_indicators(atr_14=Decimal("2.0"))  # ATR = $2
        signal = self.strategy.evaluate(indicators)
        assert signal is not None
        # stop = entry - 1.5 * ATR = 150 - 3 = 147
        assert signal.stop_loss == Decimal("147.00")

    def test_take_profit_is_2x_stop_distance(self):
        indicators = _make_indicators(atr_14=Decimal("2.0"))
        signal = self.strategy.evaluate(indicators)
        assert signal is not None
        stop_dist = signal.entry_price - signal.stop_loss
        tp_dist = signal.take_profit - signal.entry_price
        assert tp_dist == 2 * stop_dist

    def test_returns_none_when_missing_indicators(self):
        indicators = _make_indicators(rsi_14=None)
        signal = self.strategy.evaluate(indicators)
        assert signal is None

    def test_signal_symbol_matches(self):
        indicators = _make_indicators(symbol="MSFT")
        signal = self.strategy.evaluate(indicators)
        if signal:
            assert signal.symbol == "MSFT"
