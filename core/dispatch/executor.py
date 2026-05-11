"""Dispatch execution engine — translates plans into connector commands.

Moved from core/dispatch.py into the core.dispatch package.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import structlog

logger = structlog.get_logger(__name__)

SAFE_STATE_TIMEOUT_SECONDS = 30


@dataclass
class BatteryCommand:
    battery_id: UUID
    power_kw: float
    quarter_hour: int
    plan_id: UUID | None = None


@dataclass
class CommandResult:
    battery_id: UUID
    success: bool
    error: str | None = None
    latency_ms: float = 0.0


class DispatchExecutor:
    """Sends setpoint commands to all batteries for a given QH."""

    def __init__(self, connector_factory: Callable) -> None:
        self._connector_factory = connector_factory
        self._last_contact: dict[UUID, datetime] = {}

    async def execute_quarter_hour(
        self, commands: list[BatteryCommand], timeout_seconds: float = 30.0
    ) -> list[CommandResult]:
        tasks = [self._send_command(cmd, timeout_seconds) for cmd in commands]
        return await asyncio.gather(*tasks, return_exceptions=False)

    async def _send_command(self, command: BatteryCommand, timeout: float) -> CommandResult:
        import time

        t0 = time.perf_counter()
        try:
            connector = self._connector_factory(command.battery_id)
            async with asyncio.timeout(timeout):
                await connector.set_power_kw(command.power_kw)
            self._last_contact[command.battery_id] = datetime.now(UTC)
            latency = (time.perf_counter() - t0) * 1000
            logger.info(
                "dispatch.command_sent",
                battery_id=str(command.battery_id),
                power_kw=command.power_kw,
                latency_ms=round(latency, 1),
            )
            return CommandResult(
                battery_id=command.battery_id, success=True, latency_ms=round(latency, 1)
            )
        except TimeoutError:
            logger.error("dispatch.command_timeout", battery_id=str(command.battery_id))
            return CommandResult(battery_id=command.battery_id, success=False, error="timeout")
        except Exception as e:
            logger.error(
                "dispatch.command_error", battery_id=str(command.battery_id), error=str(e)
            )
            return CommandResult(battery_id=command.battery_id, success=False, error=str(e))

    def is_stale(self, battery_id: UUID) -> bool:
        last = self._last_contact.get(battery_id)
        if last is None:
            return True
        return (datetime.now(UTC) - last).total_seconds() > SAFE_STATE_TIMEOUT_SECONDS

    async def send_safe_state(self, battery_id: UUID) -> None:
        safe_cmd = BatteryCommand(battery_id=battery_id, power_kw=0.0, quarter_hour=-1)
        result = await self._send_command(safe_cmd, timeout=10.0)
        if result.success:
            logger.warning("dispatch.safe_state_applied", battery_id=str(battery_id))
        else:
            logger.critical(
                "dispatch.safe_state_failed", battery_id=str(battery_id), error=result.error
            )
