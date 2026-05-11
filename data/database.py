"""Database utilities and TimescaleDB hypertable setup."""

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

TIMESCALE_HYPERTABLE_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables
        WHERE hypertable_name = 'battery_readings'
    ) THEN
        PERFORM create_hypertable('battery_readings', 'time', chunk_time_interval => INTERVAL '1 day');
        PERFORM add_retention_policy('battery_readings', INTERVAL '90 days');
    END IF;
END
$$;
"""

TIMESCALE_COMPRESSION_SQL = """
ALTER TABLE battery_readings SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'battery_id',
    timescaledb.compress_orderby = 'time DESC'
);
SELECT add_compression_policy('battery_readings', INTERVAL '7 days');
"""


async def setup_timescaledb(session: AsyncSession) -> None:
    """Configure TimescaleDB hypertable and retention policies after table creation."""
    try:
        await session.execute(text(TIMESCALE_HYPERTABLE_SQL))
        await session.commit()
        logger.info("timescaledb.hypertable_configured")
    except Exception as e:
        logger.warning("timescaledb.setup_skipped", reason=str(e))
        await session.rollback()


async def get_latest_readings(session: AsyncSession, battery_ids: list[str]) -> list[dict]:
    """Fetch the most recent reading for each battery (uses TimescaleDB last() aggregate)."""
    sql = text("""
        SELECT DISTINCT ON (battery_id)
            battery_id,
            time,
            soc_percent,
            power_kw,
            voltage_v,
            temperature_c,
            state
        FROM battery_readings
        WHERE battery_id = ANY(:battery_ids)
          AND time > NOW() - INTERVAL '5 minutes'
        ORDER BY battery_id, time DESC
    """)
    result = await session.execute(sql, {"battery_ids": battery_ids})
    return [dict(row._mapping) for row in result]


async def get_aggregate_readings(
    session: AsyncSession,
    battery_id: str,
    start: str,
    end: str,
    bucket: str = "15 minutes",
) -> list[dict]:
    """Time-bucketed aggregate — uses TimescaleDB time_bucket for efficiency."""
    sql = text("""
        SELECT
            time_bucket(:bucket, time) AS bucket,
            battery_id,
            avg(soc_percent)  AS avg_soc,
            avg(power_kw)     AS avg_power_kw,
            min(power_kw)     AS min_power_kw,
            max(power_kw)     AS max_power_kw,
            avg(temperature_c) AS avg_temp_c
        FROM battery_readings
        WHERE battery_id = :battery_id
          AND time BETWEEN :start AND :end
        GROUP BY bucket, battery_id
        ORDER BY bucket ASC
    """)
    result = await session.execute(
        sql, {"battery_id": battery_id, "start": start, "end": end, "bucket": bucket}
    )
    return [dict(row._mapping) for row in result]
