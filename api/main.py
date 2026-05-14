"""VPP Italia — FastAPI application entry point."""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from api.dependencies import close_db, get_session_factory, init_db
from api.routers.optimization import router as optimization_router
from api.routes import (
    admin_aggregates,
    admin_users,
    auth,
    batteries,
    dashboard,
    dispatch,
    markets,
    metrics,
)
from core.battery_polling import BatteryPoller
from core.dispatch_applier import DispatchApplier
from core.market.mgp_scheduler import MGPPriceScheduler
from core.scheduler import MarketScheduler

logger = structlog.get_logger(__name__)

_scheduler: MarketScheduler | None = None
_battery_poller: BatteryPoller | None = None
_dispatch_applier: DispatchApplier | None = None
_mgp_scheduler: MGPPriceScheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _scheduler, _battery_poller, _dispatch_applier, _mgp_scheduler

    logger.info("vpp.startup", version=app.version)
    await init_db()

    _scheduler = MarketScheduler()
    await _scheduler.start()

    factory = get_session_factory()
    _battery_poller = BatteryPoller(session_factory=factory)
    await _battery_poller.start()

    _dispatch_applier = DispatchApplier(session_factory=factory)
    await _dispatch_applier.start()

    _mgp_scheduler = MGPPriceScheduler(session_factory=factory)
    await _mgp_scheduler.start()

    yield

    logger.info("vpp.shutdown")
    if _mgp_scheduler:
        await _mgp_scheduler.stop()
    if _dispatch_applier:
        await _dispatch_applier.stop()
    if _battery_poller:
        await _battery_poller.stop()
    if _scheduler:
        await _scheduler.stop()
    await close_db()


app = FastAPI(
    title="VPP Italia API",
    description=(
        "API de pilotage pour centrale virtuelle (VPP) — "
        "100+ batteries industrielles, marches GME et Terna."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

_default_origins = "http://localhost:3000,http://localhost:8080"
_origins = [
    o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", _default_origins).split(",") if o.strip()
]
_origin_regex = os.getenv("CORS_ALLOWED_ORIGIN_REGEX") or None

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_origin_regex=_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    excluded_handlers=["/health", "/metrics"],
).instrument(app).expose(app, endpoint="/metrics")

API_PREFIX = "/api/v1"

app.include_router(auth.router, tags=["auth"])
app.include_router(admin_users.router, tags=["admin"])
app.include_router(admin_aggregates.router, tags=["admin"])
app.include_router(batteries.router, prefix=API_PREFIX, tags=["batteries"])
app.include_router(dispatch.router, prefix=API_PREFIX, tags=["dispatch"])
app.include_router(markets.router, prefix=API_PREFIX, tags=["markets"])
app.include_router(dashboard.router, prefix=API_PREFIX, tags=["dashboard"])
app.include_router(metrics.router, tags=["monitoring"])
app.include_router(optimization_router)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, Any]:
    return {"name": "VPP Italia API", "version": app.version, "docs": "/docs"}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception", path=request.url.path, exc=str(exc))
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "type": "about:blank",
            "title": "Internal Server Error",
            "status": 500,
            "detail": "An unexpected error occurred.",
            "instance": str(request.url),
        },
    )
