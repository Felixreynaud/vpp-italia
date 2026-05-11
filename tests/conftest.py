"""Shared pytest fixtures."""

import asyncio
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.dependencies import get_current_user, get_db
from api.main import app
from data.models import Base, Battery, BatteryProtocol, BatteryState


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_session: AsyncSession):
    async def override_db():
        yield db_session

    def override_user():
        return {"user_id": "test-user", "roles": ["admin"]}

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def sample_battery(db_session: AsyncSession) -> Battery:
    battery = Battery(
        battery_id=uuid.uuid4(),
        asset_id="IT_BESS_001",
        site_id=uuid.uuid4(),
        name="Test Battery 1",
        protocol=BatteryProtocol.MODBUS,
        host="192.168.1.100",
        port=502,
        capacity_kwh=Decimal("1000.00"),
        max_power_kw=Decimal("500.00"),
        min_soc_percent=Decimal("10.0"),
        max_soc_percent=Decimal("90.0"),
        state=BatteryState.IDLE,
    )
    db_session.add(battery)
    await db_session.commit()
    await db_session.refresh(battery)
    return battery
