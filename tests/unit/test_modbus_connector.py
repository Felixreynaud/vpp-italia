"""Unit tests for connectors/modbus.py — read_battery, send_power_setpoint."""

from __future__ import annotations

import contextlib
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymodbus.exceptions import ModbusException

from connectors.modbus import ModbusReading, read_battery, send_power_setpoint

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_battery(
    host: str = "10.0.0.1",
    port: int = 502,
    max_power_kw: Decimal = Decimal("500.0"),
) -> MagicMock:
    battery = MagicMock()
    battery.battery_id = uuid.uuid4()
    battery.host = host
    battery.port = port
    battery.max_power_kw = max_power_kw
    return battery


def make_modbus_client(
    connected: bool = True,
    registers: list[int] | None = None,
    read_error: bool = False,
    write_error: bool = False,
) -> AsyncMock:
    client = AsyncMock()
    client.connected = connected

    read_result = MagicMock()
    read_result.isError.return_value = read_error
    if registers is not None:
        read_result.registers = registers
    client.read_input_registers.return_value = read_result

    write_result = MagicMock()
    write_result.isError.return_value = write_error
    client.write_register.return_value = write_result

    client.connect = AsyncMock()
    client.close = MagicMock()
    return client


# ---------------------------------------------------------------------------
# read_battery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_battery_returns_modbus_reading() -> None:
    regs = [450, 120, 4000, 500, 250, 3]
    mock_client = make_modbus_client(registers=regs)

    with patch("connectors.modbus.AsyncModbusTcpClient", return_value=mock_client):
        result = await read_battery(make_battery())

    assert isinstance(result, ModbusReading)
    assert result.soc_percent == pytest.approx(45.0)
    assert result.power_kw == pytest.approx(12.0)
    assert result.voltage_v == pytest.approx(400.0)
    assert result.current_a == pytest.approx(50.0)
    assert result.temperature_c == pytest.approx(25.0)
    assert result.status_code == 3


@pytest.mark.asyncio
async def test_read_battery_raises_on_connection_failure() -> None:
    mock_client = make_modbus_client(connected=False)

    with (
        patch("connectors.modbus.AsyncModbusTcpClient", return_value=mock_client),
        pytest.raises(ConnectionError),
    ):
        await read_battery(make_battery())


@pytest.mark.asyncio
async def test_read_battery_raises_on_modbus_read_error() -> None:
    mock_client = make_modbus_client(registers=[], read_error=True)

    with (
        patch("connectors.modbus.AsyncModbusTcpClient", return_value=mock_client),
        pytest.raises(ModbusException),
    ):
        await read_battery(make_battery())


@pytest.mark.asyncio
async def test_read_battery_closes_client_on_success() -> None:
    mock_client = make_modbus_client(registers=[0, 0, 0, 0, 0, 0])

    with patch("connectors.modbus.AsyncModbusTcpClient", return_value=mock_client):
        await read_battery(make_battery())

    mock_client.close.assert_called_once()


@pytest.mark.asyncio
async def test_read_battery_closes_client_on_error() -> None:
    mock_client = make_modbus_client(connected=False)

    with (
        patch("connectors.modbus.AsyncModbusTcpClient", return_value=mock_client),
        contextlib.suppress(ConnectionError),
    ):
        await read_battery(make_battery())

    mock_client.close.assert_called_once()


# ---------------------------------------------------------------------------
# send_power_setpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_power_setpoint_returns_command_id() -> None:
    mock_client = make_modbus_client()

    with patch("connectors.modbus.AsyncModbusTcpClient", return_value=mock_client):
        command_id = await send_power_setpoint(make_battery(), power_kw=100.0)

    assert isinstance(command_id, str)
    assert len(command_id) == 36


@pytest.mark.asyncio
async def test_send_power_setpoint_clamps_positive() -> None:
    mock_client = make_modbus_client()
    battery = make_battery(max_power_kw=Decimal("200.0"))

    with patch("connectors.modbus.AsyncModbusTcpClient", return_value=mock_client):
        await send_power_setpoint(battery, power_kw=999.0)

    call_args = mock_client.write_register.call_args
    register_value = call_args.args[1]
    assert register_value == 2000


@pytest.mark.asyncio
async def test_send_power_setpoint_clamps_negative() -> None:
    mock_client = make_modbus_client()
    battery = make_battery(max_power_kw=Decimal("200.0"))

    with patch("connectors.modbus.AsyncModbusTcpClient", return_value=mock_client):
        await send_power_setpoint(battery, power_kw=-999.0)

    register_value = mock_client.write_register.call_args.args[1]
    assert register_value == -2000


@pytest.mark.asyncio
async def test_send_power_setpoint_raises_on_write_error() -> None:
    mock_client = make_modbus_client(write_error=True)

    with (
        patch("connectors.modbus.AsyncModbusTcpClient", return_value=mock_client),
        pytest.raises(ModbusException),
    ):
        await send_power_setpoint(make_battery(), power_kw=50.0)


@pytest.mark.asyncio
async def test_send_power_setpoint_raises_on_connection_failure() -> None:
    mock_client = make_modbus_client(connected=False)

    with (
        patch("connectors.modbus.AsyncModbusTcpClient", return_value=mock_client),
        pytest.raises(ConnectionError),
    ):
        await send_power_setpoint(make_battery(), power_kw=50.0)
