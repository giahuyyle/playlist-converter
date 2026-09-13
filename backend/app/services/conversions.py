import time

from sqlalchemy import select

from app.models.entities import Account, Job, Mapping, Match
from app.models.track import Track
from app.providers import provider_for
from app.providers.base import ProviderError
from app.repositories.mappings import save_mapping
from app.services.matching import rank


def matches(db, conversion_id):
    return list(
        db.scalars(
            select(Match).where(Match.conversion_id == conversion_id).order_by(Match.position)
        )
    )


def mapping_query(c, track, storefront):
    return select(Mapping).where(
        Mapping.user_id == c.user_id,
        Mapping.source_provider == c.source_provider,
        Mapping.source_id == track["provider_id"],
        Mapping.destination_provider == c.destination_provider,
        Mapping.storefront == storefront,
    )


def remember(db, c, match, storefront, verified=False):
    if not match.destination:
        return
    save_mapping(db, c, match, storefront, verified)


def refresh_counts(db, c):
    rows = matches(db, c.id)
    c.total_tracks = len(rows)
    c.matched_tracks = sum(bool(m.destination) and m.status in ("matched", "manual") for m in rows)
    c.failed_tracks = sum(m.status in ("unmatched", "skipped", "failed") for m in rows)
    c.transferred_tracks = sum(m.transferred for m in rows)


def analyze(db, c):
    c.status = "matching"
    db.commit()
    with (
        provider_for(db, c.user_id, c.source_provider) as source,
        provider_for(db, c.user_id, c.destination_provider) as destination,
    ):
        account = db.scalar(
            select(Account).where(
                Account.user_id == c.user_id, Account.provider == c.destination_provider
            )
        )
        if not c.imported:
            tracks = source.get_playlist_tracks(c.source_playlist_id)
            for position, track in enumerate(tracks):
                db.add(Match(conversion_id=c.id, position=position, source=track.model_dump()))
            c.imported = True
            c.total_tracks = len(tracks)
            db.commit()
        for match in matches(db, c.id):
            if match.status != "pending":
                continue
            track = Track(**match.source)
            cached = db.scalar(mapping_query(c, match.source, account.storefront))
            if not track.available:
                match.status = "unmatched"
            elif cached and cached.created_at > time.time() - 86400 * 30:
                match.candidates = [{"track": cached.destination, "confidence": cached.confidence}]
                match.destination = cached.destination
                match.confidence = cached.confidence
                match.status = (
                    "matched" if cached.verified or cached.confidence >= 0.9 else "review"
                )
            else:
                match.candidates = rank(track, destination.search_tracks(track))
                best = match.candidates[0] if match.candidates else None
                match.confidence = best["confidence"] if best else 0
                match.destination = best["track"] if best and match.confidence >= 0.7 else None
                match.status = (
                    "matched"
                    if match.confidence >= 0.9
                    else "review"
                    if match.confidence >= 0.7
                    else "unmatched"
                )
                if match.status == "matched":
                    remember(db, c, match, account.storefront)
            refresh_counts(db, c)
            db.commit()
    c.status = "review_required"
    db.commit()


def write(db, c, operation, call):
    c.pending_write = operation
    db.commit()
    try:
        result = call()
    except ProviderError as error:
        if not error.ambiguous:
            c.pending_write = None
            db.commit()
        raise
    return result


def transfer(db, c):
    if c.pending_write:
        raise ProviderError(
            "A provider write has an uncertain outcome. Reconcile it before retrying."
        )
    c.status = "transferring"
    db.commit()
    with provider_for(db, c.user_id, c.destination_provider) as destination:
        if not c.destination_playlist_id:
            c.destination_playlist_id = write(
                db,
                c,
                "create",
                lambda: destination.create_playlist(
                    c.name, f"Transferred with Playlist Converter. Transfer ID: {c.id}"
                ),
            )
            c.pending_write = None
            db.commit()
        for match in matches(db, c.id):
            if (
                match.transferred
                or match.status not in ("matched", "manual")
                or not match.destination
            ):
                continue
            write(
                db,
                c,
                match.id,
                lambda match=match: destination.add_tracks(
                    c.destination_playlist_id, [Track(**match.destination)]
                ),
            )
            match.transferred = True
            c.pending_write = None
            refresh_counts(db, c)
            db.commit()
    c.status = "completed"
    c.completed_at = time.time()
    c.error = None
    db.commit()


def reconcile(db, c):
    """Adopt only observed writes; absence never proves that a timed-out write failed."""
    with provider_for(db, c.user_id, c.destination_provider) as destination:
        if c.pending_write == "create":
            found = [
                p
                for p in destination.get_playlists()
                if f"Transfer ID: {c.id}" in (p.get("description") or "")
            ]
            if len(found) != 1:
                raise ProviderError(
                    "Could not identify exactly one destination playlist. Wait for provider consistency and try again; no write was replayed."
                )
            c.destination_playlist_id = found[0]["id"]
        else:
            rows = [
                m for m in matches(db, c.id) if m.destination and m.status in ("matched", "manual")
            ]
            pending = next((i for i, m in enumerate(rows) if m.id == c.pending_write), None)
            if pending is None:
                raise ProviderError("No pending write to reconcile")
            actual = [
                t.provider_id for t in destination.get_playlist_tracks(c.destination_playlist_id)
            ]
            expected = [m.destination["provider_id"] for m in rows[: pending + 1]]
            if actual != expected:
                raise ProviderError(
                    "Destination does not exactly match the expected track sequence. Wait and try again; no write was replayed."
                )
            rows[pending].transferred = True
        c.pending_write = None
        c.error = None
        refresh_counts(db, c)
        db.commit()


def enqueue(db, c):
    db.add(Job(conversion_id=c.id))
    db.commit()
