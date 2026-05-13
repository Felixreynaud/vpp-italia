"""Huawei SmartPVMS NBI — HTTP simulator server.

A standalone FastAPI app that mimics the real Huawei FusionSolar Northbound
Interface. Wraps the in-memory ``HuaweiSimulator`` to expose its state over
the same HTTP routes the production client (``connectors.huawei.client``)
calls.

Run locally::

    python -m connectors.huawei.sim_server

Or via uvicorn directly::

    uvicorn connectors.huawei.sim_server:app --host 127.0.0.1 --port 9999

Configuration (env vars):
    HUAWEI_SIM_HOST            bind address (default 127.0.0.1)
    HUAWEI_SIM_PORT            port (default 9999)
    HUAWEI_SIM_NUM_BATTERIES   how many batteries to pre-load (default 20)
    HUAWEI_SIM_RATE_LIMIT_S    rate limit between real-time calls per plant
                               (default 1 — production is 300)
    HUAWEI_SIM_CLIENT_ID       expected clientId in /auth/token (default any)
    HUAWEI_SIM_CLIENT_SECRET   expected clientSecret in /auth/token
"""

from __future__ import annotations

import os
import secrets
import time
import uuid
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException
from pydantic import BaseModel

from connectors.huawei.exceptions import HuaweiAPIError
from connectors.huawei.models import BatteryDevType, DispatchSwitch, TaskStatus
from connectors.huawei.simulator import LUNA2000_MODELS, HuaweiSimulator

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HOST = os.getenv("HUAWEI_SIM_HOST", "127.0.0.1")
PORT = int(os.getenv("HUAWEI_SIM_PORT", "9999"))
NUM_BATTERIES = int(os.getenv("HUAWEI_SIM_NUM_BATTERIES", "20"))
RATE_LIMIT_S = float(os.getenv("HUAWEI_SIM_RATE_LIMIT_S", "1.0"))
EXPECTED_CLIENT_ID = os.getenv("HUAWEI_SIM_CLIENT_ID", "")
EXPECTED_CLIENT_SECRET = os.getenv("HUAWEI_SIM_CLIENT_SECRET", "")

# Plant naming: SITE-{IT_REGION}-{N}, mirrors realistic Italian deployments
_ITALIAN_SITES = ["MI", "RO", "NA", "TO", "BO", "FI", "VE", "GE"]


def _build_initial_fleet(num: int) -> list[tuple[str, str]]:
    """Generate a realistic mix of LUNA2000 plants.

    Roughly 50% residential (15-30 kWh), 40% commercial (107-161 kWh),
    10% industrial (215 kWh).
    """
    residential = ["LUNA2000-15kWh", "LUNA2000-30kWh"]
    commercial = ["LUNA2000-107kWh", "LUNA2000-161kWh"]
    industrial = ["LUNA2000-215kWh"]

    out: list[tuple[str, str]] = []
    for i in range(num):
        site = _ITALIAN_SITES[i % len(_ITALIAN_SITES)]
        plant_code = f"SITE-{site}-{i // len(_ITALIAN_SITES) + 1:02d}"
        if i < num * 0.5:
            model = residential[i % len(residential)]
        elif i < num * 0.9:
            model = commercial[i % len(commercial)]
        else:
            model = industrial[i % len(industrial)]
        out.append((plant_code, model))
    return out


# ---------------------------------------------------------------------------
# Global state — single shared simulator + auth registry
# ---------------------------------------------------------------------------

simulator = HuaweiSimulator(plants=_build_initial_fleet(NUM_BATTERIES))

# Mapping access_token -> issued_at (monotonic). Tokens are pure-random strings.
_TOKENS: dict[str, float] = {}
_TOKEN_TTL = 3600.0  # seconds, matches Huawei NBI default

# Per-plant rate limit tracking (decoupled from simulator internals)
_last_realtime_call: dict[str, float] = {}


def _issue_token() -> str:
    token = secrets.token_urlsafe(32)
    _TOKENS[token] = time.monotonic()
    return token


def _verify_token(value: str | None) -> None:
    if not value:
        raise HTTPException(status_code=401, detail="missing token")
    issued = _TOKENS.get(value)
    if issued is None:
        raise HTTPException(status_code=401, detail="invalid token")
    if time.monotonic() - issued > _TOKEN_TTL:
        del _TOKENS[value]
        raise HTTPException(status_code=401, detail="token expired")


def _fail(code: int, message: str = "") -> dict[str, Any]:
    return {"failCode": code, "message": message, "data": None}


def _ok(data: Any = None) -> dict[str, Any]:
    return {"failCode": 0, "message": "OK", "data": data}


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Huawei FusionSolar NBI Simulator",
    description="Drop-in HTTP simulator for Huawei SmartPVMS NBI v24.6.",
    version="1.0.0",
)


@app.get("/_sim/health", tags=["sim"])
async def sim_health() -> dict[str, Any]:
    """Simulator-specific health check (not part of the real Huawei API)."""
    return {
        "status": "ok",
        "plants": len(simulator._plants),
        "batteries": len(simulator._batteries),
        "active_tokens": len(_TOKENS),
        "rate_limit_s": RATE_LIMIT_S,
    }


@app.get("/_sim/fleet", tags=["sim"])
async def sim_fleet() -> dict[str, Any]:
    """List every simulated plant + its single battery (no auth required)."""
    fleet: list[dict[str, Any]] = []
    for plant_code, plant in simulator._plants.items():
        bats = [b for b in simulator._batteries.values() if b.plant_code == plant_code]
        for bat in bats:
            fleet.append({
                "plant_code": plant_code,
                "plant_name": plant.plant_name,
                "device_id": bat.device_id,
                "model": bat.model,
                "capacity_kwh": bat.capacity_kwh,
                "max_power_kw": bat.max_power_kw,
                "soc": round(bat.soc, 1),
                "current_power_kw": round(bat.current_power_kw, 2),
            })
    return {"count": len(fleet), "fleet": fleet}


# ---------------------------------------------------------------------------
# Auth — POST /rest/openapi/pvms/nbi/v1/auth/token
# ---------------------------------------------------------------------------


class AuthPayload(BaseModel):
    clientId: str
    clientSecret: str


@app.post("/rest/openapi/pvms/nbi/v1/auth/token", tags=["auth"])
async def auth_token(payload: AuthPayload) -> dict[str, Any]:
    if EXPECTED_CLIENT_ID and payload.clientId != EXPECTED_CLIENT_ID:
        return _fail(401, "invalid clientId")
    if EXPECTED_CLIENT_SECRET and payload.clientSecret != EXPECTED_CLIENT_SECRET:
        return _fail(401, "invalid clientSecret")

    token = _issue_token()
    return _ok({"accessToken": token, "expiresIn": int(_TOKEN_TTL)})


# ---------------------------------------------------------------------------
# Read — /thirdData/* (xsrf-token header)
# ---------------------------------------------------------------------------


@app.post("/thirdData/getStationList", tags=["thirdData"])
async def get_station_list(
    body: dict[str, Any] | None = Body(default=None),
    xsrf_token: str | None = Header(default=None, alias="xsrf-token"),
) -> dict[str, Any]:
    _verify_token(xsrf_token)
    plants = await simulator.get_plant_list()
    data = [
        {
            "stationCode": p.plant_code,
            "stationName": p.plant_name,
            "designCapacity": p.capacity_kwh,
        }
        for p in plants
    ]
    return _ok(data)


@app.post("/thirdData/getDevList", tags=["thirdData"])
async def get_dev_list(
    body: dict[str, Any] = Body(...),
    xsrf_token: str | None = Header(default=None, alias="xsrf-token"),
) -> dict[str, Any]:
    _verify_token(xsrf_token)
    plant_code = body.get("stationCodes", "")
    if not plant_code:
        return _fail(20001, "stationCodes required")

    try:
        devices = await simulator.get_device_list(plant_code)
    except HuaweiAPIError as exc:
        return _fail(404, str(exc))

    data = [
        {
            "devDn": d.device_id,
            "devName": d.device_name,
            "devTypeId": d.device_type_id,
            "model": d.model,
        }
        for d in devices
    ]
    return _ok(data)


@app.post("/thirdData/getDevRealKpi", tags=["thirdData"])
async def get_dev_real_kpi(
    body: dict[str, Any] = Body(...),
    xsrf_token: str | None = Header(default=None, alias="xsrf-token"),
) -> dict[str, Any]:
    _verify_token(xsrf_token)
    raw_ids = body.get("devIds", "")
    dev_type_id = int(body.get("devTypeId", BatteryDevType.BATTERY_UNIT))
    device_ids = [s.strip() for s in str(raw_ids).split(",") if s.strip()]
    if not device_ids:
        return _fail(20001, "devIds required")

    # Group requested devices by plant for rate-limit enforcement
    plants_seen: set[str] = set()
    for dev_id in device_ids:
        for bat in simulator._batteries.values():
            if bat.device_id == dev_id:
                plants_seen.add(bat.plant_code)
                break

    now = time.monotonic()
    for plant_code in plants_seen:
        last = _last_realtime_call.get(plant_code, 0.0)
        if now - last < RATE_LIMIT_S:
            return _fail(
                407,
                f"rate limit: next call for {plant_code} in {RATE_LIMIT_S - (now - last):.1f}s",
            )
    for plant_code in plants_seen:
        _last_realtime_call[plant_code] = now

    # Build KPI response — mirror real Huawei response shape
    out: list[dict[str, Any]] = []
    for dev_id in device_ids:
        bat = simulator._batteries.get(dev_id)
        if bat is None:
            continue
        status = bat.to_status()
        out.append({
            "devDn": dev_id,
            "devId": dev_id,
            "dataItemMap": {
                "battery_soc": status.soc,
                "charge_discharge_power": status.power_kw,
                "battery_voltage": status.voltage_v,
                "battery_current": status.current_a,
                "battery_temperature": status.temperature_c,
                "battery_soh": status.soh,
                "run_state": status.status,
            },
        })

    if dev_type_id == BatteryDevType.ESS_SYSTEM and out:
        # Aggregate response: one entry per plant
        return _ok(out[:1])
    return _ok(out)


# ---------------------------------------------------------------------------
# Control — /rest/openapi/pvms/* (Bearer token)
# ---------------------------------------------------------------------------


def _bearer_token(auth_header: str | None) -> str:
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing Bearer token")
    return auth_header.split(" ", 1)[1]


@app.post("/rest/openapi/pvms/nbi/v1/control/battery/mode/async-task", tags=["control"])
async def set_dispatch_mode(
    body: dict[str, Any] = Body(...),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _verify_token(_bearer_token(authorization))
    plant_codes = body.get("stationCodes") or []
    if not plant_codes:
        return _fail(20001, "stationCodes required")

    request_id = ""
    for plant_code in plant_codes:
        try:
            request_id = await simulator.set_dispatch_mode(plant_code)
        except HuaweiAPIError as exc:
            return _fail(404, str(exc))
    return _ok({"requestId": request_id})


@app.post("/rest/openapi/pvms/nbi/v2/control/charge-and-discharge/async-task", tags=["control"])
async def charge_discharge(
    body: dict[str, Any] = Body(...),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _verify_token(_bearer_token(authorization))
    plant_codes = body.get("stationCodes") or []
    if not plant_codes:
        return _fail(20001, "stationCodes required")
    switch = DispatchSwitch(int(body.get("dispatchSwitch", 0)))
    power_w = float(body.get("powerDispatch", 0))
    power_kw = abs(power_w) / 1000.0
    duration_min = int(body.get("durationMin", 60))
    target_soc = body.get("targetSoc")

    request_id = ""
    for plant_code in plant_codes:
        try:
            task = await simulator.dispatch(
                plant_code=plant_code,
                switch=switch,
                power_kw=power_kw,
                duration_min=duration_min,
                target_soc=target_soc,
            )
            request_id = task.request_id
        except HuaweiAPIError as exc:
            return _fail(exc.http_status or 422, str(exc))
    return _ok({"requestId": request_id})


@app.post("/rest/openapi/pvms/v1/vpp/chargeAndDischargeStatus", tags=["control"])
async def get_task_status(
    body: dict[str, Any] = Body(...),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _verify_token(_bearer_token(authorization))
    request_id = body.get("requestId", "")
    plant_code = body.get("stationCode", "")
    record = simulator._tasks.get(request_id)
    if record is None:
        return _fail(404, f"task {request_id} not found")

    # Simulator: any task is considered complete after 2 seconds wall-clock
    elapsed = time.monotonic() - record.created_at
    status = TaskStatus.COMPLETE if elapsed > 2.0 else TaskStatus.IN_PROGRESS

    return _ok({
        "requestId": request_id,
        "stationCode": plant_code,
        "dispatchSwitch": int(record.dispatch_switch),
        "status": int(status),
        "errorCode": None,
        "errorMsg": None,
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import uvicorn

    uvicorn.run(
        "connectors.huawei.sim_server:app",
        host=HOST,
        port=PORT,
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    main()
