"""FastAPI application — performance dashboard and operational API."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.analytics.performance import compute_metrics
from src.common.config import get_settings
from src.common.models import PerformanceMetrics, TradeRecord
from src.persistence.database import get_db
from src.persistence.repositories import CostRepository, TradeRepository

app = FastAPI(
    title="ATS Dashboard API",
    description="AI-Assisted Automated Trading System — operational dashboard",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


class DashboardSummary(BaseModel):
    trading_mode: str
    trading_enabled: bool
    today_pnl: str
    total_pnl: str
    open_positions: int
    trades_today: int
    win_rate: float
    profit_factor: float
    max_drawdown: str
    total_commission: str
    ai_cost_today: str
    timestamp: str


@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/dashboard", response_model=DashboardSummary)
def dashboard():
    settings = get_settings()
    with get_db() as db:
        trade_repo = TradeRepository(db)
        cost_repo = CostRepository(db)

        trades_today = trade_repo.get_trades_today()
        open_trades = trade_repo.get_open_trades()
        all_closed = trade_repo.get_closed_trades()
        daily_costs = cost_repo.get_daily_costs()

    today_pnl = sum(
        (t.realized_pnl for t in trades_today if t.realized_pnl),
        Decimal("0"),
    )
    total_pnl = sum(
        (t.realized_pnl for t in all_closed if t.realized_pnl),
        Decimal("0"),
    )
    metrics = compute_metrics(all_closed)
    ai_cost = daily_costs.get("AI_API", Decimal("0"))

    return DashboardSummary(
        trading_mode=settings.trading_mode.value,
        trading_enabled=settings.trading_enabled,
        today_pnl=str(today_pnl.quantize(Decimal("0.01"))),
        total_pnl=str(total_pnl.quantize(Decimal("0.01"))),
        open_positions=len(open_trades),
        trades_today=len(trades_today),
        win_rate=round(metrics.win_rate, 4),
        profit_factor=round(metrics.profit_factor, 4),
        max_drawdown=str(metrics.max_drawdown.quantize(Decimal("0.01"))),
        total_commission=str(metrics.total_commission.quantize(Decimal("0.01"))),
        ai_cost_today=str(ai_cost),
        timestamp=datetime.utcnow().isoformat(),
    )


@app.get("/trades")
def list_trades(
    symbol: Optional[str] = Query(default=None),
    strategy: Optional[str] = Query(default=None),
    start: Optional[datetime] = Query(default=None),
    end: Optional[datetime] = Query(default=None),
    limit: int = Query(default=100, le=1000),
):
    with get_db() as db:
        trades = TradeRepository(db).get_closed_trades(
            start=start, end=end, symbol=symbol, strategy=strategy
        )
    return {"trades": [t.model_dump(mode="json") for t in trades[:limit]], "count": len(trades)}


@app.get("/trades/{trade_id}")
def get_trade(trade_id: str):
    with get_db() as db:
        trade = TradeRepository(db).get_by_id(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    return trade.model_dump(mode="json")


@app.get("/performance")
def performance(
    start: Optional[datetime] = Query(default=None),
    end: Optional[datetime] = Query(default=None),
    symbol: Optional[str] = Query(default=None),
    strategy: Optional[str] = Query(default=None),
):
    with get_db() as db:
        trades = TradeRepository(db).get_closed_trades(start=start, end=end, symbol=symbol, strategy=strategy)
    metrics = compute_metrics(trades, start=start, end=end)
    return metrics.model_dump(mode="json")


@app.get("/costs")
def costs(date: Optional[datetime] = Query(default=None)):
    with get_db() as db:
        daily = CostRepository(db).get_daily_costs(date)
    return {k: str(v) for k, v in daily.items()}
