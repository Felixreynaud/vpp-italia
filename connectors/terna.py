"""Terna API client — MSD and MB market offer submission.

Terna is the Italian TSO (Transmission System Operator).
All capacity offers for ancillary services go through this API.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import httpx
import structlog

if TYPE_CHECKING:
    from data.schemas import MarketOfferCreate

logger = structlog.get_logger(__name__)


class TernaClient:
    """Client for Terna ancillary services API (MSD, MB)."""

    def __init__(self) -> None:
        self._base_url = os.environ["TERNA_API_BASE_URL"]
        self._upca_code = os.environ["TERNA_UPCA_CODE"]
        self._sandbox = os.getenv("TERNA_SANDBOX_MODE", "true").lower() == "true"
        self._token: str | None = None
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._token = await self._authenticate()
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=30.0,
            )
        return self._client

    async def _authenticate(self) -> str:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self._base_url}/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": os.environ["TERNA_CLIENT_ID"],
                    "client_secret": os.environ["TERNA_CLIENT_SECRET"],
                },
            )
            resp.raise_for_status()
            return str(resp.json()["access_token"])

    async def submit_offer(self, offer: MarketOfferCreate) -> str:
        """Submit a capacity offer to MSD or MB."""
        payload = self._build_payload(offer)
        client = await self._get_client()
        endpoint = f"/msd/{'sandbox/' if self._sandbox else ''}offers"

        resp = await client.post(endpoint, json=payload)
        resp.raise_for_status()

        external_id: str = str(resp.json()["id"])
        logger.info(
            "terna.offer_submitted",
            market=offer.market,
            delivery_date=str(offer.delivery_date),
            external_id=external_id,
            upca_code=self._upca_code,
            sandbox=self._sandbox,
        )
        return external_id

    async def cancel_offer(self, external_id: str) -> None:
        client = await self._get_client()
        endpoint = f"/msd/{'sandbox/' if self._sandbox else ''}offers/{external_id}"
        resp = await client.delete(endpoint)
        resp.raise_for_status()
        logger.info("terna.offer_cancelled", external_id=external_id)

    async def get_dispatch_signals(self, delivery_date: str) -> list[dict[str, Any]]:
        """Fetch real-time dispatch signals from Terna for the given date."""
        client = await self._get_client()
        resp = await client.get(
            "/msd/dispatch-signals", params={"date": delivery_date, "upca": self._upca_code}
        )
        resp.raise_for_status()
        signals: list[dict[str, Any]] = resp.json()["signals"]
        return signals

    def _build_payload(self, offer: MarketOfferCreate) -> dict[str, Any]:
        return {
            "upcaCode": self._upca_code,
            "market": offer.market,
            "deliveryDate": str(offer.delivery_date),
            "quarterHourStart": offer.quarter_hour_start,
            "quarterHourEnd": offer.quarter_hour_end,
            "capacityMW": float(offer.capacity_mw) if offer.capacity_mw else None,
            "priceEurMWh": float(offer.price_eur_mwh),
            "direction": offer.direction,
        }
