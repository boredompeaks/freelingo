"""Database reset script for FreeLingo.

Drops all PostgreSQL database tables, re-runs Alembic migrations to head,
and clears auth state in Redis.

Usage:
    python backend/scripts/reset_db.py
"""

import asyncio
import os
import sys

# Ensure backend root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from alembic.config import Config
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from app.core.app_logger import get_logger
from app.core.config import settings

logger = get_logger("reset_db")


async def reset_database() -> None:
    logger.info("Starting database reset...")

    # 1. Truncate / Drop all tables in PostgreSQL
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        logger.info("Dropping database schema public CASCADE...")
        await conn.execute(text("DROP SCHEMA public CASCADE;"))
        await conn.execute(text("CREATE SCHEMA public;"))
        await conn.execute(text("GRANT ALL ON SCHEMA public TO public;"))
    await engine.dispose()
    logger.info("PostgreSQL schema successfully cleared.")

    # 2. Run Alembic migrations to head
    logger.info("Running Alembic migrations to head...")
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    alembic_ini_path = os.path.join(backend_dir, "alembic.ini")
    alembic_cfg = Config(alembic_ini_path)
    alembic_cfg.set_main_option("script_location", os.path.join(backend_dir, "alembic"))
    command.upgrade(alembic_cfg, "head")
    logger.info("Alembic migrations completed successfully.")

    # 3. Flush Redis auth and cache keys
    try:
        redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        await redis.flushdb()
        await redis.aclose()
        logger.info("Redis database flushed successfully.")
    except Exception as err:
        logger.warning(f"Could not flush Redis: {err}")

    logger.info("Database reset completed successfully!")


if __name__ == "__main__":
    asyncio.run(reset_database())
