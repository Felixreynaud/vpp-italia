"""Market submission endpoints (GME, Terna)."""

from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from api.dependencies import CurrentUser, DbSession
from data.schemas import MarketOfferCreate, MarketOfferListResponse, MarketOfferResponse

router = APIRouter(prefix="/markets")


@router.get("/offers", response_model=MarketOfferListResponse)
async def list_offers(
    db: DbSession,
    _user: CurrentUser,
    market: Annotated[str | None, Query(description="MGP | MSD | MI | MB")] = None,
    delivery_date: Annotated[date | None, Query()] = None,
) -> MarketOfferListResponse:
    """List market offers submitted to GME/Terna."""
    from sqlalchemy import select

    from data.models import MarketOffer

    query = select(MarketOffer).order_by(MarketOffer.submitted_at.desc()).limit(200)
    if market:
        query = query.where(MarketOffer.market == market.upper())
    if delivery_date:
        query = query.where(MarketOffer.delivery_date == delivery_date)

    result = await db.execute(query)
    offers = result.scalars().all()
    return MarketOfferListResponse(
        data=[MarketOfferResponse.model_validate(o) for o in offers],
        meta={"count": len(offers)},
    )


@router.post("/offers", response_model=MarketOfferResponse, status_code=status.HTTP_201_CREATED)
async def submit_offer(
    payload: MarketOfferCreate,
    db: DbSession,
    _user: CurrentUser,
) -> MarketOfferResponse:
    """Submit a market offer to GME or Terna."""
    from connectors.gme import GMEClient
    from connectors.terna import TernaClient
    from data.models import MarketOffer

    client: GMEClient | TernaClient
    if payload.market in ("MGP", "MI", "MSD_GME"):
        client = GMEClient()
    elif payload.market in ("MSD", "MB"):
        client = TernaClient()
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown market: {payload.market}",
        )

    external_id = await client.submit_offer(payload)

    offer = MarketOffer(**payload.model_dump(), external_id=external_id, status="submitted")
    db.add(offer)
    await db.flush()
    await db.refresh(offer)
    return MarketOfferResponse.model_validate(offer)


@router.get("/offers/{offer_id}", response_model=MarketOfferResponse)
async def get_offer(
    offer_id: UUID,
    db: DbSession,
    _user: CurrentUser,
) -> MarketOfferResponse:
    from data.models import MarketOffer

    offer = await db.get(MarketOffer, offer_id)
    if not offer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Offer not found")
    return MarketOfferResponse.model_validate(offer)


@router.post("/offers/{offer_id}/cancel", response_model=MarketOfferResponse)
async def cancel_offer(
    offer_id: UUID,
    db: DbSession,
    _user: CurrentUser,
) -> MarketOfferResponse:
    """Cancel a previously submitted offer (if the market window allows it)."""
    from connectors.gme import GMEClient
    from connectors.terna import TernaClient
    from data.models import MarketOffer, OfferStatus

    offer = await db.get(MarketOffer, offer_id)
    if not offer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Offer not found")

    if offer.status not in (OfferStatus.SUBMITTED, OfferStatus.ACCEPTED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel offer in status: {offer.status}",
        )

    client: GMEClient | TernaClient = (
        GMEClient() if offer.market in ("MGP", "MI", "MSD_GME") else TernaClient()
    )

    await client.cancel_offer(str(offer.external_id))
    offer.status = OfferStatus.CANCELLED
    await db.flush()
    await db.refresh(offer)
    return MarketOfferResponse.model_validate(offer)


# ---------------------------------------------------------------------------
# /mgp/prices — Day-Ahead prices per zone + date (reads mgp_prices table)
# ---------------------------------------------------------------------------


@router.get("/mgp/zones")
async def list_mgp_zones(_user: CurrentUser) -> dict[str, Any]:
    """Static list of all 7 zones + PUN, for UI dropdowns."""
    from data.models import MGPZone

    return {
        "data": [{"code": z.value, "label": z.value} for z in MGPZone],
        "meta": {"count": len(MGPZone)},
    }


@router.get("/mgp/prices")
async def get_mgp_prices(
    db: DbSession,
    _user: CurrentUser,
    zone: Annotated[
        str, Query(description="Zone code: NORD, CNOR, CSUD, SUD, CALA, SARD, SICI, PUN")
    ] = "NORD",
    delivery_date: Annotated[
        date | None, Query(description="YYYY-MM-DD in Europe/Rome (defaults to today)")
    ] = None,
) -> dict[str, Any]:
    """24-hour price curve for the given zone & date.

    If the DB is empty for the requested slot, returns a plausible mock curve
    (typical Italian shape: night trough + 18-21h peak) so the UI never breaks
    while the backfill / scheduler catches up.
    """
    from datetime import date as date_cls

    from sqlalchemy import select

    from data.models import MGPPrice

    target = delivery_date or date_cls.today()
    zone_up = zone.upper()

    result = await db.execute(
        select(MGPPrice)
        .where(MGPPrice.delivery_date == target.isoformat(), MGPPrice.zone == zone_up)
        .order_by(MGPPrice.hour)
    )
    rows = result.scalars().all()

    if rows:
        prices = [{"hour": int(r.hour), "price_eur_mwh": float(r.price_eur_mwh)} for r in rows]
        source = "gme-db"
    else:
        # Fallback: typical Italian curve — night trough, 18-21h peak
        base = [
            45,
            42,
            40,
            38,
            37,
            38,
            50,
            75,
            90,
            85,
            78,
            72,
            68,
            65,
            70,
            80,
            95,
            110,
            105,
            92,
            78,
            65,
            55,
            48,
        ]
        prices = [{"hour": h, "price_eur_mwh": float(p)} for h, p in enumerate(base)]
        source = "mock-no-data"

    return {
        "data": {
            "prices": prices,
            "zone": zone_up,
            "delivery_date": target.isoformat(),
        },
        "meta": {
            "count": len(prices),
            "source": source,
        },
    }
