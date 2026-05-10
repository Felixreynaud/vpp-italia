"""LP/MILP dispatch optimizer using PuLP.

Objective: maximize revenue from MSD/MB markets while respecting
battery physical constraints (SoC, ramp rates, power limits).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Sequence

import pulp
import structlog

logger = structlog.get_logger(__name__)

N_QH = 96  # Quarter-hours per day


@dataclass
class BatteryParams:
    battery_id: str
    capacity_kwh: float
    max_power_kw: float
    min_soc_percent: float = 10.0
    max_soc_percent: float = 90.0
    ramp_rate_kw_per_min: float | None = None
    initial_soc_percent: float = 50.0
    efficiency_roundtrip: float = 0.92


@dataclass
class MarketSignal:
    quarter_hour: int
    price_eur_mwh: float
    direction: str  # "UP" | "DOWN" | "BOTH"
    max_capacity_mw: float | None = None


@dataclass
class DispatchResult:
    run_id: str
    delivery_date: str
    battery_id: str
    power_schedule_kw: list[float] = field(default_factory=list)
    soc_schedule_percent: list[float] = field(default_factory=list)
    expected_revenue_eur: float = 0.0
    solve_status: str = "unknown"
    solve_time_seconds: float = 0.0


def optimize_battery(
    battery: BatteryParams,
    market_signals: Sequence[MarketSignal],
    delivery_date: date | None = None,
) -> DispatchResult:
    """Solve the LP for a single battery over 96 QH."""
    run_id = str(uuid.uuid4())
    d = delivery_date or date.today()

    prob = pulp.LpProblem(f"vpp_dispatch_{battery.battery_id}_{d}", pulp.LpMaximize)

    dt_h = 0.25  # QH duration in hours
    cap_kwh = battery.capacity_kwh
    p_max = battery.max_power_kw
    soc_min = battery.min_soc_percent / 100.0
    soc_max = battery.max_soc_percent / 100.0
    eta = battery.efficiency_roundtrip ** 0.5  # one-way efficiency

    # Decision variables
    p_dis = [pulp.LpVariable(f"p_dis_{t}", lowBound=0, upBound=p_max) for t in range(N_QH)]
    p_ch = [pulp.LpVariable(f"p_ch_{t}", lowBound=0, upBound=p_max) for t in range(N_QH)]
    soc = [pulp.LpVariable(f"soc_{t}", lowBound=soc_min, upBound=soc_max) for t in range(N_QH + 1)]

    # Initial SoC
    prob += soc[0] == battery.initial_soc_percent / 100.0

    # SoC dynamics
    for t in range(N_QH):
        prob += soc[t + 1] == soc[t] + (eta * p_ch[t] - p_dis[t] / eta) * dt_h / cap_kwh

    # Ramp rate constraint
    if battery.ramp_rate_kw_per_min is not None:
        ramp_max = battery.ramp_rate_kw_per_min * 15  # max delta over one QH
        for t in range(1, N_QH):
            net_t = p_dis[t] - p_ch[t]
            net_tm1 = p_dis[t - 1] - p_ch[t - 1]
            prob += net_t - net_tm1 <= ramp_max
            prob += net_tm1 - net_t <= ramp_max

    # Objective: maximize revenue
    signal_map = {s.quarter_hour: s for s in market_signals}
    revenue_terms = []
    for t in range(N_QH):
        sig = signal_map.get(t)
        if sig:
            price = sig.price_eur_mwh / 1000.0  # EUR/kWh
            if sig.direction in ("UP", "BOTH"):
                revenue_terms.append(price * p_dis[t] * dt_h)
            if sig.direction in ("DOWN", "BOTH"):
                revenue_terms.append(price * p_ch[t] * dt_h)

    prob += pulp.lpSum(revenue_terms)

    import time

    t0 = time.perf_counter()
    solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=60)
    prob.solve(solver)
    solve_time = time.perf_counter() - t0

    status = pulp.LpStatus[prob.status]
    logger.info(
        "optimizer.solved",
        battery_id=battery.battery_id,
        status=status,
        solve_time=round(solve_time, 3),
        run_id=run_id,
    )

    power_schedule = []
    soc_schedule = []
    revenue = 0.0

    if prob.status == pulp.LpStatusOptimal:
        for t in range(N_QH):
            p = pulp.value(p_dis[t]) - pulp.value(p_ch[t])
            power_schedule.append(round(p, 3))
            soc_schedule.append(round(pulp.value(soc[t]) * 100, 2))
            sig = signal_map.get(t)
            if sig:
                revenue += abs(p) * dt_h * sig.price_eur_mwh / 1000.0
        soc_schedule.append(round(pulp.value(soc[N_QH]) * 100, 2))

    return DispatchResult(
        run_id=run_id,
        delivery_date=d.isoformat(),
        battery_id=battery.battery_id,
        power_schedule_kw=power_schedule,
        soc_schedule_percent=soc_schedule,
        expected_revenue_eur=round(revenue, 4),
        solve_status=status,
        solve_time_seconds=round(solve_time, 3),
    )


async def run_optimization_async(delivery_date: date | None = None) -> str:
    """Entry point called by the API — runs optimizer in a thread pool."""
    import asyncio

    task_id = str(uuid.uuid4())
    logger.info("optimizer.task_queued", task_id=task_id, delivery_date=str(delivery_date))
    # Full implementation: fetch batteries + market signals from DB, run optimize_battery()
    # for each, persist DispatchPlan rows, then return task_id for async polling.
    asyncio.ensure_future(_run_optimization_task(task_id, delivery_date))
    return task_id


async def _run_optimization_task(task_id: str, delivery_date: date | None) -> None:
    import asyncio

    logger.info("optimizer.task_started", task_id=task_id)
    # Placeholder — full implementation fetches data from DB and runs the LP
    await asyncio.sleep(0)
    logger.info("optimizer.task_completed", task_id=task_id)
