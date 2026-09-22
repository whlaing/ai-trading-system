"""
ATS Main Entry Point — paper/live trading loop.

Runs the scanner, strategy, AI analysis, risk checks, and order submission
on a configurable interval during market hours.
"""
from __future__ import annotations

import signal
import time
from datetime import datetime

from src.ai.agent import AIAnalysisAgent
from src.broker.ibkr_adapter import IBKRAdapter
from src.common.config import TradingMode, get_settings, load_scan_symbols
from src.common.exceptions import BrokerConnectionError, LiveTradingNotAllowedError
from src.common.logging import configure_logging, get_logger
from src.execution.engine import ExecutionEngine
from src.market_data.providers.ibkr_provider import IBKRMarketDataProvider
from src.market_data.service import MarketDataService
from src.notifications.service import NotificationService
from src.persistence.database import create_all_tables
from src.portfolio.monitor import PositionMonitor
from src.risk.engine import KillSwitch, RiskEngine
from src.scanner.scanner import MarketScanner
from src.strategies.trend_momentum import TrendMomentumStrategy

_shutdown = False


def _handle_signal(sig, frame):
    global _shutdown
    log.info("main.shutdown_signal", signal=sig)
    _shutdown = True


def main():
    global log
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    log = get_logger("main")

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    log.info("main.starting", mode=settings.trading_mode.value)

    if settings.trading_mode == TradingMode.BACKTEST:
        log.error("main.use_backtest_module", message="Use src/backtest/engine.py for backtesting")
        return

    if settings.trading_mode == TradingMode.LIVE and not settings.trading_enabled:
        raise LiveTradingNotAllowedError("TRADING_ENABLED must be true and TRADING_MODE=LIVE to run live")

    if settings.universe_source.lower() == "scan_file":
        settings.universe = load_scan_symbols(settings)
        log.info(
            "main.scan_universe_loaded",
            path=settings.scan_results_file,
            symbols=settings.universe,
        )

    # --- Bootstrap ---
    create_all_tables()

    kill_switch = KillSwitch(initial_state=settings.trading_enabled)
    broker = IBKRAdapter(
        host=settings.ibkr_host,
        port=settings.ibkr_port,
        client_id=settings.ibkr_client_id,
        is_live=settings.is_live,
    )
    notifications = NotificationService(settings)

    try:
        broker.connect()
    except BrokerConnectionError as exc:
        log.error("main.broker_connect_failed", error=str(exc))
        notifications.system_error("broker", str(exc))
        return

    ibkr_data_provider = IBKRMarketDataProvider(broker._ib)
    market_data = MarketDataService(ibkr_data_provider)
    risk_engine = RiskEngine(settings, kill_switch)
    ai_agent = AIAnalysisAgent(settings)
    strategy = TrendMomentumStrategy(settings)
    scanner = MarketScanner(market_data, settings)

    execution = ExecutionEngine(
        broker=broker,
        market_data=market_data,
        risk_engine=risk_engine,
        ai_agent=ai_agent,
        kill_switch=kill_switch,
        settings=settings,
    )
    monitor = PositionMonitor(
        broker=broker,
        market_data=market_data,
        execution=execution,
        kill_switch=kill_switch,
    )

    log.info("main.running", interval_seconds=settings.scan_interval_seconds)

    while not _shutdown:
        try:
            if not broker.is_connected():
                log.warning("main.broker_disconnected_reconnecting")
                notifications.ibkr_disconnected()
                kill_switch.disable("IBKR connection lost")
                time.sleep(10)
                try:
                    broker.connect()
                    kill_switch.enable()
                except BrokerConnectionError:
                    continue

            market_status = market_data.get_market_status()
            if not market_status.is_open:
                log.info("main.market_closed", session=market_status.session, next_open=str(market_status.next_open))
                time.sleep(60)
                continue

            # --- Monitor existing positions first ---
            monitor.monitor_all()

            # --- Reconciliation (every loop iteration) ---
            discrepancies = monitor.reconcile()
            if discrepancies:
                for msg in discrepancies:
                    notifications.unexpected_position("MULTIPLE", msg)

            # --- Check kill switch ---
            if not kill_switch.is_enabled:
                log.info("main.kill_switch_active_skip_scan")
                time.sleep(60)
                continue

            # --- Scan universe ---
            log.info("main.loop_tick", time=datetime.utcnow().strftime("%H:%M:%S UTC"))
            candidates = scanner.scan()

            # --- Evaluate strategy for each candidate ---
            for indicators in candidates:
                trade_signal = strategy.evaluate(indicators)
                if trade_signal is None:
                    continue
                record = execution.process_signal(trade_signal)
                if record and record.status.value == "FILLED":
                    notifications.trade_opened(
                        record.symbol,
                        record.trade_id,
                        record.actual_entry or record.entry_proposal,
                        record.quantity,
                        record.stop_loss,
                    )

        except KeyboardInterrupt:
            break
        except Exception as exc:
            log.error("main.loop_error", error=str(exc), exc_info=True)
            notifications.system_error("main_loop", str(exc))
            time.sleep(30)

        time.sleep(settings.scan_interval_seconds)

    log.info("main.shutdown")
    broker.disconnect()


if __name__ == "__main__":
    log = get_logger("main")
    main()
