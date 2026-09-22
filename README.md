# AI-Assisted Automated Trading System (ATS) — MVP

An automated, low-risk trading research and execution platform integrated with Interactive Brokers (IBKR).

## Architecture

```
Market Data
→ Market Scanner (quantitative filters)
→ Quantitative Strategy (TradeSignal)
→ AI Analysis Agent (advisory opinion)
→ Trade Proposal
→ Deterministic Risk Engine (FINAL authority)
→ Execution Engine
→ IBKR API
→ Position Monitor
→ Performance Analytics
```

**The Risk Engine has final authority. AI cannot override it.**

## Development Phases

| Phase | Description |
|-------|-------------|
| 1 | Backtesting |
| 2 | IBKR Paper Trading |
| 3 | Small live capital |
| 4 | Controlled scaling |

## Project Structure

```
src/
├── broker/          # BrokerAdapter: IBKR, Mock, Paper
├── market_data/     # MarketDataService + providers
├── scanner/         # Universe scanner + technical indicators
├── strategies/      # Strategy interface + TrendMomentum
├── ai/              # AI Analysis Agent (Anthropic Claude)
├── risk/            # Deterministic Risk Engine + position sizing
├── execution/       # Order lifecycle engine
├── portfolio/       # Position monitor + reconciliation
├── backtest/        # Backtesting engine (same strategy code)
├── analytics/       # Performance metrics
├── notifications/   # AWS SNS alerts
├── persistence/     # SQLAlchemy models + repositories
├── api/             # FastAPI dashboard
└── common/          # Config, models, logging, exceptions

tests/
├── unit/            # Unit tests for all critical components
├── integration/
└── simulation/

infra/
├── terraform/       # AWS infrastructure as code
└── docker/          # Dockerfile
```

## Quick Start

### 1. Prerequisites

- Python 3.12+
- PostgreSQL 16
- IBKR TWS or IB Gateway (paper trading account)
- Docker (optional)

### 2. Setup

```bash
# Clone and install
pip install -e ".[dev]"

# Configure
cp .env.example .env
# Edit .env with your IBKR connection details and API keys

# Start database
docker compose up postgres -d

# Run tests
pytest tests/unit/ -v
```

### 3. Running (Paper Trading)

```bash
# Ensure IBKR TWS/Gateway is running with paper trading account
# API connections must be enabled in TWS

export TRADING_MODE=PAPER
python -m src.main
```

To discover the trading universe dynamically, run the broad scanner before
starting the trader:

```bash
python scripts/scan.py --universe sp500 --top 20
```

This writes the ranked candidates and their indicator values to
`data/scan_candidates.json`. Set the following in `.env` so the trader loads
the saved symbols instead of the `UNIVERSE` list:

```dotenv
UNIVERSE_SOURCE=scan_file
SCAN_RESULTS_FILE=data/scan_candidates.json
MAX_CANDIDATES=10
```

The trader loads all saved symbols as its scan universe, then
`MAX_CANDIDATES` controls how many passing symbols are sent to the strategy on
each runtime scan. Re-run `scripts/scan.py` to refresh the saved universe.

### 4. Dashboard

```bash
uvicorn src.api.app:app --reload
# Open http://localhost:8000/dashboard
```

### 5. Backtesting

```python
from src.backtest.engine import BacktestEngine
from src.strategies.trend_momentum import TrendMomentumStrategy
from src.common.config import get_settings

settings = get_settings()
engine = BacktestEngine(settings)
strategy = TrendMomentumStrategy(settings)

# Load candles from IBKR or CSV
results = engine.run(strategy, candles_by_symbol={"AAPL": candles})
```

## Risk Parameters (Paper defaults)

| Parameter | Default |
|-----------|---------|
| Max open positions | 3 |
| Max position value | $500 |
| Max risk per trade | $5 |
| Max daily loss | $25 |
| Max daily trades | 10 |

All values are configurable via environment variables. See `.env.example`.

## Initial Strategy: Trend + Momentum + Volume

Entry conditions (all required):
1. Price > EMA(20) > EMA(50) — bullish trend alignment
2. RSI(14) in [40, 70] — not overbought/oversold
3. Relative volume ≥ 1.5× average — participation
4. Spread ≤ 0.5% — liquidity
5. Positive 5-day momentum — direction confirmation
6. ATR ≤ 3% of price — volatility tolerance

Stop loss: entry − 1.5 × ATR(14)
Take profit: entry + 2 × stop distance (2:1 risk/reward)

## Safety Features

- **Kill switch**: `TRADING_ENABLED=false` stops new trades instantly
- **Auto-disable** on: daily loss limit, IBKR disconnect, stale data, reconciliation failure
- **AI failures** never block existing position protection
- **LIVE mode** requires explicit `TRADING_MODE=LIVE` configuration
- Every financial calculation has automated unit tests

## Research Question

> Does this strategy have positive net expectancy after all realistic costs?

Success = answering this question reliably, even if the answer is "no".

## Testing

```bash
# Unit tests (no IBKR or database required)
pytest tests/unit/ -v --cov=src

# With coverage report
pytest tests/unit/ --cov=src --cov-report=html
```

## Infrastructure (AWS)

```bash
cd infra/terraform
terraform init
terraform plan -var="environment=dev"
terraform apply
```

Provisions: VPC, RDS PostgreSQL, ECS cluster, SNS alerts, S3, CloudWatch.

## Environment Variables

See `.env.example` for the full list. Never commit `.env` to version control.

Secrets (IBKR credentials, API keys) must be stored in AWS Secrets Manager in production.

## Milestones

- [x] **M1**: Project structure, config, database, logging, tests
- [x] **M2**: Market data, IBKR connectivity
- [x] **M3**: Scanner, indicators, strategy engine
- [x] **M4**: Backtesting engine
- [x] **M5**: Risk engine, position sizing, kill switch
- [x] **M6**: Paper order execution, order lifecycle, position monitoring
- [x] **M7**: AI analysis layer
- [x] **M8**: Dashboard, alerts, performance analytics
- [x] **M9**: AWS deployment, monitoring, security
- [ ] **M10**: Long-running paper trading experiment (500+ trades)
