"""Admin endpoints for battery aggregates — admin role required.

Routes:
    GET    /api/v1/admin/aggregates                list all aggregates with their batteries
    POST   /api/v1/admin/aggregates                create a new aggregate
    GET    /api/v1/admin/aggregates/{id}           detail of one aggregate
    PATCH  /api/v1/admin/aggregates/{id}           update name / strategy / market / zone
    DELETE /api/v1/admin/aggregates/{id}           delete aggregate (batteries become unassigned)
    PATCH  /api/v1/admin/batteries/{id}/aggregate  assign a battery to an aggregate
                                                    (or remove with {"aggregate_id": null})

Exclusive membership is guaranteed by the FK column Battery.aggregate_id —
a battery cannot be in two aggregates at once at the DB level.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from api.dependencies import DbSession, require_admin
from data.models import Aggregate, Battery
from data.schemas import (
    AggregateCreate,
    AggregateListResponse,
    AggregateResponse,
    AggregateUpdate,
    BatteryAggregateAssignment,
)

logger = structlog.get_logger(__name__)

router = APIRouter()


def _parse_uuid(raw: str, field: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid {field}"
        ) from None


# ---------------------------------------------------------------------------
# GET /api/v1/admin/aggregates
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/admin/aggregates",
    response_model=AggregateListResponse,
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)
async def list_aggregates(session: DbSession) -> Any:
    """Return every aggregate with its (eagerly-loaded) batteries."""
    result = await session.execute(
        select(Aggregate)
        .options(selectinload(Aggregate.batteries))
        .order_by(Aggregate.created_at.desc())
    )
    aggregates = result.scalars().all()
    return {
        "data": [AggregateResponse.model_validate(a, from_attributes=True) for a in aggregates],
        "meta": {"count": len(aggregates)},
    }


# ---------------------------------------------------------------------------
# POST /api/v1/admin/aggregates
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/admin/aggregates",
    response_model=AggregateResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)
async def create_aggregate(payload: AggregateCreate, session: DbSession) -> Any:
    existing = await session.execute(select(Aggregate).where(Aggregate.name == payload.name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An aggregate with this name already exists",
        )

    aggregate = Aggregate(**payload.model_dump())
    session.add(aggregate)
    await session.commit()
    refreshed = await session.execute(
        select(Aggregate)
        .options(selectinload(Aggregate.batteries))
        .where(Aggregate.aggregate_id == aggregate.aggregate_id)
    )
    aggregate = refreshed.scalar_one()
    logger.info("admin.aggregate_created", aggregate_id=str(aggregate.aggregate_id))
    return AggregateResponse.model_validate(aggregate, from_attributes=True)


# ---------------------------------------------------------------------------
# GET /api/v1/admin/aggregates/{id}
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/admin/aggregates/{aggregate_id}",
    response_model=AggregateResponse,
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)
async def get_aggregate(aggregate_id: str, session: DbSession) -> Any:
    pk = _parse_uuid(aggregate_id, "aggregate_id")
    result = await session.execute(
        select(Aggregate)
        .options(selectinload(Aggregate.batteries))
        .where(Aggregate.aggregate_id == pk)
    )
    aggregate = result.scalar_one_or_none()
    if aggregate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aggregate not found")
    return AggregateResponse.model_validate(aggregate, from_attributes=True)


# ---------------------------------------------------------------------------
# PATCH /api/v1/admin/aggregates/{id}
# ---------------------------------------------------------------------------


@router.patch(
    "/api/v1/admin/aggregates/{aggregate_id}",
    response_model=AggregateResponse,
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)
async def update_aggregate(aggregate_id: str, payload: AggregateUpdate, session: DbSession) -> Any:
    pk = _parse_uuid(aggregate_id, "aggregate_id")
    aggregate = await session.get(Aggregate, pk)
    if aggregate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aggregate not found")

    if payload.name is not None and payload.name != aggregate.name:
        # Enforce unique name on update too.
        dup = await session.execute(
            select(Aggregate).where(Aggregate.name == payload.name, Aggregate.aggregate_id != pk)
        )
        if dup.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An aggregate with this name already exists",
            )
        aggregate.name = payload.name

    if payload.description is not None:
        aggregate.description = payload.description
    if payload.strategy_type is not None:
        aggregate.strategy_type = payload.strategy_type
    if payload.target_market is not None:
        aggregate.target_market = payload.target_market
    if payload.target_zone is not None:
        aggregate.target_zone = payload.target_zone
    if payload.is_active is not None:
        aggregate.is_active = payload.is_active

    await session.commit()
    # Re-query with eager load to avoid lazy-loading after commit (greenlet error).
    refreshed = await session.execute(
        select(Aggregate)
        .options(selectinload(Aggregate.batteries))
        .where(Aggregate.aggregate_id == pk)
    )
    aggregate = refreshed.scalar_one()
    logger.info("admin.aggregate_updated", aggregate_id=str(aggregate.aggregate_id))
    return AggregateResponse.model_validate(aggregate, from_attributes=True)


# ---------------------------------------------------------------------------
# DELETE /api/v1/admin/aggregates/{id}
# ---------------------------------------------------------------------------


@router.delete(
    "/api/v1/admin/aggregates/{aggregate_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)
async def delete_aggregate(aggregate_id: str, session: DbSession) -> None:
    """Delete the aggregate. Member batteries become 'unassigned'
    (`aggregate_id = NULL`) automatically via the FK ON DELETE SET NULL.
    """
    pk = _parse_uuid(aggregate_id, "aggregate_id")
    aggregate = await session.get(Aggregate, pk)
    if aggregate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aggregate not found")

    await session.delete(aggregate)
    await session.commit()
    logger.info("admin.aggregate_deleted", aggregate_id=str(pk))


# ---------------------------------------------------------------------------
# PATCH /api/v1/admin/batteries/{id}/aggregate
# ---------------------------------------------------------------------------


@router.patch(
    "/api/v1/admin/batteries/{battery_id}/aggregate",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)
async def assign_battery_to_aggregate(
    battery_id: str, payload: BatteryAggregateAssignment, session: DbSession
) -> dict[str, Any]:
    """Assign or unassign a battery to/from an aggregate.

    Body `{"aggregate_id": "<uuid>"}` to assign.
    Body `{"aggregate_id": null}` to unassign.
    """
    bpk = _parse_uuid(battery_id, "battery_id")
    battery = await session.get(Battery, bpk)
    if battery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Battery not found")

    if not battery.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Battery is not under management (is_active=False); cannot assign to an aggregate",
        )

    if payload.aggregate_id is not None:
        aggregate = await session.get(Aggregate, payload.aggregate_id)
        if aggregate is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aggregate not found")
        battery.aggregate_id = payload.aggregate_id
    else:
        battery.aggregate_id = None

    await session.commit()
    logger.info(
        "admin.battery_aggregate_assigned",
        battery_id=str(bpk),
        aggregate_id=str(payload.aggregate_id) if payload.aggregate_id else None,
    )
    return {
        "battery_id": str(battery.battery_id),
        "aggregate_id": str(battery.aggregate_id) if battery.aggregate_id else None,
    }
