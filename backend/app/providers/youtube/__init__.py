import re

from app.models.track import Track
from app.providers.base import HTTPProvider, ProviderError
from app.services.matching import normalize


def duration(value):
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    return (
        sum(
            int(v or 0) * factor
            for v, factor in zip(match.groups(), (3600000, 60000, 1000), strict=True)
        )
        if match
        else None
    )


def normalize_video(item):
    snippet = item.get("snippet", {})
    title = snippet.get("title", "Unavailable video")
    channel = snippet.get("videoOwnerChannelTitle") or snippet.get("channelTitle", "")
    artist = re.sub(r"\s*(- Topic|VEVO)$", "", channel, flags=re.IGNORECASE).strip()
    if " - " in title:
        artist, title = title.split(" - ", 1)
    video_id = (
        item.get("contentDetails", {}).get("videoId")
        or snippet.get("resourceId", {}).get("videoId")
        or item.get("id")
    )
    if isinstance(video_id, dict):
        video_id = video_id.get("videoId", "")
    thumbnails = snippet.get("thumbnails", {})
    return Track(
        title=title,
        artists=[artist] if artist else [],
        provider="youtube",
        provider_id=video_id or "",
        duration_ms=duration(item.get("contentDetails", {}).get("duration")),
        provider_url=f"https://www.youtube.com/watch?v={video_id}",
        artwork_url=thumbnails.get("medium", thumbnails.get("default", {})).get("url"),
        official=bool(re.search(r"(VEVO| - Topic)$", channel, re.IGNORECASE)),
        music=snippet.get("categoryId") == "10",
        available=title not in ("Deleted video", "Private video", "Unavailable video"),
    )


class YouTubeProvider(HTTPProvider):
    def __init__(self, token):
        super().__init__(token, "https://www.googleapis.com/youtube/v3/")

    def pages(self, path, params):
        result = []
        while True:
            data = self.request("GET", path, params={**params, "maxResults": 50})
            result.extend(data.get("items", []))
            if not data.get("nextPageToken"):
                return result
            params = {**params, "pageToken": data["nextPageToken"]}

    def playlist(self, item):
        return {
            "id": item["id"],
            "name": item["snippet"]["title"],
            "description": item["snippet"].get("description", ""),
            "track_count": item.get("contentDetails", {}).get("itemCount"),
            "url": f"https://www.youtube.com/playlist?list={item['id']}",
        }

    def get_playlists(self):
        return [
            self.playlist(x)
            for x in self.pages("playlists", {"part": "snippet,contentDetails", "mine": "true"})
        ]

    def get_playlist(self, playlist_id):
        items = self.request(
            "GET",
            "playlists",
            params={"part": "snippet,contentDetails", "id": playlist_id},
        ).get("items", [])
        if not items:
            raise ProviderError("Playlist is unavailable")
        return self.playlist(items[0])

    def get_playlist_tracks(self, playlist_id):
        items = self.pages(
            "playlistItems",
            {"part": "snippet,contentDetails", "playlistId": playlist_id},
        )
        ids = [x.get("contentDetails", {}).get("videoId") for x in items]
        videos = {}
        for offset in range(0, len(ids), 50):
            batch = [v for v in ids[offset : offset + 50] if v]
            if batch:
                data = self.request(
                    "GET",
                    "videos",
                    params={"part": "snippet,contentDetails", "id": ",".join(batch)},
                )
                videos.update({v["id"]: v for v in data.get("items", [])})
        return [
            normalize_video(videos.get(video_id, item))
            for video_id, item in zip(ids, items, strict=True)
        ]

    def search_tracks(self, track):
        data = self.request(
            "GET",
            "search",
            params={
                "part": "snippet",
                "type": "video",
                "videoCategoryId": "10",
                "maxResults": 5,
                "q": " ".join(track.artists + [normalize(track.title)]),
            },
        )
        ids = [x["id"]["videoId"] for x in data.get("items", [])]
        if not ids:
            return []
        return [
            normalize_video(x)
            for x in self.request(
                "GET",
                "videos",
                params={"part": "snippet,contentDetails", "id": ",".join(ids)},
            ).get("items", [])
        ]

    def create_playlist(self, name, description):
        return self.request(
            "POST",
            "playlists",
            params={"part": "snippet,status"},
            json={
                "snippet": {"title": name, "description": description},
                "status": {"privacyStatus": "private"},
            },
        )["id"]

    def add_tracks(self, playlist_id, tracks):
        for track in tracks:
            self.request(
                "POST",
                "playlistItems",
                params={"part": "snippet"},
                json={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {
                            "kind": "youtube#video",
                            "videoId": track.provider_id,
                        },
                    }
                },
            )
