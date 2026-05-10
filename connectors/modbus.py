"""Modbus TCP connector for industrial battery systems."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

if TYPE_CHECKING:
    from data.models import Battery

logger = structlog.get_logger(__name__)

# Modbus register map (manufacturer-specific — adjust per hardware)
REG_POWER_SETPOINT = 0x1000   # Holding register: target power in 0.1 kW units
REG_SOC = 0x1001              # Input register: SoC in 0.1% units
REG_POWER_ACTUAL = 0x1002     # Input register: actual power in 0.1 kW units
REG_VOLTAGE = 0x1003          # Input register: DC voltage in 0.1 V units
REG_CURRENT = 0x1004          # Input register: DC current in 0.1 A units
REG_TEMPERATURE = 0x1005      # Input register: cell temperature in 0.1 °C units
REG_STATUS = 0x1006           # Input register: battery status bitmask


@dataclass
class ModbusReading:
    battery_id: str
    soc_percent: float | None
    power_kw: float | None
    voltage_v: float | None
    current_a: float | None
    temperature_c: float | None
    status_code: int | None
    raw: dict


async def read_battery(battery: "Battery", timeout: float = 5.0) -> ModbusReading:
    """Poll a battery over Modbus TCP and return a structured reading."""
    client = AsyncModbusTcpClient(str(battery.host), port=battery.port, timeout=timeout)
    raw: dict = {}
    try:
        await client.connect()
        if not client.connected:
            raise ConnectionError(f"Could not connect to {battery.host}:{battery.port}")

        result = await client.read_input_registers(REG_SOC, count=6, slave=1)
        if result.isError():
            raise ModbusException(f"Read error: {result}")

        regs = result.registers
        raw = {"registers": list(regs), "address": REG_SOC, "count": 6}

        return ModbusReading(
            battery_id=str(battery.battery_id),
            soc_percent=regs[0] / 10.0 if len(regs) > 0 else None,
            power_kw=regs[1] / 10.0 if len(regs) > 1 else None,
            voltage_v=regs[2] / 10.0 if len(regs) > 2 else None,
            current_a=regs[3] / 10.0 if len(regs) > 3 else None,
            temperature_c=regs[4] / 10.0 if len(regs) > 4 else None,
            status_code=regs[5] if len(regs) > 5 else None,
            raw=raw,
        )
    finally:
        client.close()


async def send_power_setpoint(battery: "Battery", power_kw: float, timeout: float = 5.0) -> str:
    """Write a power setpoint to a battery. Returns a command_id for tracking."""
    command_id = str(uuid.uuid4())
    client = AsyncModbusTcpClient(str(battery.host), port=battery.port, timeout=timeout)

    # Clamp to battery limits
    clamped = max(-float(battery.max_power_kw), min(float(battery.max_power_kw), power_kw))
    register_value = int(clamped * 10)  # 0.1 kW units

    try:
        await client.connect()
        if not client.connected:
            raise ConnectionError(f"Could not connect to {battery.host}:{battery.port}")

        result = await client.write_register(REG_POWER_SETPOINT, register_value, slave=1)
        if result.isError():
            raise ModbusException(f"Write error: {result}")

        logger.info(
            "modbus.setpoint_written",
            battery_id=str(battery.battery_id),
            power_kw=clamped,
            command_id=command_id,
        )
        return command_id
    finally:
        client.close()
