"""
Stock scanner — fetches a broad universe and surfaces the best technical setups.

Universe sources (no API key needed):
  sp500      — S&P 500 (503 tickers) from Wikipedia
  nasdaq100  — NASDAQ-100 (100 tickers) from Wikipedia
  sp400      — S&P 400 Mid-Cap from Wikipedia
  all        — sp500 + nasdaq100 combined, deduplicated

Usage:
    python scripts/scan.py                        # sp500, top 20
    python scripts/scan.py --universe nasdaq100
    python scripts/scan.py --universe all --top 30
    python scripts/scan.py --tickers AAPL MSFT NVDA   # manual list
    python scripts/scan.py --min-price 20 --min-vol 2000000
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


# ─────────────────────────────────────────────────────────────────────────────
# Universe fetchers
# ─────────────────────────────────────────────────────────────────────────────

def _wiki_tables(url: str) -> list:
    """Fetch Wikipedia page tables, bypassing the default pandas user-agent block."""
    import requests
    import pandas as pd
    from io import StringIO

    headers = {"User-Agent": "Mozilla/5.0 (ATS-Scanner/1.0; +https://github.com/whlaing/ai-trading-system)"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    return pd.read_html(StringIO(resp.text))


def _clean_tickers(series) -> list[str]:
    return sorted(series.str.replace(".", "-", regex=False).str.strip().tolist())


def _find_ticker_column(tables: list, min_count: int = 50) -> list[str]:
    """Search all tables for a column whose values look like real stock tickers."""
    import re
    ticker_re = re.compile(r"^[A-Z]{1,5}(-[A-Z])?$")
    for df in tables:
        for col in df.columns:
            vals = df[col].dropna().astype(str).str.strip()
            valid = vals[vals.str.match(ticker_re)]
            if len(valid) >= min_count:
                return _clean_tickers(valid)
    return []


def fetch_sp500() -> list[str]:
    tables = _wiki_tables("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
    tickers = _find_ticker_column(tables, min_count=400)
    if not tickers:
        raise ValueError("Could not find S&P 500 ticker table on Wikipedia")
    return tickers


def fetch_nasdaq100() -> list[str]:
    """
    Fetch NASDAQ-100 tickers from slickcharts.com (more reliable than Wikipedia
    for this index, which no longer has a component table).
    """
    import requests
    import pandas as pd
    from io import StringIO
    import re

    headers = {"User-Agent": "Mozilla/5.0 (ATS-Scanner/1.0; +https://github.com/whlaing/ai-trading-system)"}
    resp = requests.get("https://slickcharts.com/nasdaq100", headers=headers, timeout=15)
    resp.raise_for_status()
    tables = pd.read_html(StringIO(resp.text))
    ticker_re = re.compile(r"^[A-Z]{1,5}(-[A-Z])?$")
    for df in tables:
        for col in df.columns:
            vals = df[col].dropna().astype(str).str.strip()
            valid = vals[vals.str.match(ticker_re)]
            if len(valid) >= 50:
                return _clean_tickers(valid)
    raise ValueError("Could not parse NASDAQ-100 tickers from slickcharts.com")


def fetch_sp400() -> list[str]:
    tables = _wiki_tables("https://en.wikipedia.org/wiki/List_of_S%26P_400_companies")
    tickers = _find_ticker_column(tables, min_count=200)
    if not tickers:
        raise ValueError("Could not find S&P 400 ticker table on Wikipedia")
    return tickers


# SGX Straits Times Index (STI) 30 components + liquid mid-caps
# Tickers use yfinance .SI suffix for download; IBKR uses the base code on SGX exchange
_SGX_STI_TICKERS = [
    "D05.SI",   # DBS Group
    "O39.SI",   # OCBC Bank
    "U11.SI",   # UOB
    "Z74.SI",   # Singtel
    "C6L.SI",   # Singapore Airlines
    "BN4.SI",   # Keppel Corp
    "G13.SI",   # Genting Singapore
    "H78.SI",   # Hongkong Land
    "J36.SI",   # Jardine Matheson
    "J37.SI",   # Jardine Cycle & Carriage
    "C38U.SI",  # CapitaLand Integrated Commercial Trust
    "A17U.SI",  # Ascendas REIT
    "ME8U.SI",  # Mapletree Industrial Trust
    "M44U.SI",  # Mapletree Logistics Trust
    "N2IU.SI",  # Mapletree Pan Asia Commercial Trust
    "U96.SI",   # Sembcorp Industries
    "S63.SI",   # ST Engineering
    "V03.SI",   # Venture Corp
    "BS6.SI",   # YZJ Shipbldg SGD
    "9CI.SI",   # CapitaLand Investment
    "C09.SI",   # City Developments
    "F34.SI",   # Wilmar International
    "D01.SI",   # DFI Retail Group
    "S58.SI",   # SATS
    "T39.SI",   # SIA Engineering
    "U14.SI",   # UOL Group
    "Y92.SI",   # Thai Beverage
    "AWX.SI",   # AEM Holdings
    "V2Y.SI",   # ComfortDelGro
    "BVA.SI",   # Frencken Group
]


def fetch_sgx_sti() -> list[str]:
    """Return SGX STI component tickers (yfinance .SI format)."""
    return sorted(_SGX_STI_TICKERS)


def get_universe(source: str, extra: list[str]) -> list[str]:
    fetchers = {
        "sp500": fetch_sp500,
        "nasdaq100": fetch_nasdaq100,
        "sp400": fetch_sp400,
        "sgx": fetch_sgx_sti,
    }
    if source == "all":
        tickers = list({*fetch_sp500(), *fetch_nasdaq100()})
    elif source in fetchers:
        tickers = fetchers[source]()
    else:
        tickers = []

    combined = list({*tickers, *extra})
    combined.sort()
    return combined


# ─────────────────────────────────────────────────────────────────────────────
# Data download
# ─────────────────────────────────────────────────────────────────────────────

def batch_download(tickers: list[str], days: int = 90) -> dict:
    """Download OHLCV for all tickers in one yfinance call (much faster than one-by-one)."""
    import yfinance as yf

    end = datetime.today()
    start = end - timedelta(days=days)

    data = yf.download(
        tickers=" ".join(tickers),
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        interval="1d",
        group_by="ticker",
        auto_adjust=True,
        progress=False,
        threads=True,
    )
    return data


def extract_candles(data, symbol: str, days: int = 90) -> list:
    """Extract per-symbol OHLCV rows from a multi-ticker yfinance download."""
    from src.common.models import Candle

    try:
        if hasattr(data.columns, "levels"):
            df = data[symbol].dropna(how="all")
        else:
            df = data.dropna(how="all")
    except (KeyError, TypeError):
        return []

    if df.empty or len(df) < 20:
        return []

    candles = []
    for ts, row in df.iterrows():
        try:
            candles.append(
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
            )
        except Exception:
            continue
    return candles


# ─────────────────────────────────────────────────────────────────────────────
# Scoring
# ─────────────────────────────────────────────────────────────────────────────

def score_symbol(symbol: str, candles: list, settings) -> dict | None:
    """
    Compute technical indicators and return a scored result dict, or None if
    the symbol fails minimum quality thresholds.
    """
    from src.scanner.indicators import (
        atr, average_volume, ema, momentum, relative_volume, rsi
    )

    if len(candles) < 55:
        return None

    closes  = [c.close  for c in candles]
    highs   = [c.high   for c in candles]
    lows    = [c.low    for c in candles]
    volumes = [c.volume for c in candles]

    price    = float(candles[-1].close)
    vol_last = volumes[-1]

    e_short  = ema(closes, settings.ema_short)
    e_long   = ema(closes, settings.ema_long)
    rsi_val  = rsi(closes, 14)
    atr_val  = atr(highs, lows, closes, 14)
    avg_vol  = average_volume(volumes, 20)
    rel_vol  = relative_volume(vol_last, avg_vol) if avg_vol else None
    mom      = momentum(closes, settings.momentum_lookback)

    # Hard filters
    if any(v is None for v in [e_short, e_long, rsi_val, atr_val, avg_vol, rel_vol, mom]):
        return None
    if price < settings.scan_min_price:
        return None
    if float(avg_vol) < settings.scan_min_avg_volume:
        return None

    e_s = float(e_short)
    e_l = float(e_long)
    r   = float(rsi_val)
    a   = float(atr_val)
    rv  = float(rel_vol)
    m   = float(mom)

    # Must be in bullish trend
    if not (price > e_s > e_l):
        return None

    # RSI must be healthy (not overbought, not oversold)
    if not (settings.rsi_min <= r <= settings.rsi_max):
        return None

    # ATR% within tolerance
    atr_pct = a / price
    if atr_pct > float(settings.atr_max_pct):
        return None

    # Momentum must be positive
    if m <= 0:
        return None

    # Minimum relative volume
    if rv < float(settings.scan_min_rvol):
        return None

    # ── Composite score ──────────────────────────────────────────────────────
    # Each component is normalised to roughly [0, 1]:
    #   trend_strength  — how far price is above long EMA (%)
    #   rsi_score       — penalises extremes; peaks near RSI 55
    #   rvol_score      — capped at 3× to avoid outlier dominance
    #   momentum_score  — 5-day return

    trend_strength = (price - e_l) / e_l          # e.g. 0.05 = 5% above EMA50
    rsi_score      = 1.0 - abs(r - 55) / 30       # peaks at RSI 55
    rvol_score     = min(rv / 3.0, 1.0)
    mom_score      = min(m / 0.05, 1.0)            # capped at 5% 5-day return

    composite = (
        trend_strength * 0.35
        + rsi_score    * 0.25
        + rvol_score   * 0.20
        + mom_score    * 0.20
    )

    return {
        "symbol":   symbol,
        "score":    round(composite, 4),
        "price":    round(price, 2),
        "ema20":    round(e_s, 2),
        "ema50":    round(e_l, 2),
        "rsi":      round(r, 1),
        "rvol":     round(rv, 2),
        "atr_pct":  round(atr_pct * 100, 2),
        "mom_5d":   round(m * 100, 2),
        "avg_vol_m": round(float(avg_vol) / 1_000_000, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────────────────────

def print_results(results: list[dict], top: int) -> None:
    shown = results[:top]
    if not shown:
        print("\n  No candidates passed all filters.\n")
        return

    w = 70
    print("─" * w)
    print(f"  {'#':>3}  {'Symbol':<8}  {'Score':>6}  {'Price':>8}  {'RSI':>5}  {'RelVol':>7}  {'Mom5d':>7}  {'ATR%':>5}  {'AvgVol':>7}")
    print("─" * w)
    for i, r in enumerate(shown, 1):
        trend = "▲" if r["price"] > r["ema20"] > r["ema50"] else "→"
        print(
            f"  {i:>3}  {r['symbol']:<8}  {r['score']:>6.4f}  "
            f"{r['price']:>8.2f}  {r['rsi']:>5.1f}  {r['rvol']:>6.2f}x  "
            f"{r['mom_5d']:>+6.2f}%  {r['atr_pct']:>4.1f}%  "
            f"{r['avg_vol_m']:>5.1f}M  {trend}"
        )
    print("─" * w)
    print(f"\n  Showing top {len(shown)} of {len(results)} candidates that passed all filters.\n")


def write_results(results: list[dict], top: int, output: str, universe: str) -> None:
    """Persist the selected candidates for the paper/live trader."""
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    selected = results[:top]

    # Strip yfinance exchange suffixes (.SI, .HK, .L, etc.) so IBKR gets the bare ticker
    def _ibkr_symbol(sym: str) -> str:
        return sym.split(".")[0] if "." in sym else sym

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "universe": universe,
        "symbols": [_ibkr_symbol(r["symbol"]) for r in selected],
        "results": [{**r, "symbol": _ibkr_symbol(r["symbol"])} for r in selected],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"  Wrote {len(selected)} candidates → {path}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

# Per-universe filter defaults — override US defaults for smaller markets
_UNIVERSE_DEFAULTS = {
    "sgx":     dict(min_price=0.50, min_vol=100_000, min_rvol=0.1, rsi_min=35, rsi_max=75, atr_max_pct=0.08),
    "sp500":   dict(min_price=10.0, min_vol=500_000, min_rvol=0.5, rsi_min=40, rsi_max=72, atr_max_pct=0.06),
    "nasdaq100": dict(min_price=10.0, min_vol=500_000, min_rvol=0.5, rsi_min=40, rsi_max=72, atr_max_pct=0.06),
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="ATS Stock Scanner",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--universe", default="sp500",
        choices=["sp500", "nasdaq100", "sp400", "all", "sgx"],
        help="Universe to scan",
    )
    p.add_argument("--tickers", nargs="*", default=[], help="Add extra tickers to the universe")
    p.add_argument("--top", type=int, default=20, help="Number of top results to show")
    p.add_argument(
        "--output",
        default="data/scan_candidates.json",
        help="JSON output consumed by the trader",
    )
    p.add_argument("--days", type=int, default=90, help="Days of history to download")

    f = p.add_argument_group("filters (universe-specific defaults applied automatically)")
    f.add_argument("--min-price",   type=float, default=None, help="Min stock price")
    f.add_argument("--min-vol",     type=float, default=None, help="Min 20-day avg volume")
    f.add_argument("--min-rvol",    type=float, default=None, help="Min relative volume")
    f.add_argument("--rsi-min",     type=int,   default=None, help="RSI lower bound")
    f.add_argument("--rsi-max",     type=int,   default=None, help="RSI upper bound")
    f.add_argument("--atr-max-pct", type=float, default=None, help="Max ATR %% of price")

    args = p.parse_args()

    # Apply universe-specific defaults, then US defaults for anything still None
    universe_defaults = _UNIVERSE_DEFAULTS.get(args.universe, {})
    us_defaults = dict(min_price=10.0, min_vol=1_000_000, min_rvol=0.7, rsi_min=40, rsi_max=72, atr_max_pct=0.06)
    for key, us_val in us_defaults.items():
        arg_key = key.replace("-", "_")
        if getattr(args, arg_key) is None:
            setattr(args, arg_key, universe_defaults.get(key, us_val))

    return args


def main() -> None:
    args = parse_args()

    import os
    os.environ.setdefault("TRADING_MODE", "BACKTEST")

    from src.common.config import Settings, TradingMode
    from src.common.logging import configure_logging

    configure_logging(level="WARNING", json_output=False)

    settings = Settings(
        trading_mode=TradingMode.BACKTEST,
        rsi_min=args.rsi_min,
        rsi_max=args.rsi_max,
        atr_max_pct=Decimal(str(args.atr_max_pct)),
        scan_min_price=args.min_price,
        scan_min_avg_volume=args.min_vol,
        scan_min_rvol=args.min_rvol,
    )

    # ── 1. Fetch universe ────────────────────────────────────────────────────
    print(f"\n  Fetching {args.universe} universe ...", end=" ", flush=True)
    t0 = time.time()
    try:
        tickers = get_universe(args.universe, args.tickers)
    except Exception as exc:
        print(f"\n  [!] Failed to fetch universe: {exc}")
        if args.tickers:
            tickers = args.tickers
        else:
            sys.exit(1)

    print(f"{len(tickers)} tickers  ({time.time()-t0:.1f}s)")

    # ── 2. Download price data (batch) ───────────────────────────────────────
    print(f"  Downloading {args.days}-day history for all tickers ...", end=" ", flush=True)
    t0 = time.time()
    raw_data = batch_download(tickers, days=args.days)
    print(f"done  ({time.time()-t0:.1f}s)")

    # ── 3. Score each ticker ─────────────────────────────────────────────────
    print(f"  Scoring {len(tickers)} tickers ...", end=" ", flush=True)
    t0 = time.time()
    results = []
    for symbol in tickers:
        candles = extract_candles(raw_data, symbol, days=args.days)
        result = score_symbol(symbol, candles, settings)
        if result:
            results.append(result)

    results.sort(key=lambda x: x["score"], reverse=True)
    print(f"done  ({time.time()-t0:.1f}s)  —  {len(results)} passed filters")

    # ── 4. Print results ─────────────────────────────────────────────────────
    as_of = datetime.today().strftime("%Y-%m-%d %H:%M")
    print(f"\n  Top candidates  [universe: {args.universe.upper()}  |  as of {as_of}]")
    print(f"  Filters: price≥${args.min_price:.0f}  avgVol≥{args.min_vol/1e6:.1f}M  "
          f"RSI[{args.rsi_min},{args.rsi_max}]  relVol≥{args.min_rvol}  ATR≤{args.atr_max_pct:.0%}")
    print_results(results, args.top)
    write_results(results, args.top, args.output, args.universe)


if __name__ == "__main__":
    main()
