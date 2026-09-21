"""
Initialize the ATS database — create all tables.

Run once before starting paper trading:
    python scripts/init_db.py

Safe to re-run: uses CREATE TABLE IF NOT EXISTS under the hood.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def main() -> None:
    import os
    os.environ.setdefault("TRADING_MODE", "PAPER")

    from src.common.config import get_settings
    from src.common.logging import configure_logging
    from src.persistence.database import create_all_tables, get_engine

    settings = get_settings()
    configure_logging(level="INFO", json_output=False)

    url = settings.database_url
    print(f"\n  Database : {url}")
    print(f"  Mode     : {settings.trading_mode.value}")

    try:
        engine = get_engine()
        create_all_tables()
        # Verify by listing tables
        with engine.connect() as conn:
            if url.startswith("sqlite"):
                from sqlalchemy import text
                rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
            else:
                from sqlalchemy import text
                rows = conn.execute(text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                )).fetchall()
        tables = [r[0] for r in rows]
        print(f"\n  Tables created: {', '.join(sorted(tables))}")
        print("\n  ✓ Database ready.\n")
    except Exception as exc:
        print(f"\n  ✗ Failed: {exc}")
        print("\n  If using PostgreSQL, ensure the server is running:")
        print("    brew services start postgresql@16")
        print("    createdb ats")
        sys.exit(1)


if __name__ == "__main__":
    main()
