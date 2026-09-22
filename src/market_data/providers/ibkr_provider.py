"""IBKR-backed market data provider using ib_insync."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from src.common.config import get_settings
from src.common.exceptions import MarketDataError, StaleDataError
from src.common.logging import get_logger
from src.common.models import Candle, MarketStatus, Quote
from src.market_data.providers.base import MarketDataProvider

if TYPE_CHECKING:
    from ib_insync import IB

log = get_logger(__name__)

_INTERVAL_MAP = {
    "1m": ("1 min", "1 min"),
    "5m": ("5 mins", "5 mins"),
    "15m": ("15 mins", "15 mins"),
    "1h": ("1 hour", "1 hour"),
    "1d": ("1 day", "1 day"),
}


class IBKRMarketDataProvider(MarketDataProvider):
    """Wraps ib_insync for live/paper market data."""

    STALE_THRESHOLD_SECONDS = 30

    def __init__(self, ib: "IB") -> None:
        self._ib = ib

    def get_quote(self, symbol: str) -> Quote:
        import ib_insync as ibi

        settings = get_settings()
        contract = ibi.Stock(symbol, settings.exchange, settings.exchange_currency)
        qualified = self._ib.qualifyContracts(contract)
        if not qualified:
            raise MarketDataError(f"IBKR could not find contract for {symbol} on {settings.exchange} — symbol may be delisted or needs a different exchange")
        ticker = self._ib.reqMktData(contract, "", False, False)

        # Allow a brief moment for data to arrive
        self._ib.sleep(1)

        if ticker.last != ticker.last:  # NaN check
            raise MarketDataError(f"No price data for {symbol}")

        bid = Decimal(str(ticker.bid)) if ticker.bid == ticker.bid else Decimal("0")
        ask = Decimal(str(ticker.ask)) if ticker.ask == ticker.ask else Decimal("0")
        last = Decimal(str(ticker.last))
        volume = int(ticker.volume) if ticker.volume == ticker.volume else 0

        self._ib.cancelMktData(contract)

        quote = Quote(
            symbol=symbol,
            bid=bid if bid > 0 else last,
            ask=ask if ask > 0 else last,
            last=last,
            volume=volume,
            timestamp=datetime.utcnow(),
        )

        if quote.age_seconds > self.STALE_THRESHOLD_SECONDS:
            raise StaleDataError(f"Quote for {symbol} is stale ({quote.age_seconds:.0f}s old)")

        log.debug("market_data.quote", symbol=symbol, last=str(last), spread_pct=str(quote.spread_pct))
        return quote

    def get_candles(
        self,
        symbol: str,
        interval: str = "1d",
        lookback: int = 60,
        end: Optional[datetime] = None,
    ) -> list[Candle]:
        import ib_insync as ibi

        if interval not in _INTERVAL_MAP:
            raise MarketDataError(f"Unsupported interval: {interval}")

        bar_size, duration_unit = _INTERVAL_MAP[interval]

        if interval == "1d":
            duration = f"{lookback} D"
        elif interval in ("1h",):
            duration = f"{max(1, lookback // 7)} W"
        else:
            duration = f"{max(1, lookback // 390)} D"

        settings = get_settings()
        contract = ibi.Stock(symbol, settings.exchange, settings.exchange_currency)
        qualified = self._ib.qualifyContracts(contract)
        if not qualified:
            raise MarketDataError(f"IBKR could not find contract for {symbol} on {settings.exchange}")

        end_dt = end.strftime("%Y%m%d %H:%M:%S") if end else ""
        bars = self._ib.reqHistoricalData(
            contract,
            endDateTime=end_dt,
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=True,
        )

        if not bars:
            raise MarketDataError(f"No candle data returned for {symbol}")

        return [
            Candle(
                symbol=symbol,
                timestamp=bar.date if isinstance(bar.date, datetime) else datetime.combine(bar.date, datetime.min.time()),
                open=Decimal(str(bar.open)),
                high=Decimal(str(bar.high)),
                low=Decimal(str(bar.low)),
                close=Decimal(str(bar.close)),
                volume=int(bar.volume),
                interval=interval,
            )
            for bar in bars
        ]

    def get_market_status(self) -> MarketStatus:
        import pytz

        settings = get_settings()
        tz = pytz.timezone(settings.market_timezone)
        now_local = datetime.now(tz)

        open_h, open_m = map(int, settings.market_open_time.split(":"))
        close_h, close_m = map(int, settings.market_close_time.split(":"))

        # Weekend check
        if now_local.weekday() >= 5:
            days_until_monday = 7 - now_local.weekday()
            next_monday = now_local + timedelta(days=days_until_monday)
            next_open = tz.localize(datetime(next_monday.year, next_monday.month, next_monday.day, open_h, open_m))
            return MarketStatus(
                is_open=False,
                session="CLOSED",
                next_open=next_open.astimezone(pytz.utc).replace(tzinfo=None),
            )

        market_open = tz.localize(datetime(now_local.year, now_local.month, now_local.day, open_h, open_m))
        market_close = tz.localize(datetime(now_local.year, now_local.month, now_local.day, close_h, close_m))

        is_open = market_open <= now_local < market_close
        session = "REGULAR" if is_open else ("PRE" if now_local < market_open else "POST")

        log.info(
            "market_status",
            exchange=settings.exchange,
            timezone=settings.market_timezone,
            local_time=now_local.strftime("%H:%M %Z"),
            session=session,
            is_open=is_open,
        )

        return MarketStatus(
            is_open=is_open,
            session=session,
            next_open=market_open.astimezone(pytz.utc).replace(tzinfo=None) if not is_open else None,
            next_close=market_close.astimezone(pytz.utc).replace(tzinfo=None) if is_open else None,
        )
