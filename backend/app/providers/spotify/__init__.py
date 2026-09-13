from urllib.parse import quote, urlparse

from app.models.track import Track
from app.providers.base import HTTPProvider, ProviderError
from app.services.matching import normalize


def normalize_song(item):
    album = item.get("album", {})
    return Track(
        title=item.get("name", "Unavailable track"),
        artists=[a["name"] for a in item.get("artists", [])],
        album=album.get("name"),
        duration_ms=item.get("duration_ms"),
        isrc=item.get("external_ids", {}).get("isrc"),
        provider="spotify",
        provider_id=item.get("id") or "unavailable",
        provider_url=item.get("external_urls", {}).get("spotify"),
        artwork_url=(album.get("images") or [{}])[0].get("url"),
        available=bool(item.get("id"))
        and not item.get("is_local", False)
        and item.get("is_playable", True),
    )


class SpotifyProvider(HTTPProvider):
    def __init__(self, token):
        super().__init__(token, "https://api.spotify.com/v1/")

    def pages(self, path):
        result = []
        while path:
            data = self.request("GET", path)
            result.extend(data.get("items", []))
            path = data.get("next")
            if path and (
                urlparse(path).hostname != "api.spotify.com" or urlparse(path).scheme != "https"
            ):
                raise ProviderError("Unexpected Spotify pagination URL")
        return result

    def playlist(self, x):
        return {
            "id": x["id"],
            "name": x["name"],
            "description": x.get("description", ""),
            "track_count": x.get("items", x.get("tracks", {})).get("total"),
            "url": x.get("external_urls", {}).get("spotify"),
        }

    def get_playlists(self):
        return [self.playlist(x) for x in self.pages("me/playlists?limit=50")]

    def get_playlist(self, playlist_id):
        return self.playlist(self.request("GET", f"playlists/{quote(playlist_id, safe='')}"))

    def get_playlist_tracks(self, playlist_id):
        return [
            normalize_song(x.get("item") or x.get("track") or {})
            for x in self.pages(f"playlists/{quote(playlist_id, safe='')}/items?limit=50")
        ]

    def search_tracks(self, track):
        query = (
            f"isrc:{track.isrc}"
            if track.isrc
            else " ".join(track.artists + [normalize(track.title)])
        )
        data = self.request("GET", "search", params={"q": query, "type": "track", "limit": 5})
        items = data.get("tracks", {}).get("items", [])
        if not items and track.isrc:
            data = self.request(
                "GET",
                "search",
                params={
                    "q": " ".join(track.artists + [normalize(track.title)]),
                    "type": "track",
                    "limit": 5,
                },
            )
            items = data.get("tracks", {}).get("items", [])
        return [normalize_song(x) for x in items]

    def create_playlist(self, name, description):
        return self.request(
            "POST",
            "me/playlists",
            json={"name": name, "description": description, "public": False},
        )["id"]

    def add_tracks(self, playlist_id, tracks):
        self.request(
            "POST",
            f"playlists/{quote(playlist_id, safe='')}/items",
            json={"uris": [f"spotify:track:{t.provider_id}" for t in tracks]},
        )
