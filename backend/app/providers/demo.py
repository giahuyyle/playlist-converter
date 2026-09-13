"""Explicit, isolated demo catalog. Never calls a real music account."""

from sqlalchemy import select

from app.core.database import Session
from app.models.entities import DemoPlaylist
from app.models.track import Track
from app.providers.base import Provider, ProviderError

CATALOG = [
    ("Blinding Lights", "The Weeknd", 200000),
    ("Get Lucky", "Daft Punk", 369000),
    ("Dreams", "Fleetwood Mac", 257000),
    ("Teardrop", "Massive Attack", 330000),
    ("Midnight City", "M83", 244000),
    ("Lost recording", "Unknown Artist", 180000),
]


class DemoProvider(Provider):
    def __init__(self, name, user_id):
        self.name, self.user_id = name, user_id

    def track(self, index):
        title, artist, duration = CATALOG[index]
        return Track(
            title=title,
            artists=[artist],
            duration_ms=duration,
            provider=self.name,
            provider_id=f"demo-{index}",
        )

    def get_playlists(self):
        with Session() as db:
            saved = db.scalars(
                select(DemoPlaylist).where(
                    DemoPlaylist.user_id == self.user_id,
                    DemoPlaylist.provider == self.name,
                )
            )
            return [
                {
                    "id": "demo",
                    "name": "Late night favorites",
                    "description": "Six sample tracks",
                    "track_count": 6,
                }
            ] + [
                {
                    "id": p.id,
                    "name": p.name,
                    "description": p.description,
                    "track_count": len(p.tracks),
                }
                for p in saved
            ]

    def get_playlist(self, playlist_id):
        result = next((p for p in self.get_playlists() if p["id"] == playlist_id), None)
        if not result:
            raise ProviderError("Demo playlist not found")
        return result

    def get_playlist_tracks(self, playlist_id):
        if playlist_id == "demo":
            return [self.track(i) for i in range(len(CATALOG))]
        with Session() as db:
            p = db.get(DemoPlaylist, playlist_id)
            if not p or p.user_id != self.user_id or p.provider != self.name:
                raise ProviderError("Demo playlist not found")
            return [Track(**t) for t in p.tracks]

    def search_tracks(self, track):
        if track.title == "Lost recording":
            return []
        index = next(i for i, row in enumerate(CATALOG) if row[0] == track.title)
        best = self.track(index)
        if index == 1:
            best.artists = ["Daft Punk feat. Pharrell Williams"]
        return [
            best,
            best.model_copy(
                update={
                    "title": best.title + " (Live)",
                    "provider_id": best.provider_id + "-live",
                }
            ),
        ]

    def create_playlist(self, name, description):
        with Session() as db:
            p = DemoPlaylist(
                user_id=self.user_id,
                provider=self.name,
                name=name,
                description=description,
            )
            db.add(p)
            db.commit()
            return p.id

    def add_tracks(self, playlist_id, tracks):
        with Session() as db:
            p = db.get(DemoPlaylist, playlist_id)
            if not p or p.user_id != self.user_id or p.provider != self.name:
                raise ProviderError("Demo playlist not found")
            p.tracks = p.tracks + [t.model_dump() for t in tracks]
            db.commit()
