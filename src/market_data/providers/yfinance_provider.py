"""yfinance-backed market data provider — free 15-min delayed data.

No IBKR market data subscription needed. Good for SGX paper trading.
Symbols are automatically suffixed (e.g. D05 → D05.SI for SGX).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from src.common.config import get_settings
from src.common.exceptions import MarketDataError
from src.common.logging import get_logger
from src.common.models import Candle, MarketStatus, Quote
from src.market_data.providers.base import MarketDataProvider

log = get_logger(__name__)

_YF_INTERVAL_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "1d": "1d",
}


class YFinanceProvider(MarketDataProvider):
    """Market data via yfinance — 15-min delayed, no subscription required."""

    def _yf_symbol(self, symbol: str) -> str:
        suffix = get_settings().yfinance_suffix
        if suffix and not symbol.endswith(suffix):
            return symbol + suffix
        return symbol

    def get_quote(self, symbol: str) -> Quote:
        import yfinance as yf

        yf_sym = self._yf_symbol(symbol)
        ticker = yf.Ticker(yf_sym)
        info = ticker.fast_info

        try:
            last = Decimal(str(round(float(info.last_price), 6)))
        except Exception:
            raise MarketDataError(f"yfinance: no price data for {yf_sym}")

        # yfinance doesn't provide live bid/ask — use last as proxy
        bid = last
        ask = last
        try:
            volume = int(info.three_month_average_volume or 0)
        except Exception:
            volume = 0

        quote = Quote(
            symbol=symbol,
            bid=bid,
            ask=ask,
            last=last,
            volume=volume,
            timestamp=datetime.utcnow(),
        )
        log.debug("yfinance.quote", symbol=symbol, last=str(last))
        return quote

    def get_candles(
        self,
        symbol: str,
        interval: str = "1d",
        lookback: int = 60,
        end: Optional[datetime] = None,
    ) -> list[Candle]:
        import yfinance as yf

        if interval not in _YF_INTERVAL_MAP:
            raise MarketDataError(f"Unsupported interval: {interval}")

        yf_sym = self._yf_symbol(symbol)
        yf_interval = _YF_INTERVAL_MAP[interval]

        end_dt = end or datetime.utcnow()
        if interval == "1d":
            start_dt = end_dt - timedelta(days=lookback + 10)
        else:
            start_dt = end_dt - timedelta(days=max(lookback // 60, 5))

        df = yf.download(
            yf_sym,
            start=start_dt.strftime("%Y-%m-%d"),
            end=(end_dt + timedelta(days=1)).strftime("%Y-%m-%d"),
            interval=yf_interval,
            auto_adjust=True,
            progress=False,
        )

        if df.empty:
            raise MarketDataError(f"yfinance: no candle data for {yf_sym}")

        candles = []
        for ts, row in df.iterrows():
            try:
                candles.append(Candle(
                    symbol=symbol,
                    timestamp=ts.to_pydatetime().replace(tzinfo=None),
                    open=Decimal(str(round(float(row["Open"]), 6))),
                    high=Decimal(str(round(float(row["High"]), 6))),
                    low=Decimal(str(round(float(row["Low"]), 6))),
                    close=Decimal(str(round(float(row["Close"]), 6))),
                    volume=int(row["Volume"]),
                    interval=interval,
                ))
            except Exception:
                continue

        log.debug("yfinance.candles", symbol=symbol, count=len(candles))
        return candles[-lookback:]

    def get_market_status(self) -> MarketStatus:
        import pytz

        settings = get_settings()
        tz = pytz.timezone(settings.market_timezone)
        now_local = datetime.now(tz)

        open_h, open_m = map(int, settings.market_open_time.split(":"))
        close_h, close_m = map(int, settings.market_close_time.split(":"))

        if now_local.weekday() >= 5:
            days_until_monday = 7 - now_local.weekday()
            next_monday = now_local + timedelta(days=days_until_monday)
            next_open = tz.localize(datetime(next_monday.year, next_monday.month, next_monday.day, open_h, open_m))
            return MarketStatus(is_open=False, session="CLOSED",
                                next_open=next_open.astimezone(pytz.utc).replace(tzinfo=None))

        market_open = tz.localize(datetime(now_local.year, now_local.month, now_local.day, open_h, open_m))
        market_close = tz.localize(datetime(now_local.year, now_local.month, now_local.day, close_h, close_m))
        is_open = market_open <= now_local < market_close
        session = "REGULAR" if is_open else ("PRE" if now_local < market_open else "POST")

        log.info("market_status", exchange=settings.exchange,
                 local_time=now_local.strftime("%H:%M %Z"), session=session, is_open=is_open)
        return MarketStatus(
            is_open=is_open, session=session,
            next_open=market_open.astimezone(pytz.utc).replace(tzinfo=None) if not is_open else None,
            next_close=market_close.astimezone(pytz.utc).replace(tzinfo=None) if is_open else None,
        )
