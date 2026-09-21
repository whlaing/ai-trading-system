"""Notification service — sends important alerts via AWS SNS."""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Optional

from src.common.config import Settings
from src.common.logging import get_logger
from src.common.models import NotificationEvent

log = get_logger(__name__)


def _decimal_default(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


class NotificationService:
    """
    Sends structured event notifications via AWS SNS.

    Only important events are sent (not every market scan).
    If SNS is not configured, notifications are logged only.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sns_client = None

    def _get_sns(self):
        if self._sns_client is None:
            import boto3
            self._sns_client = boto3.client("sns", region_name=self._settings.aws_region)
        return self._sns_client

    def send(
        self,
        event: NotificationEvent,
        message: str,
        details: Optional[dict] = None,
        trade_id: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> None:
        payload = {
            "event": event.value,
            "message": message,
            "timestamp": datetime.utcnow().isoformat(),
            "trade_id": trade_id,
            "symbol": symbol,
            "details": details or {},
        }

        log.info("notification.send", event=event.value, message=message, symbol=symbol, trade_id=trade_id)

        if not self._settings.aws_sns_topic_arn:
            log.debug("notification.sns_not_configured", event=event.value)
            return

        try:
            sns = self._get_sns()
            subject = f"[ATS] {event.value}"
            if symbol:
                subject += f" — {symbol}"
            sns.publish(
                TopicArn=self._settings.aws_sns_topic_arn,
                Subject=subject[:100],
                Message=json.dumps(payload, default=_decimal_default, indent=2),
                MessageAttributes={
                    "event_type": {
                        "DataType": "String",
                        "StringValue": event.value,
                    }
                },
            )
        except Exception as exc:
            log.error("notification.send_error", error=str(exc), event=event.value)

    def trade_opened(self, symbol: str, trade_id: str, entry: Decimal, quantity: int, stop: Decimal) -> None:
        self.send(
            NotificationEvent.TRADE_OPENED,
            f"{symbol}: BUY {quantity} @ {entry:.2f} | Stop: {stop:.2f}",
            details={"entry": entry, "quantity": quantity, "stop": stop},
            trade_id=trade_id,
            symbol=symbol,
        )

    def trade_closed(self, symbol: str, trade_id: str, exit_price: Decimal, pnl: Decimal) -> None:
        pnl_sign = "+" if pnl >= 0 else ""
        self.send(
            NotificationEvent.TRADE_CLOSED,
            f"{symbol}: CLOSED @ {exit_price:.2f} | P/L: {pnl_sign}{pnl:.2f}",
            details={"exit_price": exit_price, "pnl": pnl},
            trade_id=trade_id,
            symbol=symbol,
        )

    def daily_loss_limit(self, daily_pnl: Decimal, limit: Decimal) -> None:
        self.send(
            NotificationEvent.DAILY_LOSS_LIMIT,
            f"Daily loss limit reached: {daily_pnl:.2f} (limit: {limit:.2f}). Trading disabled.",
            details={"daily_pnl": daily_pnl, "limit": limit},
        )

    def trading_disabled(self, reason: str) -> None:
        self.send(NotificationEvent.TRADING_DISABLED, f"Trading disabled: {reason}")

    def system_error(self, component: str, error: str) -> None:
        self.send(
            NotificationEvent.SYSTEM_ERROR,
            f"System error in {component}: {error}",
            details={"component": component, "error": error},
        )

    def ibkr_disconnected(self) -> None:
        self.send(NotificationEvent.IBKR_DISCONNECTED, "IBKR connection lost")

    def unexpected_position(self, symbol: str, details: str) -> None:
        self.send(
            NotificationEvent.UNEXPECTED_POSITION,
            f"Unexpected position detected: {symbol} — {details}",
            symbol=symbol,
        )
