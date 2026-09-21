# AI-Assisted Automated Trading System — MVP

## 1. Objective

Build an automated, low-risk trading research and execution platform integrated with Interactive Brokers (IBKR).

The system will initially operate exclusively using an IBKR Paper Trading account.

Development progression:

Phase 1 — Backtesting  
Phase 2 — IBKR Paper Trading  
Phase 3 — Small live capital  
Phase 4 — Controlled scaling

The primary objective is NOT to maximize trading frequency.

The objective is to determine whether a repeatable trading strategy can generate positive risk-adjusted returns after:

- commissions
- bid/ask spread
- slippage
- market-data costs
- infrastructure costs
- AI/API costs

The system must prioritize capital protection over profit.

---

# 2. Core Design Principle

AI must NOT have unrestricted authority to execute trades.

Architecture:

Market Data
→ Market Scanner
→ Quantitative Strategy
→ AI Analysis
→ Trade Proposal
→ Deterministic Risk Engine
→ Execution Engine
→ IBKR
→ Position Monitor
→ Performance Analytics

The deterministic Risk Engine has final authority.

AI recommendations cannot override risk controls.

---

# 3. Technology Stack

Backend:

Python 3.12+

Suggested libraries:

- pandas
- numpy
- pydantic
- SQLAlchemy
- boto3
- pytest
- IBKR official API or an appropriate maintained Python wrapper

Infrastructure:

AWS

Initial services:

- ECS/Fargate or EC2 for persistent IBKR connection
- Lambda for scheduled/background jobs where appropriate
- EventBridge
- SQS
- PostgreSQL
- S3
- CloudWatch
- SNS

Do NOT introduce Kubernetes, Aurora clusters, Kafka, Redis or other expensive infrastructure unless there is a demonstrated requirement.

Infrastructure must be deployable using Infrastructure as Code.

---

# 4. Environment Separation

Support three execution modes:

BACKTEST

PAPER

LIVE

Configuration example:

TRADING_MODE=PAPER

LIVE mode must be disabled by default.

Switching to LIVE must require explicit configuration.

Paper and live credentials/configuration must be separated.

---

# 5. Market Universe

MVP should trade only highly liquid US-listed stocks and ETFs.

Initial universe should be configurable.

Example:

SPY
QQQ
AAPL
MSFT
AMZN
GOOGL
META
NVDA

Do not trade:

- options
- futures
- crypto
- leveraged ETFs
- penny stocks
- illiquid securities

during MVP.

---

# 6. Market Data Service

Create a MarketDataService abstraction.

Responsibilities:

- obtain current price
- bid
- ask
- volume
- OHLCV candles
- historical candles
- market status
- calculate spread

Interface example:

get_quote(symbol)

get_candles(symbol, interval, lookback)

get_market_status()

Market data providers must be replaceable without changing strategy code.

---

# 7. Market Scanner

Create a scanner that periodically evaluates the configured universe.

Initial scan frequency:

Every 1–5 minutes during US market hours.

Calculate indicators such as:

- price change
- volume
- relative volume
- EMA
- SMA
- RSI
- ATR
- volatility
- momentum
- bid/ask spread

The scanner should reduce the full universe to a small list of candidate opportunities.

Example:

100 symbols
→ quantitative filters
→ 3–10 candidates
→ deeper analysis

Do NOT send every market-data update to an LLM.

---

# 8. Strategy Interface

Strategies must implement a common interface.

Example:

Strategy.evaluate(market_data) → TradeSignal

TradeSignal:

symbol
direction
entry_price
stop_loss
take_profit
confidence
strategy_name
reason
timestamp

Initial direction:

LONG only.

SHORT selling should not be included in MVP.

Multiple strategies must eventually be supported.

---

# 9. Initial Strategy

Implement a simple strategy first rather than attempting AI prediction.

Example strategy:

Trend + Momentum + Volume.

Potential conditions:

Price > EMA20

EMA20 > EMA50

RSI within configurable range

Relative volume above threshold

Spread below threshold

Positive short-term momentum

ATR within acceptable range

All thresholds must be configuration values rather than hard-coded values.

The purpose of the first strategy is primarily to validate the platform.

---

# 10. AI Analysis Agent

The AI layer receives only candidates that already passed quantitative filters.

Input should contain structured data:

symbol
price
technical indicators
volume
volatility
spread
recent movement
strategy signal

Optional later inputs:

news
earnings events
market regime
sector performance

AI output MUST use structured JSON.

Example:

{
  "decision": "APPROVE",
  "confidence": 0.72,
  "risk_flags": [],
  "reason": "...",
  "suggested_action": "LONG"
}

Allowed decisions:

APPROVE
REJECT
UNCERTAIN

AI must NEVER directly call the broker execution API.

The output becomes another input to the Risk Engine.

All AI prompts and responses must be logged for later analysis.

---

# 11. Deterministic Risk Engine

This is the most important component.

Implement:

RiskEngine.validate(trade_proposal, portfolio_state)

Possible checks:

MAX_POSITION_VALUE

MAX_RISK_PER_TRADE

MAX_DAILY_LOSS

MAX_DAILY_TRADES

MAX_OPEN_POSITIONS

MAX_SYMBOL_EXPOSURE

MAX_TOTAL_EXPOSURE

MAX_SPREAD

MIN_LIQUIDITY

MARKET_HOURS_ONLY

NO_DUPLICATE_POSITION

COOLDOWN_AFTER_LOSS

Example initial PAPER configuration:

max_positions = 3

max_position_value = 500 SGD equivalent

max_risk_per_trade = 5 SGD

max_daily_loss = 25 SGD

max_daily_trades = 10

Risk calculations should be based on account currency and support USD instruments.

If ANY mandatory risk rule fails:

REJECT TRADE.

AI cannot override this decision.

---

# 12. Position Sizing

Position size must be calculated from risk.

Example:

maximum risk = $5

entry = $100

stop = $99

risk/share = $1

maximum shares = 5

Do NOT simply allocate a fixed dollar amount without considering stop-loss distance.

Implement:

calculate_position_size(
    account_value,
    entry_price,
    stop_price,
    max_risk
)

Round according to instrument requirements.

---

# 13. IBKR Execution Adapter

Create an abstraction:

BrokerAdapter

Methods:

connect()

disconnect()

get_account()

get_positions()

get_open_orders()

submit_order()

cancel_order()

get_order_status()

get_executions()

IBKRAdapter implements BrokerAdapter.

This separation must allow creation of:

MockBrokerAdapter

PaperBrokerAdapter

LiveBrokerAdapter

Strategy code must never communicate directly with IBKR.

---

# 14. Order Lifecycle

Every trade must have an internal unique trade ID.

Lifecycle:

PROPOSED

AI_ANALYSED

RISK_APPROVED

ORDER_SUBMITTED

ORDER_ACKNOWLEDGED

PARTIALLY_FILLED

FILLED

EXIT_PENDING

CLOSED

REJECTED

CANCELLED

ERROR

Persist every state transition.

Never assume an order is filled simply because it was submitted.

Reconcile internal state against IBKR.

---

# 15. Entry Orders

Initially support:

LIMIT orders.

Avoid uncontrolled MARKET orders in the MVP.

Before submission verify:

- current spread
- current quote freshness
- available buying power
- position size
- risk limits
- duplicate orders
- market status

---

# 16. Exit Management

Every position must have an exit plan.

Support:

stop loss

take profit

time-based exit

strategy invalidation

emergency liquidation

Where supported and appropriate, use broker-side protective orders so protection does not depend entirely on the AWS service remaining online.

---

# 17. Kill Switch

Implement a global kill switch.

TRADING_ENABLED=false

When disabled:

No new positions can be opened.

Existing positions continue to be monitored.

Also automatically disable new trading when:

daily loss >= configured limit

IBKR connection becomes unstable

market data becomes stale

unexpected account state occurs

repeated execution errors occur

internal reconciliation fails

The system should alert the operator.

---

# 18. Trade Database

Store at minimum:

trade_id

symbol

strategy

signal_timestamp

AI decision

AI confidence

AI reason

entry proposal

actual entry

quantity

stop loss

take profit

exit price

commission

estimated slippage

realized P/L

maximum favorable excursion

maximum adverse excursion

execution timestamps

order IDs

trade status

rejection reason

---

# 19. Decision Audit Log

For every potential trade store the complete decision path.

Example:

09:31 scanner detected NVDA

09:31:02 quantitative strategy passed

09:31:03 AI APPROVE 0.76

09:31:03 risk engine APPROVED

09:31:04 limit order submitted

09:31:05 IBKR acknowledged

09:31:07 filled @ 182.41

10:04 stop adjusted

10:22 position closed @ 183.20

P/L +X

This is required for debugging and strategy research.

---

# 20. Backtesting Engine

The exact same strategy logic used in paper trading should be reusable in backtesting.

Avoid creating separate implementations of strategy rules.

Backtest should model:

commission

spread

slippage

position sizing

stop losses

take profits

daily loss limits

trading hours

capital constraints

Generate:

total return

annualized return

win rate

loss rate

average winner

average loser

profit factor

expectancy/trade

maximum drawdown

Sharpe ratio

number of trades

average holding period

commission cost

slippage cost

---

# 21. Paper Trading

Paper mode must use IBKR Paper Trading.

Run the system exactly as it would operate with real money.

Paper trading should record:

signal time

order submission time

fill time

fill price

expected price

difference/slippage

commission estimate

P/L

AI/API cost

cloud cost allocation

The goal is to calculate REALISTIC NET EXPECTANCY.

---

# 22. Performance Dashboard

Create a simple web dashboard/API.

Display:

Account value

Today's P/L

Total P/L

Open positions

Trades today

Win rate

Average win

Average loss

Profit factor

Maximum drawdown

Current exposure

AI cost

Estimated commissions

Strategy performance

Allow filtering by:

date

symbol

strategy

AI decision

market condition

---

# 23. Notifications

Send alerts for important events.

Examples:

Trade opened

Trade closed

Stop loss triggered

Daily loss limit reached

Trading disabled

IBKR disconnected

Unexpected position detected

System error

Do NOT send alerts for every market scan.

---

# 24. Observability

Use structured logging.

Every log should contain where relevant:

timestamp

trade_id

symbol

strategy

component

event

severity

correlation_id

CloudWatch alarms should monitor:

application errors

IBKR connection

failed orders

stale market data

risk-engine rejection spikes

service health

---

# 25. Security

Never place brokerage credentials in source code.

Use:

AWS Secrets Manager / secure parameter storage

IAM least privilege

encrypted storage

TLS

separate development and production configuration

Never log:

passwords

tokens

session credentials

API secrets

---

# 26. Testing Requirements

Unit tests are mandatory for:

risk calculations

position sizing

PnL calculations

strategy signals

daily loss calculations

order state machine

duplicate order prevention

kill switch

Test dangerous edge cases.

Examples:

IBKR disconnects after order submission

order partially fills

duplicate event arrives

price suddenly gaps

AI API times out

AI returns invalid JSON

market data is stale

AWS service restarts

database temporarily unavailable

position exists in IBKR but not database

database shows position but IBKR does not

The safest behavior should always be selected.

---

# 27. AI Failure Behaviour

AI must NOT be a required dependency for maintaining existing positions.

If AI becomes unavailable:

No new AI-dependent trades.

Existing positions remain protected and monitored.

If AI returns malformed output:

Reject the trade.

Never attempt to infer what malformed AI output intended.

---

# 28. Cost Tracking

Track operational costs separately from trading P/L.

Daily estimates:

IBKR commission

market data

AI tokens/API

AWS infrastructure

Other APIs

Calculate:

Gross Trading P/L

- Commission
- Estimated Slippage
- Data Costs
- AI Costs
- Infrastructure Costs
---------------------------
Net System P/L

This metric determines whether the system is economically viable.

---

# 29. Phase 1 Success Criteria

Do NOT enable live trading merely because paper trading is profitable for a few days.

Before considering small live capital, evaluate a statistically meaningful sample.

Suggested research target:

500+ paper trades

and multiple market conditions.

Evaluate:

positive expectancy

acceptable drawdown

profit factor

win/loss distribution

commission sensitivity

slippage sensitivity

performance consistency

AI contribution

Compare:

Strategy WITHOUT AI

versus

Strategy WITH AI

This determines whether AI actually adds value.

---

# 30. Development Milestones

Milestone 1:

Project structure
configuration
database
logging
unit-test framework

Milestone 2:

Market data
IBKR connectivity
paper account information
positions
quotes

Milestone 3:

Scanner
technical indicators
strategy engine

Milestone 4:

Backtesting engine

Milestone 5:

Risk engine
position sizing
kill switch

Milestone 6:

Paper order execution
order lifecycle
position monitoring

Milestone 7:

AI analysis layer

Milestone 8:

Dashboard
alerts
performance analytics

Milestone 9:

AWS deployment
monitoring
security

Milestone 10:

Long-running paper-trading experiment

---

# 31. Coding Standards

Use modular architecture.

Suggested structure:

src/
    broker/
    market_data/
    scanner/
    strategies/
    ai/
    risk/
    execution/
    portfolio/
    backtest/
    analytics/
    notifications/
    persistence/
    api/
    common/

tests/
    unit/
    integration/
    simulation/

infra/

config/

No trading logic should be embedded directly inside API handlers.

Use type hints.

Use Pydantic models for major domain objects.

Use dependency injection where practical.

Every financial calculation should have automated tests.

---

# 32. Important Engineering Rule

Do not optimize for sophistication.

Optimize for:

correctness

safety

testability

observability

reproducibility

cost efficiency

The first version should be boring, deterministic and measurable.

Agentic AI should be introduced only where its contribution can be independently measured.

---

# 33. Final Research Question

The system exists to answer:

"Does this strategy have positive net expectancy after all realistic costs and risks?"

The software is successful even if the answer is "no", because it prevents scaling an unprofitable strategy with real capital.