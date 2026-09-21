"""Custom exceptions for the ATS system."""


class ATSException(Exception):
    """Base exception for all ATS errors."""


class ConfigurationError(ATSException):
    """Invalid or missing configuration."""


class BrokerConnectionError(ATSException):
    """IBKR broker connection failure."""


class BrokerExecutionError(ATSException):
    """Order submission or execution failure."""


class MarketDataError(ATSException):
    """Market data unavailable or stale."""


class StaleDataError(MarketDataError):
    """Market data is too old to act on."""


class RiskRejectionError(ATSException):
    """Trade rejected by the deterministic risk engine."""


class AIAnalysisError(ATSException):
    """AI analysis failed or returned invalid output."""


class AITimeoutError(AIAnalysisError):
    """AI API call timed out."""


class AIInvalidResponseError(AIAnalysisError):
    """AI returned malformed or unparseable output."""


class PositionSizingError(ATSException):
    """Could not calculate a valid position size."""


class OrderStateError(ATSException):
    """Invalid order state transition."""


class DatabaseError(ATSException):
    """Database operation failed."""


class ReconciliationError(ATSException):
    """Internal state does not match broker state."""


class KillSwitchActiveError(ATSException):
    """Trading is disabled via kill switch."""


class LiveTradingNotAllowedError(ATSException):
    """Attempt to execute live trade without explicit LIVE mode configuration."""
