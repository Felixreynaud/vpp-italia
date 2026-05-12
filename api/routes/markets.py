"""Market submission endpoints (GME, Terna)."""

from datetime import date
from typing import Annotated
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
