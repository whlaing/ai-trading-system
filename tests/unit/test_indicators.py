"""Unit tests for technical indicator calculations."""
from decimal import Decimal

import pytest

from src.scanner.indicators import (
    atr,
    average_volume,
    ema,
    historical_volatility,
    momentum,
    relative_volume,
    rsi,
    sma,
)


def _prices(values: list[float]) -> list[Decimal]:
    return [Decimal(str(v)) for v in values]


class TestSMA:
    def test_basic(self):
        prices = _prices([1, 2, 3, 4, 5])
        result = sma(prices, 3)
        assert result == Decimal("4")  # avg of last 3: 3+4+5 = 4

    def test_insufficient_data(self):
        assert sma(_prices([1, 2]), 5) is None

    def test_single_value(self):
        assert sma(_prices([42.0]), 1) == Decimal("42.0")


class TestEMA:
    def test_returns_value(self):
        prices = _prices([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20])
        result = ema(prices, 5)
        assert result is not None
        assert result > Decimal("15")  # should be above the simple average of first 5

    def test_insufficient_data(self):
        assert ema(_prices([1, 2, 3]), 10) is None


class TestRSI:
    def test_oversold_level(self):
        # Consistent downward prices should produce low RSI
        prices = _prices([100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85])
        result = rsi(prices, 14)
        assert result is not None
        assert float(result) < 40  # oversold

    def test_overbought_level(self):
        # Consistent upward prices should produce high RSI
        prices = _prices([100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115])
        result = rsi(prices, 14)
        assert result is not None
        assert float(result) > 60  # overbought

    def test_range(self):
        prices = _prices(list(range(100, 120)))
        result = rsi(prices, 14)
        assert result is not None
        assert Decimal("0") <= result <= Decimal("100")

    def test_insufficient_data(self):
        assert rsi(_prices([1, 2, 3, 4, 5]), 14) is None


class TestATR:
    def test_basic_atr(self):
        highs = _prices([11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26])
        lows = _prices([9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24])
        closes = _prices([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25])
        result = atr(highs, lows, closes, 14)
        assert result is not None
        assert result > Decimal("0")

    def test_insufficient_data(self):
        highs = _prices([11, 12])
        lows = _prices([9, 10])
        closes = _prices([10, 11])
        assert atr(highs, lows, closes, 14) is None


class TestMomentum:
    def test_positive_momentum(self):
        prices = _prices([100, 101, 102, 103, 104, 105])
        result = momentum(prices, 5)
        assert result is not None
        assert result > Decimal("0")

    def test_negative_momentum(self):
        prices = _prices([105, 104, 103, 102, 101, 100])
        result = momentum(prices, 5)
        assert result is not None
        assert result < Decimal("0")

    def test_insufficient_data(self):
        assert momentum(_prices([100, 101]), 5) is None


class TestRelativeVolume:
    def test_above_average(self):
        result = relative_volume(current_volume=2_000_000, avg_volume=Decimal("1000000"))
        assert result == Decimal("2.0")

    def test_zero_avg_volume(self):
        assert relative_volume(1000, Decimal("0")) is None


class TestAverageVolume:
    def test_basic(self):
        volumes = [100_000] * 20
        result = average_volume(volumes, 20)
        assert result == Decimal("100000")

    def test_insufficient_data(self):
        assert average_volume([100_000] * 5, 20) is None
