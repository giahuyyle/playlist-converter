from pydantic import BaseModel, Field


class Track(BaseModel):
    title: str
    artists: list[str] = Field(default_factory=list)
    provider: str
    provider_id: str
    album: str | None = None
    duration_ms: int | None = None
    isrc: str | None = None
    provider_url: str | None = None
    artwork_url: str | None = None
    official: bool = False
    music: bool = False
    available: bool = True
