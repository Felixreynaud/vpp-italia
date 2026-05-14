"""Backfill historical MGP prices for all zones over the last N days.

Idempotent: re-running skips dates already in the DB. Typical use after
fresh deployment:

    python -m scripts.backfill_mgp_prices             # last 90 days (default)
    python -m scripts.backfill_mgp_prices --days 30   # last 30 days
    python -m scripts.backfill_mgp_prices --days 365  # last year (~70 MB)

Requires DATABASE_URL and the `mercati-energetici` package installed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.market.mgp_service import MGPService
from data.models import Base

logger = structlog.get_logger(__name__)


async def run(days: int) -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("backfill.missing_database_url")
        return 2

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Ensure the mgp_prices table exists.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        service = MGPService(session)
        result = await service.backfill(days=days)

    await engine.dispose()
    logger.info("backfill.done", **result)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill MGP prices")
    parser.add_argument("--days", type=int, default=90, help="Days to backfill (default 90)")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.days)))


if __name__ == "__main__":
    main()
