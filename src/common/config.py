from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(str, Enum):
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    LIVE = "LIVE"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Runtime Mode ---
    trading_mode: TradingMode = TradingMode.PAPER
    trading_enabled: bool = True

    # --- IBKR Connection ---
    ibkr_host: str = "127.0.0.1"
    ibkr_port_paper: int = 7497
    ibkr_port_live: int = 7496
    ibkr_client_id: int = 1
    ibkr_timeout: int = 30

    # --- Database ---
    database_url: str = "postgresql+psycopg2://ats:ats@localhost:5432/ats"
    database_pool_size: int = 5
    database_echo: bool = False

    # --- AWS ---
    aws_region: str = "ap-southeast-1"
    aws_sns_topic_arn: Optional[str] = None
    aws_secrets_manager_prefix: str = "ats/"

    # --- AI ---
    ai_provider: str = "openai"           # "openai" or "anthropic"
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    # Model name must match the chosen provider:
    #   OpenAI:    gpt-4o-mini  gpt-4o  o1-mini
    #   Anthropic: claude-sonnet-5  claude-haiku-4-5-20251001
    ai_model: str = "gpt-4o-mini"
    ai_max_tokens: int = 1024
    ai_timeout: int = 30
    ai_enabled: bool = True

    # --- Market Universe ---
    universe: list[str] = Field(
        default=["SPY", "QQQ", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA"]
    )

    # --- Scanner ---
    scan_interval_seconds: int = 60
    max_candidates: int = 10
    # Universe source for live scanner ("hardcoded" uses the `universe` list above)
    universe_source: str = "hardcoded"   # hardcoded | sp500 | nasdaq100 | sp400 | all

    # --- Scanner filters (used by scripts/scan.py and MarketScanner) ---
    scan_min_price: float = 10.0
    scan_min_avg_volume: float = 1_000_000
    scan_min_rvol: float = 0.7

    # --- Risk Parameters (paper defaults) ---
    max_open_positions: int = 3
    max_position_value_usd: Decimal = Decimal("500")
    max_risk_per_trade_usd: Decimal = Decimal("5")
    max_daily_loss_usd: Decimal = Decimal("25")
    max_daily_trades: int = 10
    max_total_exposure_pct: Decimal = Decimal("0.25")
    max_spread_pct: Decimal = Decimal("0.005")
    min_volume: int = 500_000
    cooldown_after_loss_minutes: int = 30

    # --- Strategy Parameters ---
    ema_short: int = 20
    ema_long: int = 50
    rsi_min: int = 40
    rsi_max: int = 70
    relative_volume_min: Decimal = Decimal("1.5")
    momentum_lookback: int = 5
    atr_max_pct: Decimal = Decimal("0.03")

    # --- Order Execution ---
    limit_order_offset_pct: Decimal = Decimal("0.001")
    order_timeout_seconds: int = 60

    # --- API ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_debug: bool = False

    # --- Logging ---
    log_level: str = "INFO"
    log_json: bool = True

    @property
    def ibkr_port(self) -> int:
        return self.ibkr_port_live if self.trading_mode == TradingMode.LIVE else self.ibkr_port_paper

    @property
    def is_live(self) -> bool:
        return self.trading_mode == TradingMode.LIVE

    @property
    def is_paper(self) -> bool:
        return self.trading_mode == TradingMode.PAPER

    @property
    def is_backtest(self) -> bool:
        return self.trading_mode == TradingMode.BACKTEST


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
