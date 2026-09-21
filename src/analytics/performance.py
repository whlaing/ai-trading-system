"""Performance analytics — computes all metrics from trade records."""
from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal
from typing import Optional

from src.common.models import PerformanceMetrics, TradeRecord, TradeStatus


def compute_metrics(
    trades: list[TradeRecord],
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> PerformanceMetrics:
    closed = [
        t for t in trades
        if t.status == TradeStatus.CLOSED and t.realized_pnl is not None
    ]

    if not closed:
        return PerformanceMetrics(
            period_start=start or datetime.utcnow(),
            period_end=end or datetime.utcnow(),
        )

    winners = [t for t in closed if t.realized_pnl > Decimal("0")]
    losers = [t for t in closed if t.realized_pnl <= Decimal("0")]

    gross_profit = sum(t.realized_pnl for t in winners)
    gross_loss = abs(sum(t.realized_pnl for t in losers))

    win_rate = len(winners) / len(closed) if closed else 0.0
    avg_winner = gross_profit / Decimal(str(len(winners))) if winners else Decimal("0")
    avg_loser = gross_loss / Decimal(str(len(losers))) if losers else Decimal("0")
    profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    expectancy = (gross_profit - gross_loss) / Decimal(str(len(closed)))

    total_return = sum(t.realized_pnl for t in closed)
    total_commission = sum(t.commission for t in closed)
    total_slippage = sum(t.estimated_slippage for t in closed)

    # Holding period in hours
    holding_periods = []
    for t in closed:
        if t.filled_at and t.closed_at:
            hours = (t.closed_at - t.filled_at).total_seconds() / 3600
            holding_periods.append(hours)
    avg_holding = sum(holding_periods) / len(holding_periods) if holding_periods else 0.0

    # Max drawdown (equity curve based on chronological closed trades)
    equity_curve = _build_equity_curve(closed)
    max_dd, max_dd_pct = _max_drawdown(equity_curve)

    # Annualized return (simple)
    first_trade = min((t.signal_timestamp for t in closed), default=datetime.utcnow())
    last_trade = max((t.closed_at for t in closed if t.closed_at), default=datetime.utcnow())
    years = max((last_trade - first_trade).days / 365, 1 / 365)

    # Sharpe ratio (daily PnL series, assume 0 risk-free rate)
    sharpe = _sharpe_ratio(closed)

    return PerformanceMetrics(
        period_start=start or first_trade,
        period_end=end or last_trade,
        total_trades=len(closed),
        winning_trades=len(winners),
        losing_trades=len(losers),
        win_rate=win_rate,
        avg_winner=avg_winner,
        avg_loser=avg_loser,
        profit_factor=profit_factor,
        expectancy_per_trade=expectancy,
        total_return=total_return,
        annualized_return=float(total_return) / years,
        max_drawdown=max_dd,
        max_drawdown_pct=max_dd_pct,
        sharpe_ratio=sharpe,
        total_commission=total_commission,
        total_slippage=total_slippage,
        avg_holding_period_hours=avg_holding,
    )


def _build_equity_curve(trades: list[TradeRecord]) -> list[Decimal]:
    sorted_trades = sorted(trades, key=lambda t: t.closed_at or datetime.utcnow())
    cumulative = Decimal("0")
    curve = [Decimal("0")]
    for t in sorted_trades:
        if t.realized_pnl:
            cumulative += t.realized_pnl
        curve.append(cumulative)
    return curve


def _max_drawdown(equity: list[Decimal]) -> tuple[Decimal, float]:
    if len(equity) < 2:
        return Decimal("0"), 0.0
    peak = equity[0]
    max_dd = Decimal("0")
    max_dd_pct = 0.0
    for val in equity:
        if val > peak:
            peak = val
        dd = peak - val
        if dd > max_dd:
            max_dd = dd
            if peak != 0:
                max_dd_pct = float(dd / abs(peak))
    return max_dd, max_dd_pct


def _sharpe_ratio(trades: list[TradeRecord]) -> float:
    if len(trades) < 2:
        return 0.0
    pnls = [float(t.realized_pnl) for t in trades if t.realized_pnl is not None]
    if len(pnls) < 2:
        return 0.0
    mean = sum(pnls) / len(pnls)
    variance = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
    std = math.sqrt(variance)
    if std == 0:
        return 0.0
    return (mean / std) * math.sqrt(252)
