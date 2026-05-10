"""OCPP 2.0.1 connector for EV-compatible battery storage systems."""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

import structlog
import websockets
from ocpp.routing import on
from ocpp.v201 import ChargePoint as CP
from ocpp.v201 import call, call_result
from ocpp.v201.enums import Action, RegistrationStatusType

if TYPE_CHECKING:
    from data.models import Battery

logger = structlog.get_logger(__name__)


class VPPChargePoint(CP):
    """Extended ChargePoint with VPP-specific message handlers."""

    @on(Action.Heartbeat)
    async def on_heartbeat(self) -> call_result.Heartbeat:
        from datetime import datetime, timezone

        return call_result.Heartbeat(current_time=datetime.now(timezone.utc).isoformat())

    @on(Action.BootNotification)
    async def on_boot_notification(
        self, charging_station: dict, reason: str, **kwargs
    ) -> call_result.BootNotification:
        from datetime import datetime, timezone

        logger.info(
            "ocpp.boot_notification",
            charge_point_id=self.id,
            model=charging_station.get("model"),
            vendor=charging_station.get("vendor_name"),
        )
        return call_result.BootNotification(
            current_time=datetime.now(timezone.utc).isoformat(),
            interval=60,
            status=RegistrationStatusType.accepted,
        )

    @on(Action.MeterValues)
    async def on_meter_values(self, evse_id: int, meter_value: list, **kwargs) -> call_result.MeterValues:
        logger.debug("ocpp.meter_values", charge_point_id=self.id, evse_id=evse_id)
        # Parse meter values and publish to Kafka
        return call_result.MeterValues()

    async def set_charging_profile(self, power_kw: float) -> str:
        """Send a SetChargingProfile to control charge/discharge power."""
        command_id = str(uuid.uuid4())
        await self.call(
            call.SetChargingProfile(
                evse_id=0,
                charging_profile={
                    "chargingProfileId": abs(hash(command_id)) % 10000,
                    "stackLevel": 10,
                    "chargingProfilePurpose": "TxDefaultProfile",
                    "chargingProfileKind": "Absolute",
                    "chargingSchedule": [
                        {
                            "id": 1,
                            "chargingRateUnit": "W",
                            "chargingSchedulePeriod": [{"startPeriod": 0, "limit": abs(power_kw) * 1000}],
                        }
                    ],
                },
            )
        )
        logger.info("ocpp.charging_profile_set", charge_point_id=self.id, power_kw=power_kw, command_id=command_id)
        return command_id


class OCPPServer:
    """WebSocket server accepting connections from OCPP-capable batteries."""

    def __init__(self, host: str = "0.0.0.0", port: int = 9000) -> None:
        self._host = host
        self._port = port
        self._charge_points: dict[str, VPPChargePoint] = {}

    async def start(self) -> None:
        logger.info("ocpp.server_starting", host=self._host, port=self._port)
        async with websockets.serve(
            self._on_connect,
            self._host,
            self._port,
            subprotocols=["ocpp2.0.1"],
        ):
            await asyncio.Future()

    async def _on_connect(self, websocket: websockets.WebSocketServerProtocol, path: str) -> None:
        charge_point_id = path.strip("/").split("/")[-1]
        cp = VPPChargePoint(charge_point_id, websocket)
        self._charge_points[charge_point_id] = cp
        logger.info("ocpp.charge_point_connected", charge_point_id=charge_point_id)
        try:
            await cp.start()
        finally:
            self._charge_points.pop(charge_point_id, None)
            logger.info("ocpp.charge_point_disconnected", charge_point_id=charge_point_id)

    def get_charge_point(self, cp_id: str) -> VPPChargePoint | None:
        return self._charge_points.get(cp_id)
