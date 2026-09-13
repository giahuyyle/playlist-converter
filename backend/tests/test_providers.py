import httpx
import pytest

from app.providers.base import ProviderError
from app.providers.spotify import SpotifyProvider
from app.providers.youtube import YouTubeProvider


def use_transport(provider, handler):
    base = provider.client.base_url
    provider.client.close()
    provider.client = httpx.Client(base_url=base, transport=httpx.MockTransport(handler))
    return provider


@pytest.mark.parametrize(
    "status,retryable,ambiguous,method",
    [
        (429, True, False, "POST"),
        (500, False, True, "POST"),
        (500, True, False, "GET"),
        (403, False, False, "GET"),
        (404, False, False, "GET"),
        (401, False, False, "GET"),
    ],
)
def test_http_error_classification(status, retryable, ambiguous, method):
    p = use_transport(
        YouTubeProvider("secret"),
        lambda r: httpx.Response(status, json={"error": {}}, headers={"Retry-After": "30"}),
    )
    with pytest.raises(ProviderError) as raised:
        p.request(method, "playlists")
    assert raised.value.retryable == retryable
    assert raised.value.ambiguous == ambiguous
    assert "secret" not in str(raised.value)
    p.close()


def test_youtube_pagination_batch_duration_and_order():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.path.endswith("/playlistItems"):
            if request.url.params.get("pageToken"):
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "contentDetails": {"videoId": "a"},
                                "snippet": {"title": "Artist - Song"},
                            }
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "contentDetails": {"videoId": "a"},
                            "snippet": {"title": "Artist - Song"},
                        }
                    ],
                    "nextPageToken": "next",
                },
            )
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "a",
                        "snippet": {"title": "Artist - Song"},
                        "contentDetails": {"duration": "PT3M"},
                    }
                ]
            },
        )

    p = use_transport(YouTubeProvider("token"), handler)
    result = p.get_playlist_tracks("playlist")
    assert len(result) == 2 and all(t.duration_ms == 180000 for t in result)
    assert len(calls) == 3
    p.close()


def test_spotify_current_items_and_private_create():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, request.content))
        if request.method == "POST":
            return httpx.Response(201, json={"id": "new"})
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "item": {
                            "id": "track",
                            "name": "Song",
                            "artists": [{"name": "Artist"}],
                        }
                    }
                ],
                "next": None,
            },
        )

    p = use_transport(SpotifyProvider("token"), handler)
    tracks = p.get_playlist_tracks("test")
    assert tracks[0].provider_id == "track"
    assert p.create_playlist("name", "description") == "new"
    assert calls[0][1].endswith("/items") and b'"public":false' in calls[1][2]
    p.close()


def test_quota_exhaustion_is_not_blindly_retried():
    p = use_transport(
        YouTubeProvider("token"),
        lambda r: httpx.Response(403, json={"error": {"errors": [{"reason": "quotaExceeded"}]}}),
    )
    with pytest.raises(ProviderError, match="quota exhausted") as raised:
        p.get_playlists()
    assert not raised.value.retryable
    p.close()


def test_apple_pagination_catalog_and_write_contract(monkeypatch):
    from app.models.track import Track
    from app.providers.apple import AppleProvider

    monkeypatch.setattr("app.providers.apple.developer_token", lambda: "developer")
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "POST":
            if request.url.path.endswith("/tracks"):
                return httpx.Response(204)
            return httpx.Response(201, json={"data": [{"id": "p.new"}]})
        if request.url.path.endswith("/search"):
            return httpx.Response(
                200,
                json={
                    "results": {
                        "songs": {
                            "data": [
                                {
                                    "id": "123",
                                    "attributes": {"name": "Song", "artistName": "Artist"},
                                }
                            ]
                        }
                    }
                },
            )
        if request.url.params.get("offset"):
            return httpx.Response(
                200, json={"data": [{"id": "p.two", "attributes": {"name": "Two"}}]}
            )
        return httpx.Response(
            200,
            json={
                "data": [{"id": "p.one", "attributes": {"name": "One"}}],
                "next": "/v1/me/library/playlists?offset=1",
            },
        )

    provider = AppleProvider("user-token")
    provider.client.close()
    provider.client = httpx.Client(
        base_url="https://api.music.apple.com/",
        headers={"Authorization": "Bearer developer", "Music-User-Token": "user-token"},
        transport=httpx.MockTransport(handler),
    )
    assert len(provider.get_playlists()) == 2
    track = Track(title="Song", artists=["Artist"], provider="youtube", provider_id="source")
    result = provider.search_tracks(track)
    assert result[0].provider_id == "123"
    assert provider.create_playlist("name", "description") == "p.new"
    provider.add_tracks("p.new", result)
    assert calls[-1].headers["Music-User-Token"] == "user-token"
    assert b'"type":"songs"' in calls[-1].content
    provider.close()
