"""Dispatch plan endpoints."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from typing import Annotated

from api.dependencies import CurrentUser, DbSession
from data.models import DispatchPlan
from data.schemas import DispatchPlanCreate, DispatchPlanResponse, DispatchPlanListResponse

router = APIRouter(prefix="/dispatch")


@router.get("/plans", response_model=DispatchPlanListResponse)
async def list_plans(
    db: DbSession,
    _user: CurrentUser,
    delivery_date: Annotated[date | None, Query()] = None,
    battery_id: Annotated[UUID | None, Query()] = None,
) -> DispatchPlanListResponse:
    """List dispatch plans, optionally filtered by date or battery."""
    query = select(DispatchPlan).order_by(DispatchPlan.created_at.desc()).limit(200)
    if delivery_date:
        query = query.where(DispatchPlan.delivery_date == delivery_date)
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
    """Create a manual dispatch plan (overrides optimizer output)."""
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
) -> dict:
    """Trigger an on-demand optimization run for the given delivery date."""
    from core.optimizer import run_optimization_async

    task_id = await run_optimization_async(delivery_date=delivery_date)
    return {"task_id": task_id, "status": "accepted", "delivery_date": str(delivery_date or "today+1")}
