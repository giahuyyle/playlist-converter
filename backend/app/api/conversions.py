import asyncio
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.database import Session, get_db
from app.core.security import current_user
from app.models.entities import Account, Conversion, Match
from app.providers import provider_for
from app.services.conversions import (
    enqueue,
    matches,
    reconcile,
    refresh_counts,
    remember,
)

router = APIRouter(prefix="/api", tags=["conversions"])
ProviderName = Literal["youtube", "apple", "spotify"]


def owned(db, user_id, conversion_id, lock=False):
    query = select(Conversion).where(Conversion.id == conversion_id, Conversion.user_id == user_id)
    c = db.scalar(query.with_for_update() if lock else query)
    if not c:
        raise HTTPException(404, "Transfer not found")
    return c


def serialize(c):
    return {
        key: getattr(c, key)
        for key in (
            "id",
            "name",
            "source_provider",
            "destination_provider",
            "source_playlist_id",
            "destination_playlist_id",
            "status",
            "total_tracks",
            "matched_tracks",
            "failed_tracks",
            "transferred_tracks",
            "error",
            "pending_write",
            "created_at",
            "completed_at",
        )
    }


def detail(db, c):
    return {
        **serialize(c),
        "matches": [
            {
                key: getattr(m, key)
                for key in (
                    "id",
                    "position",
                    "source",
                    "candidates",
                    "destination",
                    "confidence",
                    "status",
                    "transferred",
                )
            }
            for m in matches(db, c.id)
        ],
    }


@router.get("/playlists/{provider}")
def playlists(provider: ProviderName, user=Depends(current_user), db=Depends(get_db)):
    with provider_for(db, user.id, provider) as p:
        return p.get_playlists()


@router.get("/playlists/{provider}/{playlist_id}/tracks")
def playlist_tracks(
    provider: ProviderName,
    playlist_id: str,
    user=Depends(current_user),
    db=Depends(get_db),
):
    with provider_for(db, user.id, provider) as p:
        return p.get_playlist_tracks(playlist_id)


class CreateConversion(BaseModel):
    source_provider: ProviderName
    destination_provider: ProviderName
    source_playlist_id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=200)
    request_key: str = Field(min_length=1, max_length=128)


@router.post("/conversions", status_code=202)
def create(body: CreateConversion, user=Depends(current_user), db=Depends(get_db)):
    if body.source_provider == body.destination_provider:
        raise HTTPException(422, "Choose two different providers")
    previous = db.scalar(
        select(Conversion).where(
            Conversion.user_id == user.id, Conversion.request_key == body.request_key
        )
    )
    if previous:
        if any(
            getattr(previous, k) != getattr(body, k)
            for k in (
                "source_provider",
                "destination_provider",
                "source_playlist_id",
                "name",
            )
        ):
            raise HTTPException(409, "Idempotency key was already used for another request")
        return serialize(previous)
    accounts = set(db.scalars(select(Account.provider).where(Account.user_id == user.id)))
    if not {body.source_provider, body.destination_provider} <= accounts:
        raise HTTPException(409, "Connect both accounts first")
    c = Conversion(user_id=user.id, **body.model_dump())
    db.add(c)
    try:
        db.flush()
        enqueue(db, c)
    except IntegrityError:
        db.rollback()
        return create(body, user, db)
    return serialize(c)


@router.get("/conversions")
def history(user=Depends(current_user), db=Depends(get_db)):
    return [
        serialize(c)
        for c in db.scalars(
            select(Conversion)
            .where(Conversion.user_id == user.id)
            .order_by(Conversion.created_at.desc())
            .limit(100)
        )
    ]


@router.get("/conversions/{conversion_id}")
def get_conversion(conversion_id: str, user=Depends(current_user), db=Depends(get_db)):
    return detail(db, owned(db, user.id, conversion_id))


class Review(BaseModel):
    candidate_index: int | None = Field(default=None, ge=0, le=4)
    skip: bool = False


@router.patch("/conversions/{conversion_id}/matches/{match_id}")
def review(
    conversion_id: str,
    match_id: str,
    body: Review,
    user=Depends(current_user),
    db=Depends(get_db),
):
    c = owned(db, user.id, conversion_id, True)
    if c.status != "review_required":
        raise HTTPException(409, "Transfer is not awaiting review")
    m = db.get(Match, match_id)
    if not m or m.conversion_id != c.id:
        raise HTTPException(404)
    if body.skip:
        m.status, m.destination = "skipped", None
    elif body.candidate_index is not None and body.candidate_index < len(m.candidates):
        candidate = m.candidates[body.candidate_index]
        m.status, m.destination, m.confidence = (
            "manual",
            candidate["track"],
            candidate["confidence"],
        )
        account = db.scalar(
            select(Account).where(
                Account.user_id == user.id, Account.provider == c.destination_provider
            )
        )
        if not account:
            raise HTTPException(409, "Reconnect destination account")
        remember(db, c, m, account.storefront, True)
    else:
        raise HTTPException(422, "Pick a candidate or skip this track")
    refresh_counts(db, c)
    db.commit()
    return detail(db, c)


@router.post("/conversions/{conversion_id}/confirm", status_code=202)
def confirm(conversion_id: str, user=Depends(current_user), db=Depends(get_db)):
    c = owned(db, user.id, conversion_id, True)
    if c.status in ("transferring", "completed") or (
        c.status == "queued" and c.phase == "transfer"
    ):
        return serialize(c)
    if c.status != "review_required":
        raise HTTPException(409, "Transfer is not ready for confirmation")
    if any(m.status in ("pending", "review", "unmatched") for m in matches(db, c.id)):
        raise HTTPException(409, "Review or skip every unresolved track first")
    if not c.matched_tracks:
        raise HTTPException(409, "Select at least one track")
    c.phase, c.status = "transfer", "queued"
    enqueue(db, c)
    return serialize(c)


@router.post("/conversions/{conversion_id}/retry", status_code=202)
def retry(conversion_id: str, user=Depends(current_user), db=Depends(get_db)):
    c = owned(db, user.id, conversion_id, True)
    if c.status != "failed" or c.pending_write:
        raise HTTPException(409, "Only failed transfers without uncertain writes can be retried")
    c.status, c.error = "queued", None
    enqueue(db, c)
    return serialize(c)


@router.post("/conversions/{conversion_id}/reconcile")
def reconcile_transfer(conversion_id: str, user=Depends(current_user), db=Depends(get_db)):
    c = owned(db, user.id, conversion_id, True)
    if c.status != "failed" or not c.pending_write:
        raise HTTPException(409, "No uncertain write to reconcile")
    reconcile(db, c)
    return detail(db, c)


@router.get("/conversions/{conversion_id}/events")
def events(conversion_id: str, request: Request, user=Depends(current_user), db=Depends(get_db)):
    owned(db, user.id, conversion_id)

    async def stream():
        previous = None
        for _ in range(300):
            if await request.is_disconnected():
                break

            def snapshot():
                with Session() as session:
                    return serialize(owned(session, user.id, conversion_id))

            value = await asyncio.to_thread(snapshot)
            payload = json.dumps(value)
            if payload != previous:
                yield f"data: {payload}\n\n"
                previous = payload
            else:
                yield ": keepalive\n\n"
            if value["status"] in ("completed", "failed", "review_required"):
                break
            await asyncio.sleep(2)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
