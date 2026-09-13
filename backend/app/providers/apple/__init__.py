import time
from pathlib import Path
from urllib.parse import quote

import jwt

from app.core.config import config
from app.models.track import Track
from app.providers.base import HTTPProvider, ProviderError
from app.services.matching import normalize


def developer_token():
    if not all((config.apple_team_id, config.apple_key_id, config.apple_private_key_path)):
        raise ProviderError("Apple Music is not configured on the server")
    return jwt.encode(
        {
            "iss": config.apple_team_id,
            "iat": int(time.time()) - 30,
            "exp": int(time.time()) + 3600,
        },
        Path(config.apple_private_key_path).read_text(),
        algorithm="ES256",
        headers={"kid": config.apple_key_id},
    )


def normalize_song(item):
    a = item.get("attributes", {})
    artwork = a.get("artwork", {}).get("url")
    catalog_id = a.get("playParams", {}).get("catalogId")
    return Track(
        title=a.get("name", "Unavailable song"),
        artists=[a["artistName"]] if a.get("artistName") else [],
        album=a.get("albumName"),
        duration_ms=a.get("durationInMillis"),
        isrc=a.get("isrc"),
        provider="apple",
        provider_id=catalog_id or item["id"],
        provider_url=a.get("url"),
        artwork_url=artwork.replace("{w}", "160").replace("{h}", "160") if artwork else None,
        available=bool(a),
    )


class AppleProvider(HTTPProvider):
    def __init__(self, token, storefront="us"):
        super().__init__(
            developer_token(),
            "https://api.music.apple.com/",
            {"Music-User-Token": token},
        )
        self.storefront = quote(storefront, safe="")

    def pages(self, path, params=None):
        result = []
        while path:
            if not path.startswith("/v1/"):
                raise ProviderError("Unexpected Apple pagination URL")
            data = self.request("GET", path, params=params)
            result.extend(data.get("data", []))
            path, params = data.get("next"), None
        return result

    def playlist(self, x):
        a = x.get("attributes", {})
        description = a.get("description", {})
        return {
            "id": x["id"],
            "name": a.get("name", "Untitled"),
            "description": description.get("standard", "")
            if isinstance(description, dict)
            else description,
            "track_count": None,
            "url": a.get("url"),
        }

    def get_playlists(self):
        return [self.playlist(x) for x in self.pages("/v1/me/library/playlists", {"limit": 100})]

    def get_playlist(self, playlist_id):
        items = self.request("GET", f"/v1/me/library/playlists/{quote(playlist_id, safe='')}").get(
            "data", []
        )
        if not items:
            raise ProviderError("Playlist is unavailable")
        return self.playlist(items[0])

    def get_playlist_tracks(self, playlist_id):
        return [
            normalize_song(x)
            for x in self.pages(
                f"/v1/me/library/playlists/{quote(playlist_id, safe='')}/tracks",
                {"limit": 100},
            )
        ]

    def search_tracks(self, track):
        if track.isrc:
            songs = self.request(
                "GET",
                f"/v1/catalog/{self.storefront}/songs",
                params={"filter[isrc]": track.isrc},
            ).get("data", [])
            if songs:
                return [normalize_song(x) for x in songs]
        data = self.request(
            "GET",
            f"/v1/catalog/{self.storefront}/search",
            params={
                "types": "songs",
                "limit": 5,
                "term": " ".join(track.artists + [normalize(track.title)]),
            },
        )
        return [normalize_song(x) for x in data.get("results", {}).get("songs", {}).get("data", [])]

    def create_playlist(self, name, description):
        return self.request(
            "POST",
            "/v1/me/library/playlists",
            json={"attributes": {"name": name, "description": description}},
        )["data"][0]["id"]

    def add_tracks(self, playlist_id, tracks):
        self.request(
            "POST",
            f"/v1/me/library/playlists/{quote(playlist_id, safe='')}/tracks",
            json={"data": [{"id": t.provider_id, "type": "songs"} for t in tracks]},
        )
