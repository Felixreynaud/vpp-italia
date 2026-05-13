"""Battery management endpoints."""

from decimal import Decimal
from typing import Annotated, Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from api.dependencies import CurrentUser, DbSession
from data.models import Battery, BatteryProtocol
from data.schemas import (
    BatteryCreate,
    BatteryListResponse,
    BatteryResponse,
    BatteryUpdate,
    BulkImportRequest,
    BulkImportResponse,
    DiscoveredBattery,
    DispatchCommand,
    DispatchCommandResponse,
    HuaweiDiscoverRequest,
    HuaweiDiscoverResponse,
)

router = APIRouter(prefix="/batteries")


# ---------------------------------------------------------------------------
# Helpers — Huawei connector
# ---------------------------------------------------------------------------


def _split_host_port(endpoint_url: str) -> tuple[str, int]:
    """Extract (host, port) from a URL — defaults to 80/443 if not given."""
    parsed = urlparse(endpoint_url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, port


def _build_huawei_client(endpoint_url: str, client_id: str, client_secret: str) -> Any:
    """Build a HuaweiBatteryClient targeted at a custom endpoint URL.

    Accepts URLs with or without scheme: "http://127.0.0.1:9999",
    "https://intl.fusionsolar.huawei.com", or simply "127.0.0.1:9999".
    """
    from connectors.huawei.auth import HuaweiAuthClient
    from connectors.huawei.client import HuaweiBatteryClient

    parsed = urlparse(endpoint_url if "://" in endpoint_url else f"https://{endpoint_url}")
    domain = parsed.netloc
    scheme = parsed.scheme or "https"
    if not domain:
        raise ValueError(f"Cannot parse endpoint_url: {endpoint_url}")

    auth = HuaweiAuthClient(
        domain=domain, client_id=client_id, client_secret=client_secret, scheme=scheme
    )
    return HuaweiBatteryClient(domain=domain, auth=auth, scheme=scheme)


@router.get("", response_model=BatteryListResponse)
async def list_batteries(
    db: DbSession,
    _user: CurrentUser,
    site_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[str | None, Query()] = None,
) -> BatteryListResponse:
    """List all batteries, optionally filtered by site.

    Each battery is enriched with the latest reading (soc_percent, power_kw,
    temperature_c, …) when available — so a single GET is enough to render
    the dashboard / battery cards.
    """
    from sqlalchemy import text

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

    # Fetch latest reading per battery in one go (DISTINCT ON, last 10 minutes)
    readings_map: dict[str, Any] = {}
    if batteries:
        battery_ids = [str(b.battery_id) for b in batteries]
        readings_sql = text(
            """
            SELECT DISTINCT ON (battery_id)
                battery_id, soc_percent, power_kw, voltage_v, current_a,
                temperature_c, time
            FROM battery_readings
            WHERE time > NOW() - INTERVAL '10 minutes'
              AND battery_id::text = ANY(:ids)
            ORDER BY battery_id, time DESC
            """
        )
        try:
            rr = await db.execute(readings_sql, {"ids": battery_ids})
            for row in rr:
                readings_map[str(row.battery_id)] = row
        except Exception:
            # battery_readings may not exist yet — degrade gracefully
            readings_map = {}

    payload: list[BatteryResponse] = []
    for b in batteries:
        r = readings_map.get(str(b.battery_id))
        item = BatteryResponse.model_validate(b)
        if r is not None:
            item.soc_percent = float(r.soc_percent) if r.soc_percent is not None else None
            # Convention exposée au frontend : positive = décharge, négative = charge.
            # On inverse le signe par rapport à battery_readings (qui stocke en
            # convention Huawei : positive = charge).
            item.power_kw = -float(r.power_kw) if r.power_kw is not None else None
            item.voltage_v = float(r.voltage_v) if r.voltage_v is not None else None
            item.current_a = float(r.current_a) if r.current_a is not None else None
            item.temperature_c = (
                float(r.temperature_c) if r.temperature_c is not None else None
            )
            item.last_seen = r.time
        payload.append(item)

    return BatteryListResponse(
        data=payload,
        meta={"count": len(payload), "next_cursor": next_cursor},
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


@router.delete("/{battery_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_battery(
    battery_id: UUID,
    db: DbSession,
    _user: CurrentUser,
) -> None:
    """Remove a battery from the fleet (does not delete historical readings)."""
    battery = await db.get(Battery, battery_id)
    if not battery:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Battery not found")
    await db.delete(battery)
    await db.flush()


@router.post("/{battery_id}/dispatch", response_model=DispatchCommandResponse)
async def send_dispatch_command(
    battery_id: UUID,
    command: DispatchCommand,
    db: DbSession,
    _user: CurrentUser,
) -> DispatchCommandResponse:
    """Send an immediate dispatch command (manual override).

    Routes to the right connector based on battery.protocol + metadata.subtype:
    - REST + huawei_fusion_solar → HuaweiBatteryClient.charge/discharge/stop
    - Other → Modbus connector (legacy fallback)

    Power convention (frontend → backend): power_kw > 0 means **discharge**,
    < 0 means **charge**, 0 means **stop** (matches DispatchCommand docstring).
    """
    battery = await db.get(Battery, battery_id)
    if not battery:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Battery not found")

    if abs(command.power_kw) > battery.max_power_kw:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Requested power {command.power_kw} kW exceeds battery limit {battery.max_power_kw} kW",
        )

    meta = battery.metadata_ or {}
    subtype = meta.get("subtype")

    # ---- Huawei FusionSolar path ----
    if battery.protocol == BatteryProtocol.REST and subtype == "huawei_fusion_solar":
        client = _build_huawei_client(
            meta["endpoint_url"], meta["client_id"], meta["client_secret"]
        )
        plant_code = meta["plant_code"]

        # Ensure the plant is in thirdPartyDispatch mode (idempotent)
        try:
            await client.set_dispatch_mode(plant_code)
        except Exception:
            pass  # already enabled or transient; charge/discharge will raise if not OK

        power_kw_abs = abs(float(command.power_kw))
        power_w = power_kw_abs * 1000.0
        try:
            if command.power_kw > 0:
                task = await client.discharge(plant_code, power_w=power_w)
            elif command.power_kw < 0:
                task = await client.charge(plant_code, power_w=power_w)
            else:
                task = await client.stop(plant_code)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Huawei dispatch failed: {exc}",
            ) from exc

        # Immediately re-read the state so the frontend sees the new state
        # without waiting for the next 10-second poller cycle.
        try:
            from data.models import BatteryState

            statuses = await client.get_battery_realtime(
                device_ids=[meta["device_id"]], plant_code=plant_code
            )
            if statuses:
                s = statuses[0]
                if s.power_kw > 0.1:
                    battery.state = BatteryState.CHARGING
                elif s.power_kw < -0.1:
                    battery.state = BatteryState.DISCHARGING
                else:
                    battery.state = BatteryState.IDLE
                await db.flush()
        except Exception:
            pass  # state will get refreshed by the next poller cycle anyway

        return DispatchCommandResponse(
            command_id=task.request_id,
            battery_id=battery_id,
            power_kw=command.power_kw,
        )

    # ---- Modbus fallback (legacy) ----
    from connectors.modbus import send_power_setpoint

    command_id = await send_power_setpoint(battery, float(command.power_kw))
    return DispatchCommandResponse(
        command_id=command_id, battery_id=battery_id, power_kw=command.power_kw
    )


# ---------------------------------------------------------------------------
# Discovery & bulk import — Huawei FusionSolar
# ---------------------------------------------------------------------------


@router.post("/discover/huawei", response_model=HuaweiDiscoverResponse)
async def discover_huawei(
    payload: HuaweiDiscoverRequest,
    _user: CurrentUser,
) -> HuaweiDiscoverResponse:
    """Query a Huawei FusionSolar endpoint (or simulator) and list all batteries.

    Does **not** persist anything — only returns a catalog the operator can
    then submit via POST /batteries/bulk-import.
    """
    client = _build_huawei_client(payload.endpoint_url, payload.client_id, payload.client_secret)

    try:
        plants = await client.get_plant_list()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Huawei endpoint error: {exc}",
        ) from exc

    discovered: list[DiscoveredBattery] = []
    for plant in plants:
        try:
            devices = await client.get_device_list(plant.plant_code)
        except Exception:
            continue
        for dev in devices:
            # Default specs if the device doesn't expose them: use plant capacity.
            capacity_kwh = Decimal(str(plant.capacity_kwh or 0))
            # Heuristic: max power = capacity / 2 hours (0.5C rate) when unknown.
            max_power_kw = (
                Decimal(str(plant.capacity_kw)) if plant.capacity_kw else capacity_kwh / 2
            )
            discovered.append(
                DiscoveredBattery(
                    plant_code=plant.plant_code,
                    plant_name=plant.plant_name,
                    device_id=dev.device_id,
                    model=dev.model,
                    capacity_kwh=capacity_kwh,
                    max_power_kw=max_power_kw,
                )
            )

    return HuaweiDiscoverResponse(
        data=discovered, meta={"count": len(discovered), "endpoint": payload.endpoint_url}
    )


@router.post("/bulk-import", response_model=BulkImportResponse)
async def bulk_import_batteries(
    payload: BulkImportRequest,
    db: DbSession,
    _user: CurrentUser,
) -> BulkImportResponse:
    """Insert a list of discovered Huawei batteries into the fleet.

    Skips silently any battery whose asset_id already exists (idempotent).
    """
    parsed = urlparse(payload.endpoint_url if "://" in payload.endpoint_url else f"https://{payload.endpoint_url}")
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    # Pre-fetch existing asset_ids to skip duplicates in a single query
    existing_result = await db.execute(
        select(Battery.asset_id).where(
            Battery.asset_id.in_([b.asset_id for b in payload.batteries])
        )
    )
    existing: set[str] = {row[0] for row in existing_result}

    created_ids: list[UUID] = []
    skipped = 0

    for item in payload.batteries:
        if item.asset_id in existing:
            skipped += 1
            continue

        metadata: dict[str, Any] = {
            "subtype": "huawei_fusion_solar",
            "endpoint_url": payload.endpoint_url,
            "plant_code": item.plant_code,
            "device_id": item.device_id,
            "model": item.model,
            "client_id": payload.client_id,
            # SECURITY: in real deployments, store this in Secrets Manager and
            # reference it here rather than persisting plain text.
            "client_secret": payload.client_secret,
        }

        battery = Battery(
            battery_id=uuid4(),
            asset_id=item.asset_id,
            site_id=item.site_id,
            name=item.name,
            protocol=BatteryProtocol.REST,
            host=host,
            port=port,
            capacity_kwh=item.capacity_kwh,
            max_power_kw=item.max_power_kw,
            metadata_=metadata,
        )
        db.add(battery)
        await db.flush()
        created_ids.append(battery.battery_id)

    return BulkImportResponse(imported=len(created_ids), skipped=skipped, battery_ids=created_ids)


@router.post("/{battery_id}/test-connection")
async def test_connection(
    battery_id: UUID,
    db: DbSession,
    _user: CurrentUser,
) -> dict[str, Any]:
    """Ping a battery via its configured connector and return current KPIs."""
    battery = await db.get(Battery, battery_id)
    if not battery:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Battery not found")

    meta = battery.metadata_ or {}
    subtype = meta.get("subtype")

    if battery.protocol != BatteryProtocol.REST or subtype != "huawei_fusion_solar":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"test-connection only supports Huawei FusionSolar (got {subtype})",
        )

    client = _build_huawei_client(
        meta["endpoint_url"], meta["client_id"], meta["client_secret"]
    )
    try:
        statuses = await client.get_battery_realtime(
            device_ids=[meta["device_id"]], plant_code=meta["plant_code"]
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    if not statuses:
        return {"ok": False, "error": "No data returned for device"}

    s = statuses[0]
    return {
        "ok": True,
        "soc_percent": s.soc,
        # Convention business pour le frontend : positive = décharge.
        "power_kw": -s.power_kw if s.power_kw is not None else None,
        "voltage_v": s.voltage_v,
        "temperature_c": s.temperature_c,
        "soh": s.soh,
    }
