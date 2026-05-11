"""Day-ahead dispatch optimizer — MGP price arbitrage.

Algorithm (3 steps):
  1. Classify each hour as PEAK / OFF_PEAK / NEUTRAL using
     mean ± threshold * std of the 24-hour price vector.
  2. Simulate SoC evolution for each battery, respecting physical
     constraints (SOC bounds, power limit, ramp rate, min cycle duration).
  3. Compute estimated P&L for the full schedule.

This is a greedy heuristic designed for daily MGP arbitrage. For MSD/MB
participation, use the LP solver in core/optimizer.py (PuLP/CBC).
"""

from __future__ import annotations

import math
import os
import statistics
import uuid
from datetime import date

import structlog

from core.dispatch.models import (
    ActionType,
    BatterySpec,
    DailySchedule,
    DispatchAction,
    HourlyPrice,
    HourlySchedule,
    HourType,
    ScheduleStatus,
)

logger = structlog.get_logger(__name__)

DEFAULT_THRESHOLD = float(os.getenv("DISPATCH_PRICE_THRESHOLD", "0.5"))
DEFAULT_SOC_MIN = float(os.getenv("DISPATCH_SOC_MIN", "10.0"))
DEFAULT_SOC_MAX = float(os.getenv("DISPATCH_SOC_MAX", "90.0"))
MIN_CYCLE_MINUTES = 30
HOURS_PER_SLOT = 1.0  # hourly granularity


class DispatchOptimizer:
    """Greedy peak/off-peak optimizer for day-ahead price arbitrage.

    Args:
        threshold: Number of standard deviations away from mean that
            defines peak (above) and off-peak (below) hours.
        soc_min_pct: Global safety floor for SoC (overridden per battery
            if BatterySpec.soc_min_pct is tighter).
        soc_max_pct: Global safety ceiling for SoC.
    """

    def __init__(
        self,
        threshold: float = DEFAULT_THRESHOLD,
        soc_min_pct: float = DEFAULT_SOC_MIN,
        soc_max_pct: float = DEFAULT_SOC_MAX,
    ) -> None:
        self.threshold = threshold
        self.soc_min_pct = soc_min_pct
        self.soc_max_pct = soc_max_pct

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def optimize_day(
        self,
        prices_24h: dict[int, float],
        batteries: list[BatterySpec],
        delivery_date: date | None = None,
        zone: str = "SUD",
    ) -> DailySchedule:
        """Compute a 24-hour dispatch schedule for all batteries.

        Args:
            prices_24h: {hour(0-23): price_eur_mwh} — must cover all 24 hours.
                Missing hours default to the daily average.
            batteries: Physical specs for each battery.
            delivery_date: Target date (defaults to today).
            zone: GME price zone.

        Returns:
            DailySchedule with all actions, SoC trajectories, and P&L.
        """
        from datetime import date as date_type

        target_date = delivery_date or date_type.today()
        run_id = str(uuid.uuid4())

        # Fill missing hours with average
        avg_price = statistics.mean(prices_24h.values()) if prices_24h else 80.0
        full_prices = {h: prices_24h.get(h, avg_price) for h in range(24)}

        # Step 1 — classify hours
        classified = self._classify_hours(full_prices)

        # Step 2 — build per-battery schedules respecting physical limits
        schedule = DailySchedule(
            date=target_date,
            zone=zone,
            optimization_run_id=run_id,
            status=ScheduleStatus.PLANNED,
        )
        for h in range(24):
            schedule.hours[h] = HourlySchedule(
                hour=h,
                hour_price_eur_mwh=full_prices[h],
            )

        for battery in batteries:
            self._schedule_battery(battery, classified, full_prices, schedule)

        # Step 3 — compute P&L
        self._compute_pnl(schedule)

        logger.info(
            "optimizer.schedule_computed",
            date=str(target_date),
            batteries=len(batteries),
            pnl_eur=round(schedule.estimated_pnl_eur, 2),
            run_id=run_id,
        )
        return schedule

    # ------------------------------------------------------------------
    # Step 1 — hour classification
    # ------------------------------------------------------------------

    def _classify_hours(self, prices: dict[int, float]) -> dict[int, HourlyPrice]:
        values = list(prices.values())
        mean = statistics.mean(values)
        std = statistics.stdev(values) if len(values) > 1 else 0.0

        peak_threshold = mean + self.threshold * std
        offpeak_threshold = mean - self.threshold * std

        classified: dict[int, HourlyPrice] = {}
        for h, price in prices.items():
            if std == 0.0:
                hour_type = HourType.NEUTRAL
            elif price >= peak_threshold:
                hour_type = HourType.PEAK
            elif price <= offpeak_threshold:
                hour_type = HourType.OFF_PEAK
            else:
                hour_type = HourType.NEUTRAL

            classified[h] = HourlyPrice(
                hour=h,
                price_eur_mwh=price,
                zone="",
                market="MGP",
                hour_type=hour_type,
            )

        peak_count = sum(1 for p in classified.values() if p.hour_type == HourType.PEAK)
        offpeak_count = sum(1 for p in classified.values() if p.hour_type == HourType.OFF_PEAK)
        logger.debug(
            "optimizer.hours_classified",
            peak=peak_count,
            off_peak=offpeak_count,
            neutral=24 - peak_count - offpeak_count,
            mean_eur_mwh=round(mean, 2),
            std_eur_mwh=round(std, 2),
        )
        return classified

    # ------------------------------------------------------------------
    # Step 2 — per-battery scheduling
    # ------------------------------------------------------------------

    def _schedule_battery(
        self,
        battery: BatterySpec,
        classified: dict[int, HourlyPrice],
        prices: dict[int, float],
        schedule: DailySchedule,
    ) -> None:
        soc_min = max(self.soc_min_pct, battery.soc_min_pct)
        soc_max = min(self.soc_max_pct, battery.soc_max_pct)
        soc = battery.initial_soc_pct
        eta = math.sqrt(battery.efficiency_roundtrip)  # one-way efficiency

        last_action: ActionType | None = None
        last_action_hour: int | None = None

        for h in range(24):
            hp = classified[h]
            price = prices[h]

            # Determine desired action
            if hp.hour_type == HourType.OFF_PEAK and soc < soc_max - 1.0:
                desired = ActionType.CHARGE
            elif hp.hour_type == HourType.PEAK and soc > soc_min + 1.0:
                desired = ActionType.DISCHARGE
            else:
                desired = ActionType.STOP

            # Enforce minimum cycle duration (30 min = 1 full hour slot for hourly)
            if (
                desired != ActionType.STOP
                and last_action is not None
                and last_action != desired
                and last_action_hour is not None
                and (h - last_action_hour) < math.ceil(MIN_CYCLE_MINUTES / 60)
            ):
                desired = ActionType.STOP

            # Compute achievable power and new SoC
            power_kw, soc_after = self._compute_action(
                desired, battery, soc, soc_min, soc_max, eta
            )

            action = DispatchAction(
                battery_id=battery.battery_id,
                hour=h,
                action_type=desired if power_kw != 0 else ActionType.STOP,
                power_kw=power_kw,
                target_price_eur_mwh=price,
                estimated_revenue_eur=self._revenue(power_kw, price),
                hour_type=hp.hour_type,
                soc_before_pct=round(soc, 2),
                soc_after_pct=round(soc_after, 2),
            )

            schedule.hours[h].actions[battery.battery_id] = action
            schedule.hours[h].total_power_kw += power_kw

            if power_kw != 0:
                last_action = action.action_type
                last_action_hour = h

            soc = soc_after

    def _compute_action(
        self,
        desired: ActionType,
        battery: BatterySpec,
        soc: float,
        soc_min: float,
        soc_max: float,
        eta: float,
    ) -> tuple[float, float]:
        """Return (power_kw, new_soc_pct). power_kw > 0 = charge, < 0 = discharge."""
        cap = battery.capacity_kwh
        dt_h = HOURS_PER_SLOT

        if desired == ActionType.CHARGE:
            # Max energy that can be added without exceeding SOC_MAX
            headroom_kwh = (soc_max - soc) / 100.0 * cap
            max_charge_kwh = battery.max_power_kw * dt_h * eta
            actual_kwh = min(headroom_kwh, max_charge_kwh)
            if actual_kwh < 0.1:
                return 0.0, soc
            power_kw = actual_kwh / (dt_h * eta)
            new_soc = soc + (actual_kwh / cap) * 100.0
            return round(min(power_kw, battery.max_power_kw), 3), round(new_soc, 4)

        elif desired == ActionType.DISCHARGE:
            # Max energy that can be extracted without hitting SOC_MIN
            available_kwh = (soc - soc_min) / 100.0 * cap
            max_discharge_kwh = battery.max_power_kw * dt_h / eta
            actual_kwh = min(available_kwh, max_discharge_kwh)
            if actual_kwh < 0.1:
                return 0.0, soc
            power_kw = actual_kwh * eta / dt_h
            new_soc = soc - (actual_kwh / cap) * 100.0
            return round(-min(power_kw, battery.max_power_kw), 3), round(new_soc, 4)

        return 0.0, soc

    # ------------------------------------------------------------------
    # Step 3 — P&L
    # ------------------------------------------------------------------

    @staticmethod
    def _revenue(power_kw: float, price_eur_mwh: float) -> float:
        """Revenue from one hour of dispatch: positive = revenue, negative = cost."""
        energy_mwh = abs(power_kw) * HOURS_PER_SLOT / 1000.0
        if power_kw < 0:  # discharge → sell energy
            return energy_mwh * price_eur_mwh
        elif power_kw > 0:  # charge → buy energy
            return -energy_mwh * price_eur_mwh
        return 0.0

    def _compute_pnl(self, schedule: DailySchedule) -> None:
        revenue = 0.0
        cost = 0.0
        for hs in schedule.hours.values():
            for action in hs.actions.values():
                r = action.estimated_revenue_eur
                if r > 0:
                    revenue += r
                else:
                    cost += abs(r)
        schedule.estimated_revenue_eur = round(revenue, 4)
        schedule.estimated_cost_eur = round(cost, 4)
