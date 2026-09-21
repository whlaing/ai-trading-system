#!/usr/bin/env bash
# ============================================================
#  ATS Paper Trading Launcher
#  Usage: ./scripts/start_paper.sh
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$SCRIPT_DIR/.."
VENV="$ROOT/.venv/bin/python"

cd "$ROOT"

# ── Preflight checks ────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════"
echo "  ATS — Paper Trading Preflight Check"
echo "══════════════════════════════════════════════════════"

# 1. Virtual environment
if [ ! -f "$VENV" ]; then
  echo "  ✗ .venv not found. Run: python3 -m venv .venv && .venv/bin/pip install -e .[dev]"
  exit 1
fi
echo "  ✓ Virtual environment found"

# 2. .env file
if [ ! -f "$ROOT/.env" ]; then
  echo "  ✗ .env not found. Copy .env.example to .env and fill in your credentials."
  exit 1
fi
echo "  ✓ .env file found"

# 3. Check trading mode
TRADING_MODE=$(grep '^TRADING_MODE=' "$ROOT/.env" | cut -d= -f2 | tr -d ' ')
if [ "$TRADING_MODE" != "PAPER" ]; then
  echo "  ✗ TRADING_MODE=$TRADING_MODE — set TRADING_MODE=PAPER in .env for paper trading"
  exit 1
fi
echo "  ✓ Trading mode: PAPER"

# 4. Check IBKR settings
IBKR_HOST=$(grep '^IBKR_HOST=' "$ROOT/.env" | cut -d= -f2 | tr -d ' ')
IBKR_PORT=$(grep '^IBKR_PORT_PAPER=' "$ROOT/.env" | cut -d= -f2 | tr -d ' ')
echo "  ℹ IBKR: ${IBKR_HOST:-127.0.0.1}:${IBKR_PORT:-7497}"
echo "    → Make sure TWS or IB Gateway is running and API is enabled"

# 5. Check AI setting
AI_ENABLED=$(grep '^AI_ENABLED=' "$ROOT/.env" | cut -d= -f2 | tr -d ' ')
if [ "$AI_ENABLED" = "true" ]; then
  API_KEY=$(grep '^ANTHROPIC_API_KEY=' "$ROOT/.env" | cut -d= -f2 | tr -d ' ')
  if [[ "$API_KEY" == *"REPLACE_WITH"* ]] || [ -z "$API_KEY" ]; then
    echo "  ✗ AI_ENABLED=true but ANTHROPIC_API_KEY is not set in .env"
    exit 1
  fi
  echo "  ✓ AI enabled (claude)"
else
  echo "  ℹ AI disabled — set AI_ENABLED=true in .env to enable"
fi

# 6. Init database (safe to run every time — idempotent)
echo ""
echo "  Initialising database ..."
"$VENV" scripts/init_db.py

# ── Launch ──────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════"
echo "  Starting ATS paper trader  (Ctrl-C to stop)"
echo "══════════════════════════════════════════════════════"
echo ""

exec "$VENV" -m src.main
