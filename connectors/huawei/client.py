"""Huawei SmartPVMS NBI v24.6 — battery client.

Read operations use the /thirdData/ legacy endpoints (token as xsrf-token header).
Control operations use the /rest/openapi/pvms/nbi/ endpoints (Bearer token).

Rate limit on real-time data: 1 request per 5 minutes per plant, strictly
enforced by the Huawei platform (failCode 407). This client enforces the same
limit locally to avoid burning quota.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import structlog

from .auth import HuaweiAuthClient
from .exceptions import HuaweiAPIError, HuaweiAuthError, HuaweiTaskError
from .models import (
    BatteryDevType,
    DispatchSwitch,
    HuaweiBatteryStatus,
    HuaweiDevice,
    HuaweiDispatchTask,
    HuaweiPlant,
    TaskStatus,
)

logger = structlog.get_logger(__name__)

# Huawei's documented minimum interval between real-time data calls (seconds)
REALTIME_MIN_INTERVAL = 300
# Maximum wait for an async task to complete
TASK_POLL_INTERVAL = 5.0
TASK_MAX_WAIT = 60.0


class HuaweiBatteryClient:
    """Full-featured client for Huawei SmartPVMS NBI battery operations.

    Usage:
        client = HuaweiBatteryClient.from_env()
        plants = await client.get_plant_list()
        status = await client.get_battery_realtime([device_id])
        task = await client.charge(plant_code, power_w=50_000)
        final = await client.wait_for_task(task.request_id, plant_code)
    """

    def __init__(self, domain: str, auth: HuaweiAuthClient) -> None:
        self._domain = domain
        self._auth = auth
        # Last call timestamps per plant for rate-limit enforcement
        self._last_realtime_call: dict[str, float] = {}
        self._pending_tasks: dict[str, str] = {}  # plant_code → request_id

    @classmethod
    def from_env(cls) -> HuaweiBatteryClient:
        import os

        domain = os.environ["HUAWEI_DOMAIN"]
        auth = HuaweiAuthClient(
            domain=domain,
            client_id=os.environ["HUAWEI_CLIENT_ID"],
            client_secret=os.environ["HUAWEI_CLIENT_SECRET"],
        )
        return cls(domain=domain, auth=auth)

    # ------------------------------------------------------------------
    # URLs
    # ------------------------------------------------------------------

    def _third_data_url(self, path: str) -> str:
        return f"https://{self._domain}/thirdData/{path}"

    def _nbi_url(self, path: str) -> str:
        return f"https://{self._domain}/rest/openapi/pvms/{path}"

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _post_third_data(self, path: str, payload: dict) -> Any:
        """POST to /thirdData/ — uses xsrf-token header."""
        token = await self._auth.get_token()
        url = self._third_data_url(path)
        return await self._post(url, payload, headers={"xsrf-token": token})

    async def _post_nbi(self, path: str, payload: dict) -> Any:
        """POST to /rest/openapi/pvms/ — uses Bearer token."""
        token = await self._auth.get_token()
        url = self._nbi_url(path)
        return await self._post(url, payload, headers={"Authorization": f"Bearer {token}"})

    async def _post(self, url: str, payload: dict, headers: dict) -> Any:
        headers = {"Content-Type": "application/json", **headers}
        try:
            async with httpx.AsyncClient(timeout=30.0) as http:
                resp = await http.post(url, json=payload, headers=headers)
        except httpx.RequestError as exc:
            raise HuaweiAPIError(f"Network error: {exc}") from exc

        return await self._handle_response(resp)

    async def _handle_response(self, resp: httpx.Response) -> Any:
        if resp.status_code == 401:
            self._auth.invalidate()
            raise HuaweiAuthError("HTTP 401 from Huawei API — credentials rejected", fail_code=401)

        body: dict = {}
        try:
            body = resp.json()
        except Exception:
            resp.raise_for_status()

        fail_code = body.get("failCode") or body.get("code")
        if fail_code is not None:
            fail_code = int(fail_code)
            if fail_code not in (0, 200):
                if fail_code == 305:
                    self._auth.invalidate()
                    raise HuaweiAuthError("Session expired (305)", fail_code=305)
                raise HuaweiAPIError.from_fail_code(
                    fail_code, detail=str(body.get("message") or "")
                )

        return body.get("data")

    # ------------------------------------------------------------------
    # Read — plant & device discovery
    # ------------------------------------------------------------------

    async def get_plant_list(self) -> list[HuaweiPlant]:
        """Return all plants accessible with the current credentials."""
        data = await self._post_third_data("getStationList", {})
        raw_list: list[dict] = data if isinstance(data, list) else (data or [])
        plants = [HuaweiPlant.from_api(r) for r in raw_list]
        logger.info("huawei.plants_fetched", count=len(plants))
        return plants

    async def get_device_list(
        self,
        plant_code: str,
        dev_type_id: int = BatteryDevType.BATTERY_UNIT,
    ) -> list[HuaweiDevice]:
        """Return devices for a plant, filtered by type (39=battery, 41=ESS)."""
        data = await self._post_third_data(
            "getDevList",
            {"stationCodes": plant_code, "devTypeId": dev_type_id},
        )
        raw_list: list[dict] = data if isinstance(data, list) else (data or [])
        devices = [HuaweiDevice.from_api(r, plant_code) for r in raw_list]
        logger.debug("huawei.devices_fetched", plant_code=plant_code, count=len(devices))
        return devices

    # ------------------------------------------------------------------
    # Read — real-time KPIs
    # ------------------------------------------------------------------

    async def get_battery_realtime(
        self,
        device_ids: list[str],
        plant_code: str | None = None,
    ) -> list[HuaweiBatteryStatus]:
        """Fetch real-time KPIs for one or more battery devices (devTypeId=39).

        Rate-limited to 1 call per 5 minutes per plant. Raises HuaweiAPIError
        if called too frequently — callers should schedule via the watchdog.
        """
        if plant_code:
            self._check_rate_limit(plant_code)

        data = await self._post_third_data(
            "getDevRealKpi",
            {"devIds": ",".join(device_ids), "devTypeId": BatteryDevType.BATTERY_UNIT},
        )

        if plant_code:
            self._last_realtime_call[plant_code] = time.monotonic()

        raw_list: list[dict] = data if isinstance(data, list) else (data or [])
        statuses = []
        for entry in raw_list:
            device_id = str(entry.get("devDn") or entry.get("devId") or "")
            kpi = entry.get("dataItemMap") or entry
            statuses.append(HuaweiBatteryStatus.from_kpi(device_id, kpi, plant_code))

        logger.debug("huawei.realtime_fetched", device_count=len(statuses))
        return statuses

    async def get_plant_realtime(self, plant_code: str) -> dict[str, Any]:
        """Fetch aggregated real-time data for the whole plant (ESS level)."""
        self._check_rate_limit(plant_code)
        data = await self._post_third_data(
            "getDevRealKpi",
            {"devIds": plant_code, "devTypeId": BatteryDevType.ESS_SYSTEM},
        )
        self._last_realtime_call[plant_code] = time.monotonic()
        raw_list: list[dict] = data if isinstance(data, list) else (data or [])
        return raw_list[0] if raw_list else {}

    def _check_rate_limit(self, plant_code: str) -> None:
        last = self._last_realtime_call.get(plant_code)
        if last is not None:
            elapsed = time.monotonic() - last
            if elapsed < REALTIME_MIN_INTERVAL:
                remaining = int(REALTIME_MIN_INTERVAL - elapsed)
                raise HuaweiAPIError(
                    f"Rate limit: next real-time call for {plant_code} allowed in {remaining}s",
                    fail_code=407,
                )

    # ------------------------------------------------------------------
    # Control — dispatch mode
    # ------------------------------------------------------------------

    async def set_dispatch_mode(self, plant_code: str) -> str:
        """Switch the plant to thirdPartyDispatch mode.

        Must be called once before sending charge/discharge commands.
        Returns a request_id for the mode-change async task.
        """
        data = await self._post_nbi(
            "nbi/v1/control/battery/mode/async-task",
            {"stationCodes": [plant_code], "mode": "thirdPartyDispatch"},
        )
        request_id = str((data or {}).get("requestId") or (data or {}).get("request_id") or "")
        logger.info("huawei.dispatch_mode_set", plant_code=plant_code, request_id=request_id)
        return request_id

    # ------------------------------------------------------------------
    # Control — charge / discharge / stop
    # ------------------------------------------------------------------

    async def charge(
        self,
        plant_code: str,
        power_w: float,
        duration_min: int | None = None,
        target_soc: float | None = None,
    ) -> HuaweiDispatchTask:
        """Command the plant to charge at power_w watts.

        power_w must be > 0. The API maps this to dispatchSwitch=1.
        Raises HuaweiTaskError if a task is already in progress for this plant.
        """
        if power_w <= 0:
            raise ValueError(f"charge() requires power_w > 0, got {power_w}")
        self._assert_no_pending_task(plant_code)
        return await self._send_dispatch(
            plant_code=plant_code,
            switch=DispatchSwitch.CHARGE,
            power_w=power_w,  # positive → charge
            duration_min=duration_min,
            target_soc=target_soc,
        )

    async def discharge(
        self,
        plant_code: str,
        power_w: float,
        duration_min: int | None = None,
        target_soc: float | None = None,
    ) -> HuaweiDispatchTask:
        """Command the plant to discharge at power_w watts.

        power_w must be > 0 (the sign inversion is applied internally).
        The API receives powerDispatch=-power_w with dispatchSwitch=2.
        Raises HuaweiTaskError if a task is already in progress for this plant.
        """
        if power_w <= 0:
            raise ValueError(
                f"discharge() requires power_w > 0 (sign is applied internally), got {power_w}"
            )
        self._assert_no_pending_task(plant_code)
        return await self._send_dispatch(
            plant_code=plant_code,
            switch=DispatchSwitch.DISCHARGE,
            power_w=-power_w,  # negative → discharge (per Huawei spec)
            duration_min=duration_min,
            target_soc=target_soc,
        )

    async def stop(self, plant_code: str) -> HuaweiDispatchTask:
        """Stop any active charge or discharge command immediately."""
        # Stop is allowed even if there's a pending task — it's an override.
        self._pending_tasks.pop(plant_code, None)
        return await self._send_dispatch(
            plant_code=plant_code,
            switch=DispatchSwitch.STOP,
            power_w=0,
        )

    async def _send_dispatch(
        self,
        plant_code: str,
        switch: DispatchSwitch,
        power_w: float,
        duration_min: int | None = None,
        target_soc: float | None = None,
    ) -> HuaweiDispatchTask:
        payload: dict[str, Any] = {
            "stationCodes": [plant_code],
            "dispatchSwitch": int(switch),
            "powerDispatch": power_w,
        }
        if duration_min is not None:
            payload["durationMin"] = duration_min
        if target_soc is not None:
            payload["targetSoc"] = target_soc

        data = await self._post_nbi("nbi/v2/control/charge-and-discharge/async-task", payload)
        request_id = str((data or {}).get("requestId") or (data or {}).get("request_id") or "")

        task = HuaweiDispatchTask(
            request_id=request_id,
            plant_code=plant_code,
            dispatch_switch=switch,
            power_w=power_w,
            duration_min=duration_min,
            target_soc=target_soc,
            status=TaskStatus.IN_PROGRESS,
        )

        if switch != DispatchSwitch.STOP:
            self._pending_tasks[plant_code] = request_id

        logger.info(
            "huawei.dispatch_sent",
            plant_code=plant_code,
            switch=switch.name,
            power_w=power_w,
            request_id=request_id,
        )
        return task

    # ------------------------------------------------------------------
    # Control — task status
    # ------------------------------------------------------------------

    async def get_task_status(self, request_id: str, plant_code: str) -> HuaweiDispatchTask:
        """Poll the status of an async dispatch task."""
        data = await self._post_nbi(
            "v1/vpp/chargeAndDischargeStatus",
            {"requestId": request_id, "stationCode": plant_code},
        )
        raw = data or {}
        task = HuaweiDispatchTask.from_status_response(request_id, plant_code, raw)

        if task.is_complete or task.is_timed_out:
            self._pending_tasks.pop(plant_code, None)

        return task

    async def wait_for_task(
        self,
        request_id: str,
        plant_code: str,
        max_wait: float = TASK_MAX_WAIT,
        poll_interval: float = TASK_POLL_INTERVAL,
    ) -> HuaweiDispatchTask:
        """Poll get_task_status until complete, timeout, or max_wait exceeded."""
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            task = await self.get_task_status(request_id, plant_code)
            if task.is_complete:
                logger.info("huawei.task_complete", request_id=request_id)
                return task
            if task.is_timed_out:
                raise HuaweiTaskError(
                    f"Task {request_id} timed out on Huawei side",
                    request_id=request_id,
                    task_status=int(TaskStatus.TIMEOUT),
                )
            await asyncio.sleep(poll_interval)

        raise HuaweiTaskError(
            f"Task {request_id} did not complete within {max_wait}s",
            request_id=request_id,
            task_status=int(TaskStatus.IN_PROGRESS),
        )

    # ------------------------------------------------------------------
    # Safety helpers
    # ------------------------------------------------------------------

    def _assert_no_pending_task(self, plant_code: str) -> None:
        pending = self._pending_tasks.get(plant_code)
        if pending:
            raise HuaweiTaskError(
                f"Plant {plant_code} already has a pending task {pending}. "
                "Call get_task_status() to confirm completion before sending a new command.",
                request_id=pending,
            )
