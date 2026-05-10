"""GME (Gestore dei Mercati Energetici) API client.

Handles offer submission for MGP, MI, and MSD-GME markets.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import httpx
import structlog

if TYPE_CHECKING:
    from data.schemas import MarketOfferCreate

logger = structlog.get_logger(__name__)


class GMEClient:
    """Client for GME electronic markets API."""

    def __init__(self) -> None:
        self._base_url = os.environ["GME_API_BASE_URL"]
        self._participant_code = os.environ["GME_PARTICIPANT_CODE"]
        self._sandbox = os.getenv("GME_SANDBOX_MODE", "true").lower() == "true"
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            token = await self._authenticate()
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {token}", "X-Participant-Code": self._participant_code},
                timeout=30.0,
            )
        return self._client

    async def _authenticate(self) -> str:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self._base_url}/auth/token",
                data={
                    "grant_type": "password",
                    "username": os.environ["GME_API_USERNAME"],
                    "password": os.environ["GME_API_PASSWORD"],
                },
            )
            resp.raise_for_status()
            return resp.json()["access_token"]

    async def submit_offer(self, offer: "MarketOfferCreate") -> str:
        """Submit a market offer and return the external offer ID."""
        payload = self._build_payload(offer)
        client = await self._get_client()
        endpoint = "/sandbox/offers" if self._sandbox else "/offers"

        resp = await client.post(endpoint, json=payload)
        resp.raise_for_status()

        external_id = resp.json()["offerId"]
        logger.info(
            "gme.offer_submitted",
            market=offer.market,
            delivery_date=str(offer.delivery_date),
            external_id=external_id,
            sandbox=self._sandbox,
        )
        return external_id

    async def cancel_offer(self, external_id: str) -> None:
        client = await self._get_client()
        endpoint = f"/sandbox/offers/{external_id}" if self._sandbox else f"/offers/{external_id}"
        resp = await client.delete(endpoint)
        resp.raise_for_status()
        logger.info("gme.offer_cancelled", external_id=external_id)

    def _build_payload(self, offer: "MarketOfferCreate") -> dict[str, Any]:
        return {
            "market": offer.market,
            "deliveryDate": str(offer.delivery_date),
            "quarterHourStart": offer.quarter_hour_start,
            "quarterHourEnd": offer.quarter_hour_end,
            "energyMWh": float(offer.energy_mwh) if offer.energy_mwh else None,
            "priceEurMWh": float(offer.price_eur_mwh),
            "direction": offer.direction,
            "participantCode": self._participant_code,
        }
