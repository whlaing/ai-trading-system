"""
Backtesting Engine.

Uses the exact same strategy logic as paper/live trading.
Models realistic costs: commission, spread, slippage, daily limits.

The same Strategy.evaluate() interface is used — no separate strategy
implementations for backtesting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from typing import Optional

from src.broker.mock import MockBrokerAdapter
from src.common.config import Settings
from src.common.logging import get_logger
from src.common.models import Candle, Direction, TradeRecord, TradeStatus
from src.market_data.providers.base import MarketDataProvider
from src.risk.engine import KillSwitch, RiskEngine
from src.risk.position_sizing import calculate_position_size
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
from src.strategies.base import Strategy

log = get_logger(__name__)

_SLIPPAGE_BPS = Decimal("5")  # 5 basis points per side
_COMMISSION_PER_SHARE = Decimal("0.005")
_MIN_COMMISSION = Decimal("1.00")


@dataclass
class BacktestResult:
    symbol: str
    strategy: str
    start_date: datetime
    end_date: datetime
    initial_capital: Decimal
    final_capital: Decimal
    trades: list[TradeRecord] = field(default_factory=list)

    @property
    def total_return(self) -> Decimal:
        return self.final_capital - self.initial_capital

    @property
    def total_return_pct(self) -> float:
        if self.initial_capital == 0:
            return 0.0
        return float(self.total_return / self.initial_capital)

    @property
    def closed_trades(self) -> list[TradeRecord]:
        return [t for t in self.trades if t.status == TradeStatus.CLOSED]

    @property
    def win_rate(self) -> float:
        closed = self.closed_trades
        if not closed:
            return 0.0
        winners = [t for t in closed if t.realized_pnl and t.realized_pnl > 0]
        return len(winners) / len(closed)

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(float(t.realized_pnl) for t in self.closed_trades if t.realized_pnl and t.realized_pnl > 0)
        gross_loss = abs(sum(float(t.realized_pnl) for t in self.closed_trades if t.realized_pnl and t.realized_pnl < 0))
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return gross_profit / gross_loss

    @property
    def expectancy_per_trade(self) -> Decimal:
        closed = self.closed_trades
        if not closed:
            return Decimal("0")
        total_pnl = sum(t.realized_pnl for t in closed if t.realized_pnl is not None)
        return total_pnl / Decimal(str(len(closed)))

    @property
    def total_commission(self) -> Decimal:
        return sum(t.commission for t in self.closed_trades)

    @property
    def total_slippage(self) -> Decimal:
        return sum(t.estimated_slippage for t in self.closed_trades)


class BacktestEngine:
    """
    Simulates strategy execution against historical OHLCV data.

    Design constraints:
    - Uses the same Strategy objects as live trading
    - Models commission, slippage, spread
    - Enforces daily loss limits and max positions
    - No lookahead bias (indicators are computed only on data available at bar time)
    """

    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def run(
        self,
        strategy: Strategy,
        candles_by_symbol: dict[str, list[Candle]],
        initial_capital: Decimal = Decimal("10000"),
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> dict[str, BacktestResult]:
        results: dict[str, BacktestResult] = {}

        for symbol, candles in candles_by_symbol.items():
            result = self._run_symbol(
                strategy=strategy,
                symbol=symbol,
                candles=candles,
                initial_capital=initial_capital,
                start_date=start_date,
                end_date=end_date,
            )
            results[symbol] = result
            log.info(
                "backtest.symbol_complete",
                symbol=symbol,
                trades=len(result.closed_trades),
                win_rate=round(result.win_rate, 3),
                total_return_pct=round(result.total_return_pct, 4),
            )

        return results

    def _run_symbol(
        self,
        strategy: Strategy,
        symbol: str,
        candles: list[Candle],
        initial_capital: Decimal,
        start_date: Optional[datetime],
        end_date: Optional[datetime],
    ) -> BacktestResult:
        if not candles:
            return BacktestResult(
                symbol=symbol,
                strategy=strategy.name,
                start_date=start_date or datetime.utcnow(),
                end_date=end_date or datetime.utcnow(),
                initial_capital=initial_capital,
                final_capital=initial_capital,
            )

        broker = MockBrokerAdapter(initial_cash=initial_capital)
        broker.connect()

        kill_switch = KillSwitch(initial_state=True)
        risk_engine = RiskEngine(self._s, kill_switch)

        trades: list[TradeRecord] = []
        daily_pnl = Decimal("0")
        daily_trade_count = 0
        current_date = None
        open_trade: Optional[TradeRecord] = None

        warmup = max(self._s.ema_long + 5, 60)

        for i in range(warmup, len(candles)):
            bar = candles[i]

            if start_date and bar.timestamp < start_date:
                continue
            if end_date and bar.timestamp > end_date:
                break

            bar_date = bar.timestamp.date()

            # Reset daily counters
            if current_date != bar_date:
                current_date = bar_date
                daily_pnl = Decimal("0")
                daily_trade_count = 0
                kill_switch.enable()  # Reset intraday kill switch

            window = candles[:i + 1]
            indicators = self._compute_indicators(symbol, window)
            if indicators is None:
                continue

            # Update current price in broker
            broker.update_price(symbol, bar.close)

            # --- Exit logic ---
            if open_trade is not None:
                exit_reason = self._check_exit(open_trade, bar)
                if exit_reason:
                    fill_price = self._apply_slippage(bar.close, is_buy=False)
                    commission = max(_MIN_COMMISSION, _COMMISSION_PER_SHARE * Decimal(str(open_trade.quantity)))
                    slippage = abs(fill_price - bar.close) * Decimal(str(open_trade.quantity))
                    entry = open_trade.actual_entry or Decimal("0")
                    pnl = (fill_price - entry) * Decimal(str(open_trade.quantity)) - commission
                    daily_pnl += pnl
                    open_trade.exit_price = fill_price
                    open_trade.commission += commission
                    open_trade.estimated_slippage += slippage
                    open_trade.realized_pnl = pnl
                    open_trade.closed_at = bar.timestamp
                    open_trade.status = TradeStatus.CLOSED
                    trades.append(open_trade)
                    open_trade = None
                    continue

            # --- Entry logic ---
            if open_trade is not None:
                continue  # Already in a position

            if daily_pnl <= -float(self._s.max_daily_loss_usd):
                continue

            if daily_trade_count >= self._s.max_daily_trades:
                continue

            signal = strategy.evaluate(indicators)
            if signal is None:
                continue

            # Position sizing
            shares = calculate_position_size(
                account_value=broker.get_account().net_liquidation,
                entry_price=signal.entry_price,
                stop_price=signal.stop_loss,
                max_risk_usd=self._s.max_risk_per_trade_usd,
                max_position_value_usd=self._s.max_position_value_usd,
            )
            if shares < 1:
                continue

            fill_price = self._apply_slippage(bar.close, is_buy=True)
            commission = max(_MIN_COMMISSION, _COMMISSION_PER_SHARE * Decimal(str(shares)))
            slippage = abs(fill_price - bar.close) * Decimal(str(shares))

            import uuid
            trade_id = str(uuid.uuid4())
            open_trade = TradeRecord(
                trade_id=trade_id,
                symbol=symbol,
                strategy=strategy.name,
                direction=Direction.LONG,
                signal_timestamp=bar.timestamp,
                entry_proposal=signal.entry_price,
                actual_entry=fill_price,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                quantity=shares,
                commission=commission,
                estimated_slippage=slippage,
                filled_at=bar.timestamp,
                status=TradeStatus.FILLED,
            )
            daily_trade_count += 1

        # Force-close any open position at end of backtest
        if open_trade is not None and candles:
            last_bar = candles[-1]
            fill_price = last_bar.close
            commission = max(_MIN_COMMISSION, _COMMISSION_PER_SHARE * Decimal(str(open_trade.quantity)))
            entry = open_trade.actual_entry or Decimal("0")
            pnl = (fill_price - entry) * Decimal(str(open_trade.quantity)) - commission
            open_trade.exit_price = fill_price
            open_trade.commission += commission
            open_trade.realized_pnl = pnl
            open_trade.closed_at = last_bar.timestamp
            open_trade.status = TradeStatus.CLOSED
            trades.append(open_trade)

        final_capital = broker.get_account().net_liquidation
        actual_start = candles[warmup].timestamp if len(candles) > warmup else candles[0].timestamp
        actual_end = candles[-1].timestamp

        return BacktestResult(
            symbol=symbol,
            strategy=strategy.name,
            start_date=start_date or actual_start,
            end_date=end_date or actual_end,
            initial_capital=initial_capital,
            final_capital=final_capital,
            trades=trades,
        )

    def _apply_slippage(self, price: Decimal, is_buy: bool) -> Decimal:
        bps = _SLIPPAGE_BPS / Decimal("10000")
        if is_buy:
            return (price * (1 + bps)).quantize(Decimal("0.01"))
        else:
            return (price * (1 - bps)).quantize(Decimal("0.01"))

    def _check_exit(self, trade: TradeRecord, bar: Candle) -> Optional[str]:
        if trade.stop_loss and bar.low <= trade.stop_loss:
            return "stop_loss"
        if trade.take_profit and bar.high >= trade.take_profit:
            return "take_profit"
        return None

    def _compute_indicators(self, symbol: str, candles: list[Candle]):
        from src.common.models import TechnicalIndicators

        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        volumes = [c.volume for c in candles]

        if len(closes) < self._s.ema_long + 5:
            return None

        last = candles[-1]
        ema_short = ema(closes, self._s.ema_short)
        ema_long = ema(closes, self._s.ema_long)
        sma_20 = sma(closes, 20)
        rsi_val = rsi(closes, 14)
        atr_val = atr(highs, lows, closes, 14)
        avg_vol = average_volume(volumes, 20)
        rel_vol = relative_volume(volumes[-1], avg_vol) if avg_vol else None
        mom = momentum(closes, self._s.momentum_lookback)
        vol = historical_volatility(closes, 20)

        # Simulated spread (assume 0.01% for liquid stocks in backtest)
        spread_pct = Decimal("0.0001")

        return TechnicalIndicators(
            symbol=symbol,
            timestamp=last.timestamp,
            price=last.close,
            ema_short=ema_short,
            ema_long=ema_long,
            sma_20=sma_20,
            rsi_14=rsi_val,
            atr_14=atr_val,
            volume=last.volume,
            avg_volume_20=avg_vol,
            relative_volume=rel_vol,
            momentum_5d=mom,
            volatility_20d=vol,
            spread_pct=spread_pct,
        )
