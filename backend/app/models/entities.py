import time
import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def uid():
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Account(Base):
    __tablename__ = "provider_accounts"
    __table_args__ = (UniqueConstraint("user_id", "provider"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(16))
    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[float | None] = mapped_column(Float)
    storefront: Mapped[str] = mapped_column(String(16), default="us")
    provider_user_id: Mapped[str | None] = mapped_column(String(256))


class OAuthState(Base):
    __tablename__ = "oauth_states"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    provider: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[float] = mapped_column(Float)
    verifier: Mapped[str] = mapped_column(String(128))


class Conversion(Base):
    __tablename__ = "conversions"
    __table_args__ = (UniqueConstraint("user_id", "request_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    request_key: Mapped[str] = mapped_column(String(128))
    source_provider: Mapped[str] = mapped_column(String(16))
    destination_provider: Mapped[str] = mapped_column(String(16))
    source_playlist_id: Mapped[str] = mapped_column(String(256))
    destination_playlist_id: Mapped[str | None] = mapped_column(String(256))
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    phase: Mapped[str] = mapped_column(String(16), default="matching")
    total_tracks: Mapped[int] = mapped_column(Integer, default=0)
    matched_tracks: Mapped[int] = mapped_column(Integer, default=0)
    transferred_tracks: Mapped[int] = mapped_column(Integer, default=0)
    failed_tracks: Mapped[int] = mapped_column(Integer, default=0)
    imported: Mapped[bool] = mapped_column(Boolean, default=False)
    pending_write: Mapped[str | None] = mapped_column(String(128))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    completed_at: Mapped[float | None] = mapped_column(Float)


class Match(Base):
    __tablename__ = "track_matches"
    __table_args__ = (UniqueConstraint("conversion_id", "position"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    conversion_id: Mapped[str] = mapped_column(ForeignKey("conversions.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    source: Mapped[dict] = mapped_column(JSON)
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    destination: Mapped[dict | None] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    transferred: Mapped[bool] = mapped_column(Boolean, default=False)


class Mapping(Base):
    __tablename__ = "track_mappings"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "source_provider",
            "source_id",
            "destination_provider",
            "storefront",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    source_provider: Mapped[str] = mapped_column(String(16))
    source_id: Mapped[str] = mapped_column(String(256))
    destination_provider: Mapped[str] = mapped_column(String(16))
    storefront: Mapped[str] = mapped_column(String(16))
    destination: Mapped[dict] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    conversion_id: Mapped[str] = mapped_column(ForeignKey("conversions.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[float] = mapped_column(Float, default=time.time)
    published_at: Mapped[float | None] = mapped_column(Float)


class DemoPlaylist(Base):
    __tablename__ = "demo_playlists"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    provider: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    tracks: Mapped[list] = mapped_column(JSON, default=list)
