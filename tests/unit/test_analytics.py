"""Unit tests for performance analytics."""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.analytics.performance import _build_equity_curve, _max_drawdown, compute_metrics
from src.common.models import Direction, TradeRecord, TradeStatus


def _make_trade(pnl: float, days_ago: int = 0, symbol: str = "AAPL") -> TradeRecord:
    now = datetime.utcnow()
    filled = now - timedelta(days=days_ago, hours=2)
    closed = now - timedelta(days=days_ago)
    return TradeRecord(
        trade_id=f"trade-{days_ago}-{pnl}",
        symbol=symbol,
        strategy="TrendMomentum",
        direction=Direction.LONG,
        signal_timestamp=filled,
        realized_pnl=Decimal(str(pnl)),
        commission=Decimal("1.00"),
        estimated_slippage=Decimal("0.50"),
        quantity=5,
        actual_entry=Decimal("100"),
        exit_price=Decimal(str(100 + pnl / 5)),
        filled_at=filled,
        closed_at=closed,
        status=TradeStatus.CLOSED,
    )


class TestComputeMetrics:
    def test_empty_trades(self):
        metrics = compute_metrics([])
        assert metrics.total_trades == 0
        assert metrics.win_rate == 0.0

    def test_all_winners(self):
        trades = [_make_trade(5.0, i) for i in range(5)]
        metrics = compute_metrics(trades)
        assert metrics.total_trades == 5
        assert metrics.win_rate == 1.0
        assert metrics.winning_trades == 5
        assert metrics.losing_trades == 0

    def test_all_losers(self):
        trades = [_make_trade(-5.0, i) for i in range(5)]
        metrics = compute_metrics(trades)
        assert metrics.win_rate == 0.0
        assert metrics.losing_trades == 5

    def test_mixed_trades(self):
        trades = [_make_trade(10.0, 4), _make_trade(-5.0, 3), _make_trade(10.0, 2), _make_trade(-5.0, 1)]
        metrics = compute_metrics(trades)
        assert metrics.win_rate == 0.5
        assert metrics.profit_factor == pytest.approx(2.0, abs=0.01)

    def test_expectancy_calculation(self):
        trades = [_make_trade(10.0, 2), _make_trade(-5.0, 1)]
        metrics = compute_metrics(trades)
        # (10 + (-5)) / 2 = 2.5
        assert metrics.expectancy_per_trade == Decimal("2.5")

    def test_total_return(self):
        trades = [_make_trade(10.0, 2), _make_trade(5.0, 1)]
        metrics = compute_metrics(trades)
        assert metrics.total_return == Decimal("15.0")

    def test_commission_totals(self):
        trades = [_make_trade(10.0, 2), _make_trade(5.0, 1)]
        metrics = compute_metrics(trades)
        assert metrics.total_commission == Decimal("2.0")  # $1 each

    def test_excludes_non_closed_trades(self):
        closed = _make_trade(10.0, 1)
        open_trade = TradeRecord(
            trade_id="open-1",
            symbol="AAPL",
            strategy="TrendMomentum",
            direction=Direction.LONG,
            signal_timestamp=datetime.utcnow(),
            status=TradeStatus.FILLED,
        )
        metrics = compute_metrics([closed, open_trade])
        assert metrics.total_trades == 1  # Only closed trade counted


class TestMaxDrawdown:
    def test_no_drawdown(self):
        equity = [Decimal("0"), Decimal("10"), Decimal("20"), Decimal("30")]
        dd, dd_pct = _max_drawdown(equity)
        assert dd == Decimal("0")
        assert dd_pct == 0.0

    def test_simple_drawdown(self):
        equity = [Decimal("0"), Decimal("100"), Decimal("50"), Decimal("80")]
        dd, dd_pct = _max_drawdown(equity)
        assert dd == Decimal("50")

    def test_empty_equity(self):
        dd, dd_pct = _max_drawdown([])
        assert dd == Decimal("0")
