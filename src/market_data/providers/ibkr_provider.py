"""IBKR-backed market data provider using ib_insync."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

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

        contract = ibi.Stock(symbol, "SMART", "USD")
        self._ib.qualifyContracts(contract)
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

        contract = ibi.Stock(symbol, "SMART", "USD")
        self._ib.qualifyContracts(contract)

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
        now = datetime.utcnow()
        # NYSE regular session 14:30–21:00 UTC
        market_open = now.replace(hour=14, minute=30, second=0, microsecond=0)
        market_close = now.replace(hour=21, minute=0, second=0, microsecond=0)

        # Weekend check
        if now.weekday() >= 5:
            next_monday = now + timedelta(days=(7 - now.weekday()))
            return MarketStatus(
                is_open=False,
                session="CLOSED",
                next_open=next_monday.replace(hour=14, minute=30),
            )

        is_open = market_open <= now < market_close
        session = "REGULAR" if is_open else ("PRE" if now < market_open else "POST")

        return MarketStatus(
            is_open=is_open,
            session=session,
            next_open=market_open if not is_open else None,
            next_close=market_close if is_open else None,
        )
