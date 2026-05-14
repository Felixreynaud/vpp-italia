"""Dispatch endpoints — prices, schedule, P&L, backtest, apply."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from sqlalchemy import delete, select

from api.dependencies import CurrentUser, DbSession
from data.models import Battery, DispatchPlan, DispatchSource
from data.schemas import (
    DispatchApplyRequest,
    DispatchApplyResult,
    DispatchPlanCreate,
    DispatchPlanListResponse,
    DispatchPlanResponse,
)

router = APIRouter(prefix="/dispatch")

TZ_ROME = ZoneInfo("Europe/Rome")

# ---------------------------------------------------------------------------
# Existing plan CRUD (unchanged)
# ---------------------------------------------------------------------------


@router.get("/plans", response_model=DispatchPlanListResponse)
async def list_plans(
    db: DbSession,
    _user: CurrentUser,
    delivery_date: Annotated[date | None, Query()] = None,
    battery_id: Annotated[UUID | None, Query()] = None,
) -> DispatchPlanListResponse:
    query = select(DispatchPlan).order_by(DispatchPlan.created_at.desc()).limit(200)
    if delivery_date:
        query = query.where(DispatchPlan.delivery_date == delivery_date.isoformat())
    if battery_id:
        query = query.where(DispatchPlan.battery_id == battery_id)

    result = await db.execute(query)
    plans = result.scalars().all()
    return DispatchPlanListResponse(
        data=[DispatchPlanResponse.model_validate(p) for p in plans],
        meta={"count": len(plans)},
    )


@router.post("/plans", response_model=DispatchPlanResponse, status_code=status.HTTP_201_CREATED)
async def create_plan(
    payload: DispatchPlanCreate,
    db: DbSession,
    _user: CurrentUser,
) -> DispatchPlanResponse:
    plan = DispatchPlan(**payload.model_dump(), source="manual")
    db.add(plan)
    await db.flush()
    await db.refresh(plan)
    return DispatchPlanResponse.model_validate(plan)


@router.get("/plans/{plan_id}", response_model=DispatchPlanResponse)
async def get_plan(
    plan_id: UUID,
    db: DbSession,
    _user: CurrentUser,
) -> DispatchPlanResponse:
    plan = await db.get(DispatchPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    return DispatchPlanResponse.model_validate(plan)


@router.post("/optimize", status_code=status.HTTP_202_ACCEPTED)
async def trigger_optimization(
    _user: CurrentUser,
    delivery_date: Annotated[date | None, Query()] = None,
) -> dict[str, Any]:
    from core.optimizer import run_optimization_async

    task_id = await run_optimization_async(delivery_date=delivery_date)
    return {
        "task_id": task_id,
        "status": "accepted",
        "delivery_date": str(delivery_date or "today+1"),
    }


# ---------------------------------------------------------------------------
# MGP prices
# ---------------------------------------------------------------------------


@router.get("/prices/today")
async def get_prices_today(_user: CurrentUser) -> dict[str, Any]:
    """Return MGP hourly prices for today (24 hours, EUR/MWh)."""
    import os
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from core.market.gme_client import GMEPriceClient

    zone = os.getenv("GME_ZONE", "SUD")
    today = datetime.now(ZoneInfo("Europe/Rome")).date()
    client = GMEPriceClient(zone=zone)
    prices = await client.get_mgp_prices(today)
    pun = await client.get_pun_index(today)

    return {
        "data": {
            "date": str(today),
            "zone": zone,
            "market": "MGP",
            "pun_eur_mwh": round(pun, 2),
            "hourly_prices": {str(h): round(p, 2) for h, p in sorted(prices.items())},
        },
        "meta": {"hours": len(prices)},
    }


@router.get("/prices/tomorrow")
async def get_prices_tomorrow(_user: CurrentUser) -> dict[str, Any]:
    """Return MGP hourly prices for tomorrow (published daily ~13:00 CET)."""
    import os
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from core.market.gme_client import GMEPriceClient

    zone = os.getenv("GME_ZONE", "SUD")
    tomorrow = datetime.now(ZoneInfo("Europe/Rome")).date() + timedelta(days=1)
    client = GMEPriceClient(zone=zone)
    prices = await client.get_mgp_prices(tomorrow)
    pun = await client.get_pun_index(tomorrow)

    return {
        "data": {
            "date": str(tomorrow),
            "zone": zone,
            "market": "MGP",
            "pun_eur_mwh": round(pun, 2),
            "hourly_prices": {str(h): round(p, 2) for h, p in sorted(prices.items())},
            "available": bool(prices),
        },
        "meta": {"hours": len(prices)},
    }


# ---------------------------------------------------------------------------
# Dispatch schedule
# ---------------------------------------------------------------------------


@router.get("/schedule/today")
async def get_schedule_today(_user: CurrentUser) -> dict[str, Any]:
    """Return the optimizer's dispatch schedule for today."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from api.main import _scheduler

    today = datetime.now(ZoneInfo("Europe/Rome")).date()

    if _scheduler is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dispatch scheduler not running",
        )

    schedule = _scheduler.get_schedule(today)
    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No schedule computed yet for {today}. Prices are published at 13:00 CET.",
        )

    return {"data": schedule.to_dict(), "meta": {"status": schedule.status.value}}


@router.post("/schedule/force", status_code=status.HTTP_202_ACCEPTED)
async def force_schedule(
    payload: dict[str, Any],
    _user: CurrentUser,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Trigger an immediate re-optimization and override the current schedule.

    Body: {"delivery_date": "YYYY-MM-DD"} — omit for today+1.
    """
    from api.main import _scheduler

    if _scheduler is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dispatch scheduler not running",
        )

    raw_date = payload.get("delivery_date")
    delivery_date = date.fromisoformat(raw_date) if raw_date else None

    # Run the optimization in the background so the endpoint returns immediately
    background_tasks.add_task(_scheduler.trigger_now, delivery_date)

    return {
        "status": "accepted",
        "delivery_date": str(delivery_date or "tomorrow"),
        "message": "Optimization triggered — check /dispatch/schedule/today in a few seconds.",
    }


# ---------------------------------------------------------------------------
# P&L real-time
# ---------------------------------------------------------------------------


@router.get("/pnl")
async def get_pnl(_user: CurrentUser) -> dict[str, Any]:
    """Return today's realised P&L and projected end-of-day P&L in EUR."""
    from api.main import _scheduler

    if _scheduler is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dispatch scheduler not running",
        )

    pnl = _scheduler.get_today_pnl()
    return {"data": pnl, "meta": {"currency": "EUR"}}


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------


@router.post("/backtest", status_code=status.HTTP_202_ACCEPTED)
async def run_backtest(
    payload: dict[str, Any],
    _user: CurrentUser,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Launch a backtest simulation over a historical period.

    Body:
        {
          "date_start": "2024-01-01",
          "date_end":   "2024-01-31",
          "zone":       "SUD",          // optional, default from env
          "batteries": [                // optional, uses fleet default
            {"battery_id": "B1", "capacity_kwh": 107, "max_power_kw": 108}
          ]
        }

    The backtest runs in the background. Poll GET /dispatch/backtest/{task_id}
    for results (not yet implemented — check logs or use POST for synchronous
    small ranges < 7 days).
    """
    import os
    import uuid as _uuid

    from core.dispatch.backtester import Backtester
    from core.dispatch.models import BatterySpec
    from core.market.gme_client import GMEPriceClient

    try:
        date_start = date.fromisoformat(payload["date_start"])
        date_end = date.fromisoformat(payload["date_end"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid date: {exc}") from exc

    if (date_end - date_start).days > 365:
        raise HTTPException(status_code=422, detail="Backtest range cannot exceed 365 days")

    zone: str = str(payload.get("zone") or os.getenv("GME_ZONE", "SUD"))
    raw_batteries = payload.get("batteries") or []
    batteries = [
        BatterySpec(
            battery_id=b.get("battery_id", f"BAT_{i}"),
            capacity_kwh=float(b.get("capacity_kwh", 107)),
            max_power_kw=float(b.get("max_power_kw", 108)),
        )
        for i, b in enumerate(raw_batteries)
    ] or [BatterySpec(battery_id="DEFAULT", capacity_kwh=107.0, max_power_kw=108.0)]

    task_id = str(_uuid.uuid4())
    gme_client = GMEPriceClient(zone=zone)
    backtester = Backtester(gme_client=gme_client, zone=zone)

    async def _run() -> None:
        try:
            report = await backtester.simulate(date_start, date_end, batteries)
            import structlog as _log

            _log.get_logger("backtest").info(
                "backtest.completed", task_id=task_id, **report.to_summary()
            )
        except Exception as exc:
            import structlog as _log

            _log.get_logger("backtest").error("backtest.failed", task_id=task_id, error=str(exc))

    background_tasks.add_task(_run)

    return {
        "task_id": task_id,
        "status": "accepted",
        "date_start": str(date_start),
        "date_end": str(date_end),
        "days": (date_end - date_start).days + 1,
        "batteries": len(batteries),
        "zone": zone,
    }


# ---------------------------------------------------------------------------
# Apply a 24h optimization plan to all batteries of a site
# ---------------------------------------------------------------------------


@router.post("/apply")
async def apply_dispatch_plan(
    payload: DispatchApplyRequest,
    db: DbSession,
    _user: CurrentUser,
) -> dict[str, Any]:
    """Persist a 24h schedule for every active battery of the given site.

    The schedule is expanded to 96 quarter-hours (4 QH per hour, same power_kw).
    Total site power is divided equally across active batteries.

    Existing plans for the same (site, today) are wiped first so each call
    is idempotent — re-applying the same optimisation replaces the previous one.

    The background DispatchApplier worker (started in the API lifespan) will
    then poll the dispatch_plans table every 60 s and translate the QH for
    the current time into a Huawei charge/discharge command per battery.
    """
    bats = await db.execute(
        select(Battery).where(Battery.site_id == payload.site_id).where(Battery.is_active.is_(True))
    )
    batteries = list(bats.scalars().all())
    if not batteries:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active batteries for site {payload.site_id}",
        )

    today_iso = datetime.now(TZ_ROME).date().isoformat()
    battery_ids = [b.battery_id for b in batteries]

    # Wipe previous plans for these batteries today (replace fully)
    await db.execute(
        delete(DispatchPlan)
        .where(DispatchPlan.delivery_date == today_iso)
        .where(DispatchPlan.battery_id.in_(battery_ids))
    )

    # Translate 24 hourly slots → 96 QH × N batteries
    try:
        source_enum = DispatchSource(payload.source)
    except ValueError:
        source_enum = DispatchSource.MANUAL

    per_bat = 1.0 / len(batteries)
    plans_saved = 0
    for slot in payload.schedule:
        per_battery_kw = float(slot.power_kw) * per_bat
        for qh_in_hour in range(4):
            qh = slot.hour * 4 + qh_in_hour
            for battery in batteries:
                db.add(
                    DispatchPlan(
                        battery_id=battery.battery_id,
                        delivery_date=today_iso,
                        quarter_hour=qh,
                        power_kw=Decimal(str(round(per_battery_kw, 2))),
                        source=source_enum,
                    )
                )
                plans_saved += 1

    await db.flush()

    result = DispatchApplyResult(
        success=True,
        message=f"Plan saved for {len(batteries)} battery/ies × 96 QH",
        applied_at=datetime.now(UTC),
        plans_saved=plans_saved,
        batteries_targeted=len(batteries),
    )

    return {
        "data": result.model_dump(mode="json"),
        "meta": {
            "site_id": str(payload.site_id),
            "delivery_date": today_iso,
            "source": source_enum.value,
        },
    }
