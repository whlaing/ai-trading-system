"""
Standalone backtest script.

Downloads historical OHLCV data via yfinance (no IBKR required) and
runs the TrendMomentum strategy through the backtesting engine.

Usage:
    python scripts/backtest.py
    python scripts/backtest.py --symbols AAPL MSFT NVDA --years 2
    python scripts/backtest.py --symbols SPY --start 2022-01-01 --end 2024-01-01
    python scripts/backtest.py --symbols AAPL --capital 5000
    python scripts/backtest.py --symbols AAPL --rvol 0.8 --rsi-min 35 --debug
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ATS Backtest Runner")
    p.add_argument("--symbols", nargs="+", default=["SPY", "QQQ", "AAPL", "MSFT", "NVDA"])
    p.add_argument("--years", type=int, default=3, help="Years of history (default: 3)")
    p.add_argument("--start", type=str, default=None, help="Start date YYYY-MM-DD")
    p.add_argument("--end", type=str, default=None, help="End date YYYY-MM-DD")
    p.add_argument("--capital", type=float, default=10_000, help="Starting capital per symbol (default: 10000)")

    # Risk parameter overrides
    r = p.add_argument_group("risk overrides")
    r.add_argument("--risk-per-trade", type=float, default=None,
                   help="Max $ risk per trade (default: 1%% of capital)")
    r.add_argument("--max-position", type=float, default=None,
                   help="Max position value $ (default: 40%% of capital)")
    r.add_argument("--max-daily-loss", type=float, default=None,
                   help="Max daily loss $ (default: 5%% of capital)")
    r.add_argument("--max-daily-trades", type=int, default=5,
                   help="Max trades per day in backtest (default: 5)")

    # Strategy parameter overrides
    g = p.add_argument_group("strategy overrides")
    g.add_argument("--ema-short", type=int, default=20, help="EMA short period (default: 20)")
    g.add_argument("--ema-long", type=int, default=50, help="EMA long period (default: 50)")
    g.add_argument("--rsi-min", type=int, default=40, help="RSI minimum (default: 40)")
    g.add_argument("--rsi-max", type=int, default=70, help="RSI maximum (default: 70)")
    g.add_argument("--rvol", type=float, default=0.8,
                   help="Relative volume minimum for daily bars (default: 0.8, live default: 1.5)")
    g.add_argument("--atr-max-pct", type=float, default=0.05,
                   help="Max ATR as %% of price (default: 0.05 for daily, live: 0.03)")
    g.add_argument("--spread-max-pct", type=float, default=0.005,
                   help="Max spread %% (default: 0.005)")

    p.add_argument("--debug", action="store_true", help="Print why each bar was rejected")
    return p.parse_args()


def download_candles(symbol: str, start: datetime, end: datetime) -> list:
    import yfinance as yf
    from src.common.models import Candle

    df = yf.Ticker(symbol).history(
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        interval="1d",
    )
    if df.empty:
        print(f"  [!] No data returned for {symbol}")
        return []

    return [
        Candle(
            symbol=symbol,
            timestamp=ts.to_pydatetime().replace(tzinfo=None),
            open=Decimal(str(round(float(row["Open"]), 6))),
            high=Decimal(str(round(float(row["High"]), 6))),
            low=Decimal(str(round(float(row["Low"]), 6))),
            close=Decimal(str(round(float(row["Close"]), 6))),
            volume=int(row["Volume"]),
            interval="1d",
        )
        for ts, row in df.iterrows()
    ]


def run_debug(symbol: str, candles: list, settings, strategy) -> None:
    """Print per-bar signal/rejection reasons to diagnose filtering."""
    from src.backtest.engine import BacktestEngine
    from src.scanner.indicators import (
        atr, average_volume, ema, momentum, relative_volume, rsi, sma
    )
    from src.common.models import TechnicalIndicators

    warmup = max(settings.ema_long + 5, 60)
    pass_count = fail_count = 0

    print(f"\n  Debug: {symbol} ({len(candles)} bars, warmup={warmup})")
    print(f"  {'Date':<12} {'Price':>8} {'EMA20':>8} {'EMA50':>8} {'RSI':>6} {'RelVol':>8} {'Mom':>8}  Result")
    print(f"  {'─'*12} {'─'*8} {'─'*8} {'─'*8} {'─'*6} {'─'*8} {'─'*8}  {'─'*30}")

    for i in range(warmup, min(len(candles), warmup + 30)):  # Show first 30 active bars
        window = candles[:i + 1]
        bar = candles[i]
        closes = [c.close for c in window]
        highs  = [c.high for c in window]
        lows   = [c.low for c in window]
        vols   = [c.volume for c in window]

        e_short = ema(closes, settings.ema_short)
        e_long  = ema(closes, settings.ema_long)
        r       = rsi(closes, 14)
        avg_vol = average_volume(vols, 20)
        rel_vol = relative_volume(vols[-1], avg_vol) if avg_vol else None
        mom     = momentum(closes, settings.momentum_lookback)
        a       = atr(highs, lows, closes, 14)

        tech = TechnicalIndicators(
            symbol=symbol, timestamp=bar.timestamp, price=bar.close,
            ema_short=e_short, ema_long=e_long, rsi_14=r,
            atr_14=a, volume=vols[-1], avg_volume_20=avg_vol,
            relative_volume=rel_vol, momentum_5d=mom,
            spread_pct=Decimal("0.0001"),
        )

        signal = strategy.evaluate(tech)
        result = "SIGNAL" if signal else "no signal"

        e_s = f"{float(e_short):.2f}" if e_short else "N/A"
        e_l = f"{float(e_long):.2f}" if e_long else "N/A"
        rsi_ = f"{float(r):.1f}" if r else "N/A"
        rv  = f"{float(rel_vol):.2f}x" if rel_vol else "N/A"
        m   = f"{float(mom):.3f}" if mom else "N/A"

        marker = ">>>" if signal else "   "
        print(f"  {bar.timestamp.strftime('%Y-%m-%d'):<12} {float(bar.close):>8.2f} {e_s:>8} {e_l:>8} {rsi_:>6} {rv:>8} {m:>8}  {marker} {result}")

        if signal:
            pass_count += 1
        else:
            fail_count += 1

    print(f"\n  First 30 bars: {pass_count} signals, {fail_count} filtered")


def print_separator(char: str = "─", width: int = 70) -> None:
    print(char * width)


def print_result(symbol: str, result) -> None:
    from src.common.models import TradeStatus

    closed = [t for t in result.trades if t.status == TradeStatus.CLOSED]
    print_separator()
    print(f"  {symbol}   {result.start_date.strftime('%Y-%m-%d')} → {result.end_date.strftime('%Y-%m-%d')}")
    print_separator("─")

    if not closed:
        print("  No completed trades.")
        print()
        return

    winners = [t for t in closed if t.realized_pnl and t.realized_pnl > 0]
    losers  = [t for t in closed if t.realized_pnl and t.realized_pnl <= 0]
    net_pnl = sum(float(t.realized_pnl) for t in closed if t.realized_pnl)
    gross_profit = sum(float(t.realized_pnl) for t in winners)
    gross_loss   = abs(sum(float(t.realized_pnl) for t in losers))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    win_rate = len(winners) / len(closed)
    expectancy = net_pnl / len(closed)
    total_comm = sum(float(t.commission) for t in closed)
    total_slip = sum(float(t.estimated_slippage) for t in closed)
    return_pct = (float(result.final_capital) - float(result.initial_capital)) / float(result.initial_capital) * 100

    # Max drawdown
    peak = float(result.initial_capital)
    equity = float(result.initial_capital)
    max_dd = 0.0
    for t in sorted(closed, key=lambda x: x.closed_at or datetime.min):
        if t.realized_pnl:
            equity += float(t.realized_pnl)
        if equity > peak:
            peak = equity
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)

    # Average hold
    holds = [(t.closed_at - t.filled_at).days for t in closed if t.filled_at and t.closed_at]
    avg_hold = sum(holds) / len(holds) if holds else 0

    print(f"  Trades        : {len(closed):>6}   ({len(winners)} wins / {len(losers)} losses)")
    print(f"  Win Rate      : {win_rate:>6.1%}")
    print(f"  Profit Factor : {profit_factor:>6.2f}")
    print(f"  Expectancy    : ${expectancy:>+8.2f} / trade")
    print(f"  Net P/L       : ${net_pnl:>+9.2f}  ({return_pct:+.1f}%)")
    print(f"  Max Drawdown  : {max_dd:>6.1%}")
    print(f"  Avg Hold      : {avg_hold:>5.1f} days")
    print(f"  Commission    : ${total_comm:>8.2f}")
    print(f"  Est. Slippage : ${total_slip:>8.2f}")
    print(f"  Start Capital : ${float(result.initial_capital):>10,.2f}")
    print(f"  End Capital   : ${float(result.final_capital):>10,.2f}")
    print()
    print(f"  {'Date':<12} {'Reason':<18} {'Qty':>5} {'Entry':>8} {'Exit':>8} {'P/L':>9}")
    print(f"  {'─'*12} {'─'*18} {'─'*5} {'─'*8} {'─'*8} {'─'*9}")
    for t in sorted(closed, key=lambda x: x.closed_at or datetime.min)[-15:]:
        date = t.closed_at.strftime("%Y-%m-%d") if t.closed_at else "?"
        reason = (t.rejection_reason or "—")[:18]
        entry = f"${float(t.actual_entry):.2f}" if t.actual_entry else "?"
        exit_ = f"${float(t.exit_price):.2f}" if t.exit_price else "?"
        pnl   = f"${float(t.realized_pnl):+.2f}" if t.realized_pnl is not None else "?"
        print(f"  {date:<12} {reason:<18} {t.quantity:>5} {entry:>8} {exit_:>8} {pnl:>9}")
    print()


def print_aggregate(all_results: dict) -> None:
    from src.common.models import TradeStatus

    all_closed = [
        t for r in all_results.values()
        for t in r.trades if t.status == TradeStatus.CLOSED
    ]
    if not all_closed:
        return

    winners = [t for t in all_closed if t.realized_pnl and t.realized_pnl > 0]
    losers  = [t for t in all_closed if t.realized_pnl and t.realized_pnl <= 0]
    net_pnl = sum(float(t.realized_pnl) for t in all_closed if t.realized_pnl)
    gross_profit = sum(float(t.realized_pnl) for t in winners)
    gross_loss   = abs(sum(float(t.realized_pnl) for t in losers))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    win_rate = len(winners) / len(all_closed)

    print_separator("═")
    print("  AGGREGATE (all symbols combined)")
    print_separator("═")
    print(f"  Total Trades  : {len(all_closed):>6}   ({len(winners)} wins / {len(losers)} losses)")
    print(f"  Win Rate      : {win_rate:>6.1%}")
    print(f"  Profit Factor : {profit_factor:>6.2f}")
    print(f"  Net P/L       : ${net_pnl:>+9.2f}")
    print(f"  Commission    : ${sum(float(t.commission) for t in all_closed):>8.2f}")
    print_separator("═")
    print()


def main() -> None:
    args = parse_args()

    import os
    os.environ.setdefault("TRADING_MODE", "BACKTEST")

    from src.backtest.engine import BacktestEngine
    from src.common.config import Settings, TradingMode
    from src.common.logging import configure_logging
    from src.strategies.trend_momentum import TrendMomentumStrategy

    configure_logging(level="WARNING", json_output=False)

    capital = Decimal(str(args.capital))

    # Derive risk defaults from capital if not specified
    risk_per_trade  = args.risk_per_trade  or float(capital) * 0.01   # 1% of capital
    max_position    = args.max_position    or float(capital) * 0.40   # 40% of capital
    max_daily_loss  = args.max_daily_loss  or float(capital) * 0.05   # 5% of capital

    settings = Settings(
        trading_mode=TradingMode.BACKTEST,
        # Risk
        max_risk_per_trade_usd=Decimal(str(round(risk_per_trade, 2))),
        max_position_value_usd=Decimal(str(round(max_position, 2))),
        max_daily_loss_usd=Decimal(str(round(max_daily_loss, 2))),
        max_daily_trades=args.max_daily_trades,
        max_open_positions=10,          # Backtest simulates one symbol at a time
        # Strategy
        ema_short=args.ema_short,
        ema_long=args.ema_long,
        rsi_min=args.rsi_min,
        rsi_max=args.rsi_max,
        relative_volume_min=Decimal(str(args.rvol)),
        atr_max_pct=Decimal(str(args.atr_max_pct)),
        max_spread_pct=Decimal(str(args.spread_max_pct)),
    )
    strategy = TrendMomentumStrategy(settings)
    engine   = BacktestEngine(settings)

    end_date   = datetime.strptime(args.end, "%Y-%m-%d") if args.end else datetime.today()
    start_date = (
        datetime.strptime(args.start, "%Y-%m-%d")
        if args.start
        else end_date - timedelta(days=args.years * 365)
    )

    print()
    print_separator("═")
    print(f"  ATS BACKTEST  —  TrendMomentum strategy")
    print(f"  Period        : {start_date.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')}")
    print(f"  Symbols       : {', '.join(args.symbols)}")
    print(f"  Capital       : ${float(capital):,.2f} per symbol")
    print(f"  EMA           : {args.ema_short}/{args.ema_long}  RSI: [{args.rsi_min},{args.rsi_max}]  RelVol≥{args.rvol}  ATR≤{args.atr_max_pct:.0%}")
    print(f"  Risk/Trade    : ${risk_per_trade:.0f}  Max Position: ${max_position:.0f}  Daily Loss Limit: ${max_daily_loss:.0f}")
    print_separator("═")

    candles_by_symbol: dict = {}
    for symbol in args.symbols:
        print(f"  Downloading {symbol} ...", end=" ", flush=True)
        candles = download_candles(symbol, start_date, end_date)
        if candles:
            candles_by_symbol[symbol] = candles
            print(f"{len(candles)} daily bars")
        else:
            print("skipped (no data)")

    if not candles_by_symbol:
        print("\n[!] No data downloaded.")
        sys.exit(1)

    # Debug mode: show per-bar filtering before running full backtest
    if args.debug:
        print()
        for symbol, candles in candles_by_symbol.items():
            run_debug(symbol, candles, settings, strategy)

    print("\n  Running backtest ...\n")

    results = engine.run(
        strategy=strategy,
        candles_by_symbol=candles_by_symbol,
        initial_capital=capital,
        start_date=start_date,
        end_date=end_date,
    )

    for symbol, result in results.items():
        print_result(symbol, result)

    if len(results) > 1:
        print_aggregate(results)


if __name__ == "__main__":
    main()
