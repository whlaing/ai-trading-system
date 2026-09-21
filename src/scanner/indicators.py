"""Technical indicator calculations (pure functions, no side effects)."""
from __future__ import annotations

import math
from decimal import Decimal
from typing import Optional

import numpy as np
import pandas as pd


def ema(prices: list[Decimal], period: int) -> Optional[Decimal]:
    if len(prices) < period:
        return None
    series = pd.Series([float(p) for p in prices])
    result = series.ewm(span=period, adjust=False).mean()
    return Decimal(str(round(result.iloc[-1], 6)))


def sma(prices: list[Decimal], period: int) -> Optional[Decimal]:
    if len(prices) < period:
        return None
    values = [float(p) for p in prices[-period:]]
    return Decimal(str(round(sum(values) / period, 6)))


def rsi(prices: list[Decimal], period: int = 14) -> Optional[Decimal]:
    if len(prices) < period + 1:
        return None
    series = pd.Series([float(p) for p in prices])
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi_series = 100 - (100 / (1 + rs))
    val = rsi_series.iloc[-1]
    if math.isnan(val):
        return None
    return Decimal(str(round(val, 2)))


def atr(
    highs: list[Decimal],
    lows: list[Decimal],
    closes: list[Decimal],
    period: int = 14,
) -> Optional[Decimal]:
    if len(closes) < period + 1:
        return None
    df = pd.DataFrame(
        {
            "high": [float(h) for h in highs],
            "low": [float(lo) for lo in lows],
            "close": [float(c) for c in closes],
        }
    )
    df["prev_close"] = df["close"].shift(1)
    df["tr"] = df[["high", "prev_close"]].max(axis=1) - df[["low", "prev_close"]].min(axis=1)
    atr_val = df["tr"].ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
    return Decimal(str(round(atr_val, 6)))


def relative_volume(current_volume: int, avg_volume: Decimal) -> Optional[Decimal]:
    if avg_volume == 0:
        return None
    return Decimal(str(round(current_volume / float(avg_volume), 4)))


def momentum(prices: list[Decimal], lookback: int = 5) -> Optional[Decimal]:
    if len(prices) < lookback + 1:
        return None
    start = float(prices[-(lookback + 1)])
    end = float(prices[-1])
    if start == 0:
        return None
    return Decimal(str(round((end - start) / start, 6)))


def historical_volatility(closes: list[Decimal], period: int = 20) -> Optional[Decimal]:
    if len(closes) < period + 1:
        return None
    series = pd.Series([float(c) for c in closes[-(period + 1):]])
    log_returns = np.log(series / series.shift(1)).dropna()
    vol = float(log_returns.std()) * math.sqrt(252)
    return Decimal(str(round(vol, 6)))


def average_volume(volumes: list[int], period: int = 20) -> Optional[Decimal]:
    if len(volumes) < period:
        return None
    return Decimal(str(round(sum(volumes[-period:]) / period, 0)))
