"""Unit tests for core/scheduler.py — MarketScheduler."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time
from unittest.mock import AsyncMock, patch

import pytest

from core.scheduler import TZ_ROME, MarketScheduler

# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduler_start_creates_tasks() -> None:
    scheduler = MarketScheduler()
    with (
        patch.object(scheduler, "_run_mgp_scheduler", new=AsyncMock()),
        patch.object(scheduler, "_run_msd_scheduler", new=AsyncMock()),
        patch.object(scheduler, "_run_telemetry_watchdog", new=AsyncMock()),
    ):
        await scheduler.start()
        assert scheduler._running is True
        assert len(scheduler._tasks) == 3
        await scheduler.stop()


@pytest.mark.asyncio
async def test_scheduler_stop_cancels_tasks() -> None:
    scheduler = MarketScheduler()
    cancelled = []

    async def _slow() -> None:
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    with (
        patch.object(scheduler, "_run_mgp_scheduler", new=_slow),
        patch.object(scheduler, "_run_msd_scheduler", new=_slow),
        patch.object(scheduler, "_run_telemetry_watchdog", new=_slow),
    ):
        await scheduler.start()
        await asyncio.sleep(0)
        await scheduler.stop()

    assert scheduler._running is False
    assert len(cancelled) == 3


@pytest.mark.asyncio
async def test_scheduler_stop_with_no_tasks_is_safe() -> None:
    scheduler = MarketScheduler()
    await scheduler.stop()
    assert scheduler._running is False


# ---------------------------------------------------------------------------
# Public query interface
# ---------------------------------------------------------------------------


def test_get_schedule_returns_none() -> None:
    scheduler = MarketScheduler()
    assert scheduler.get_schedule(date(2025, 6, 1)) is None


def test_get_today_pnl_returns_expected_keys() -> None:
    scheduler = MarketScheduler()
    pnl = scheduler.get_today_pnl()
    assert "realised_eur" in pnl
    assert "projected_eur" in pnl
    assert "status" in pnl


# ---------------------------------------------------------------------------
# trigger_now
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_now_calls_run_optimization_async() -> None:
    mock_run = AsyncMock(return_value="task-123")
    scheduler = MarketScheduler()

    with patch("core.optimizer.run_optimization_async", mock_run):
        await scheduler.trigger_now(delivery_date=date(2025, 6, 1))

    mock_run.assert_awaited_once_with(delivery_date=date(2025, 6, 1))


@pytest.mark.asyncio
async def test_trigger_now_without_date() -> None:
    mock_run = AsyncMock(return_value="task-abc")
    scheduler = MarketScheduler()

    with patch("core.optimizer.run_optimization_async", mock_run):
        await scheduler.trigger_now()

    mock_run.assert_awaited_once_with(delivery_date=None)


@pytest.mark.asyncio
async def test_trigger_now_swallows_exception() -> None:
    scheduler = MarketScheduler()

    with patch(
        "core.optimizer.run_optimization_async", AsyncMock(side_effect=RuntimeError("boom"))
    ):
        await scheduler.trigger_now()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_msd_offers_runs_without_error() -> None:
    scheduler = MarketScheduler()
    await scheduler._submit_msd_offers()


@pytest.mark.asyncio
async def test_check_stale_batteries_runs_without_error() -> None:
    scheduler = MarketScheduler()
    await scheduler._check_stale_batteries()


@pytest.mark.asyncio
async def test_wait_until_next_sleeps_correct_duration() -> None:
    tz = TZ_ROME
    slept: list[float] = []

    async def mock_sleep(seconds: float) -> None:
        slept.append(seconds)

    now = datetime(2025, 6, 1, 10, 0, 0, tzinfo=tz)
    target = time(11, 55)

    with (
        patch("core.scheduler.asyncio.sleep", mock_sleep),
        patch("core.scheduler.datetime") as mock_dt,
    ):
        mock_dt.now.return_value = now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await MarketScheduler._wait_until_next(target, tz=tz)

    assert len(slept) == 1
    assert slept[0] == pytest.approx(6900.0, abs=5)


@pytest.mark.asyncio
async def test_wait_until_next_adds_day_when_past_target() -> None:
    tz = TZ_ROME
    slept: list[float] = []

    async def mock_sleep(seconds: float) -> None:
        slept.append(seconds)

    now = datetime(2025, 6, 1, 12, 30, 0, tzinfo=tz)
    target = time(11, 55)

    with (
        patch("core.scheduler.asyncio.sleep", mock_sleep),
        patch("core.scheduler.datetime") as mock_dt,
    ):
        mock_dt.now.return_value = now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await MarketScheduler._wait_until_next(target, tz=tz)

    assert len(slept) == 1
    assert slept[0] > 0
    assert slept[0] > 60 * 60 * 20


# ---------------------------------------------------------------------------
# Internal loop methods — single iteration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_mgp_scheduler_one_iteration() -> None:
    scheduler = MarketScheduler()
    scheduler._running = True
    mock_run = AsyncMock(return_value="task-id")

    async def _stop_after(*_: object, **__: object) -> None:
        scheduler._running = False

    with (
        patch.object(MarketScheduler, "_wait_until_next", _stop_after),
        patch("core.optimizer.run_optimization_async", mock_run),
    ):
        await scheduler._run_mgp_scheduler()

    mock_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_mgp_scheduler_handles_optimizer_error() -> None:
    scheduler = MarketScheduler()
    scheduler._running = True

    async def _stop_after(*_: object, **__: object) -> None:
        scheduler._running = False

    with (
        patch.object(MarketScheduler, "_wait_until_next", _stop_after),
        patch("core.optimizer.run_optimization_async", AsyncMock(side_effect=RuntimeError("err"))),
    ):
        await scheduler._run_mgp_scheduler()


@pytest.mark.asyncio
async def test_run_msd_scheduler_one_iteration() -> None:
    scheduler = MarketScheduler()
    scheduler._running = True

    async def _stop_after(*_: object, **__: object) -> None:
        scheduler._running = False

    with patch.object(MarketScheduler, "_wait_until_next", _stop_after):
        await scheduler._run_msd_scheduler()


@pytest.mark.asyncio
async def test_run_telemetry_watchdog_one_iteration() -> None:
    scheduler = MarketScheduler()
    scheduler._running = True
    calls = 0

    async def _mock_sleep(_: float) -> None:
        nonlocal calls
        calls += 1
        scheduler._running = False

    with patch("core.scheduler.asyncio.sleep", _mock_sleep):
        await scheduler._run_telemetry_watchdog()

    assert calls == 1
