"""Battery management endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from api.dependencies import CurrentUser, DbSession
from data.models import Battery
from data.schemas import (
    BatteryCreate,
    BatteryListResponse,
    BatteryResponse,
    BatteryUpdate,
    DispatchCommand,
    DispatchCommandResponse,
)

router = APIRouter(prefix="/batteries")


@router.get("", response_model=BatteryListResponse)
async def list_batteries(
    db: DbSession,
    _user: CurrentUser,
    site_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[str | None, Query()] = None,
) -> BatteryListResponse:
    """List all batteries, optionally filtered by site."""
    query = select(Battery).order_by(Battery.battery_id).limit(limit + 1)
    if site_id:
        query = query.where(Battery.site_id == site_id)
    if cursor:
        query = query.where(Battery.battery_id > UUID(cursor))

    result = await db.execute(query)
    batteries = result.scalars().all()

    next_cursor = None
    if len(batteries) > limit:
        batteries = batteries[:limit]
        next_cursor = str(batteries[-1].battery_id)

    return BatteryListResponse(
        data=[BatteryResponse.model_validate(b) for b in batteries],
        meta={"count": len(batteries), "next_cursor": next_cursor},
    )


@router.post("", response_model=BatteryResponse, status_code=status.HTTP_201_CREATED)
async def create_battery(
    payload: BatteryCreate,
    db: DbSession,
    _user: CurrentUser,
) -> BatteryResponse:
    """Register a new battery in the fleet."""
    battery = Battery(**payload.model_dump())
    db.add(battery)
    await db.flush()
    await db.refresh(battery)
    return BatteryResponse.model_validate(battery)


@router.get("/{battery_id}", response_model=BatteryResponse)
async def get_battery(
    battery_id: UUID,
    db: DbSession,
    _user: CurrentUser,
) -> BatteryResponse:
    """Get battery details and current state."""
    battery = await db.get(Battery, battery_id)
    if not battery:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Battery not found")
    return BatteryResponse.model_validate(battery)


@router.patch("/{battery_id}", response_model=BatteryResponse)
async def update_battery(
    battery_id: UUID,
    payload: BatteryUpdate,
    db: DbSession,
    _user: CurrentUser,
) -> BatteryResponse:
    """Update battery configuration."""
    battery = await db.get(Battery, battery_id)
    if not battery:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Battery not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(battery, field, value)

    await db.flush()
    await db.refresh(battery)
    return BatteryResponse.model_validate(battery)


@router.post("/{battery_id}/dispatch", response_model=DispatchCommandResponse)
async def send_dispatch_command(
    battery_id: UUID,
    command: DispatchCommand,
    db: DbSession,
    _user: CurrentUser,
) -> DispatchCommandResponse:
    """Send an immediate dispatch command to a battery (manual override)."""
    battery = await db.get(Battery, battery_id)
    if not battery:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Battery not found")

    if abs(command.power_kw) > battery.max_power_kw:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Requested power {command.power_kw} kW exceeds battery limit {battery.max_power_kw} kW",
        )

    # Dispatch is handled by the connector layer asynchronously
    from connectors.modbus import send_power_setpoint  # lazy import to avoid circular deps

    command_id = await send_power_setpoint(battery, command.power_kw)
    return DispatchCommandResponse(
        command_id=command_id, battery_id=battery_id, power_kw=command.power_kw
    )
