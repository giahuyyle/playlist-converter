from pydantic import BaseModel


class Track(BaseModel):
    title: str
    artists: list[str]

    provider: str
    provider_id: str

    album: str | None = None
    duration_ms: int | None = None
    isrc: str | None = None
    provider_url: str | None = None
    artwork_url: str | None = None