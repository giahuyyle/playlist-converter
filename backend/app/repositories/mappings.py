"""Atomic mapping writes: concurrent conversions must not fail on cache collisions."""

import time

from sqlalchemy import or_
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models.entities import Mapping, uid


def save_mapping(db, conversion, match, storefront, verified):
    insert = postgres_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    values = {
        "id": uid(),
        "user_id": conversion.user_id,
        "source_provider": conversion.source_provider,
        "source_id": match.source["provider_id"],
        "destination_provider": conversion.destination_provider,
        "storefront": storefront,
        "destination": match.destination,
        "confidence": match.confidence,
        "verified": verified,
        "created_at": time.time(),
    }
    statement = insert(Mapping).values(**values)
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[
                "user_id",
                "source_provider",
                "source_id",
                "destination_provider",
                "storefront",
            ],
            set_={
                key: values[key] for key in ("destination", "confidence", "verified", "created_at")
            },
            # A speculative match must not overwrite a user's explicit correction.
            where=or_(Mapping.verified.is_(False), verified),
        )
    )
