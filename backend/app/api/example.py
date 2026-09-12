from fastapi import APIRouter
from app.models.track import Track

router = APIRouter(prefix="/example", tags=["example"])

@router.get("/track")
def get_example_track():
    return Track(
        title="Blinding Lights",
        artists=["The Weeknd"],
        provider="apple",
        provider_id="123456789",
        album="After Hours",
        duration_ms=200040,
        isrc="USUG11904206",
    )