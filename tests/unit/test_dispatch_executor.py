"""Unit tests for core/dispatch/executor.py — DispatchExecutor."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.dispatch.executor import BatteryCommand, CommandResult, DispatchExecutor


def make_battery_id() -> uuid.UUID:
    return uuid.uuid4()


def make_connector(raise_exc: Exception | None = None, delay: float = 0.0) -> AsyncMock:
    connector = AsyncMock()
    if raise_exc:
        connector.set_power_kw.side_effect = raise_exc
    elif delay:

        async def _slow(*args, **kwargs):
            await asyncio.sleep(delay)

        connector.set_power_kw.side_effect = _slow
    return connector


# ---------------------------------------------------------------------------
# execute_quarter_hour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_returns_success_result() -> None:
    bid = make_battery_id()
    connector = make_connector()
    executor = DispatchExecutor(connector_factory=lambda _: connector)

    cmd = BatteryCommand(battery_id=bid, power_kw=100.0, quarter_hour=4)
    results = await executor.execute_quarter_hour([cmd])

    assert len(results) == 1
    assert results[0].success is True
    assert results[0].battery_id == bid
    assert results[0].latency_ms >= 0.0
    connector.set_power_kw.assert_awaited_once_with(100.0)


@pytest.mark.asyncio
async def test_execute_multiple_commands_all_succeed() -> None:
    ids = [make_battery_id() for _ in range(3)]
    connectors = {bid: make_connector() for bid in ids}
    executor = DispatchExecutor(connector_factory=lambda bid: connectors[bid])

    commands = [BatteryCommand(battery_id=bid, power_kw=50.0, quarter_hour=0) for bid in ids]
    results = await executor.execute_quarter_hour(commands)

    assert all(r.success for r in results)
    assert len(results) == 3


@pytest.mark.asyncio
async def test_execute_returns_error_result_on_exception() -> None:
    bid = make_battery_id()
    connector = make_connector(raise_exc=ConnectionError("unreachable"))
    executor = DispatchExecutor(connector_factory=lambda _: connector)

    cmd = BatteryCommand(battery_id=bid, power_kw=50.0, quarter_hour=1)
    results = await executor.execute_quarter_hour([cmd])

    assert results[0].success is False
    assert "unreachable" in (results[0].error or "")


@pytest.mark.asyncio
async def test_execute_returns_timeout_result_on_timeout() -> None:
    bid = make_battery_id()
    connector = make_connector(delay=5.0)
    executor = DispatchExecutor(connector_factory=lambda _: connector)

    cmd = BatteryCommand(battery_id=bid, power_kw=50.0, quarter_hour=2)
    results = await executor.execute_quarter_hour([cmd], timeout_seconds=0.05)

    assert results[0].success is False
    assert results[0].error == "timeout"


@pytest.mark.asyncio
async def test_execute_empty_command_list() -> None:
    executor = DispatchExecutor(connector_factory=MagicMock())
    results = await executor.execute_quarter_hour([])
    assert results == []


# ---------------------------------------------------------------------------
# is_stale
# ---------------------------------------------------------------------------


def test_is_stale_true_for_unknown_battery() -> None:
    executor = DispatchExecutor(connector_factory=MagicMock())
    assert executor.is_stale(make_battery_id()) is True


@pytest.mark.asyncio
async def test_is_stale_false_immediately_after_command() -> None:
    bid = make_battery_id()
    connector = make_connector()
    executor = DispatchExecutor(connector_factory=lambda _: connector)

    cmd = BatteryCommand(battery_id=bid, power_kw=0.0, quarter_hour=0)
    await executor.execute_quarter_hour([cmd])

    assert executor.is_stale(bid) is False


# ---------------------------------------------------------------------------
# send_safe_state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_safe_state_sends_zero_power() -> None:
    bid = make_battery_id()
    connector = make_connector()
    executor = DispatchExecutor(connector_factory=lambda _: connector)

    await executor.send_safe_state(bid)

    connector.set_power_kw.assert_awaited_once_with(0.0)


@pytest.mark.asyncio
async def test_send_safe_state_handles_failure_without_raising() -> None:
    bid = make_battery_id()
    connector = make_connector(raise_exc=ConnectionError("down"))
    executor = DispatchExecutor(connector_factory=lambda _: connector)

    await executor.send_safe_state(bid)


# ---------------------------------------------------------------------------
# CommandResult dataclass
# ---------------------------------------------------------------------------


def test_command_result_defaults() -> None:
    bid = make_battery_id()
    result = CommandResult(battery_id=bid, success=True)
    assert result.error is None
    assert result.latency_ms == 0.0
