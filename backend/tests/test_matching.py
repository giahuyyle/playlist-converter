import pytest

from app.models.track import Track
from app.providers.apple import normalize_song
from app.providers.youtube import duration, normalize_video
from app.services.matching import normalize, rank, score

SONGS = [
    ("Blinding Lights", "The Weeknd"),
    ("Get Lucky", "Daft Punk"),
    ("Dreams", "Fleetwood Mac"),
    ("Teardrop", "Massive Attack"),
    ("Midnight City", "M83"),
    ("Breathe", "Télépopmusik"),
    ("Jóga", "Björk"),
    ("Everything In Its Right Place", "Radiohead"),
    ("Around the World", "Daft Punk"),
    ("Redbone", "Childish Gambino"),
    ("Bad Guy", "Billie Eilish"),
    ("Electric Feel", "MGMT"),
    ("Royals", "Lorde"),
    ("Reckoner", "Radiohead"),
    ("Losing My Religion", "R.E.M."),
    ("Heroes", "David Bowie"),
    ("Purple Rain", "Prince"),
    ("Feel Good Inc.", "Gorillaz"),
    ("Heartbeats", "The Knife"),
    ("Somebody Else", "The 1975"),
    ("Time", "Pink Floyd"),
    ("Clair de Lune", "Claude Debussy"),
    ("東京", "くるり"),
    ("Águas de Março", "Elis Regina"),
    ("Dancing On My Own", "Robyn"),
]


# 100 deterministic synthetic cases, not a claim of measured real-provider accuracy.
@pytest.mark.parametrize("title,artist", SONGS)
@pytest.mark.parametrize(
    "noise", [" (Official Video)", " [Lyrics]", " — Official Audio 4K", " (Visualizer)"]
)
def test_100_track_matching_corpus(title, artist, noise):
    source = Track(
        title=title + noise,
        artists=[artist],
        provider="youtube",
        provider_id="source",
        duration_ms=200000,
    )
    correct = Track(
        title=title,
        artists=[artist],
        provider="apple",
        provider_id="correct",
        duration_ms=200040,
    )
    wrong_version = correct.model_copy(update={"title": title + " (Live)", "provider_id": "live"})
    wrong_artist = correct.model_copy(
        update={"artists": ["Another artist"], "provider_id": "wrong"}
    )
    result = rank(source, [wrong_version, wrong_artist, correct])
    assert result[0]["track"]["provider_id"] == "correct"
    assert result[0]["confidence"] >= 0.9
    assert result[1]["confidence"] < 0.9


def test_versions_and_duration():
    source = Track(
        title="Song",
        artists=["Artist"],
        provider="apple",
        provider_id="1",
        duration_ms=200000,
    )
    for version in [
        "Live",
        "Remix",
        "Radio Edit",
        "Acoustic",
        "Instrumental",
        "Slowed",
        "10 Hours",
        "Cover",
    ]:
        assert score(source, source.model_copy(update={"title": f"Song ({version})"})) < 0.7
    assert score(source, source.model_copy(update={"duration_ms": 3600000})) < 0.7
    assert score(source.model_copy(update={"artists": []}), source) < 0.9
    assert normalize("ＡＢＣ (Official Music Video)") == "abc"


def test_youtube_and_apple_normalization():
    yt = normalize_video(
        {
            "id": "abc",
            "snippet": {
                "title": "The Weeknd - Blinding Lights (Official Video)",
                "channelTitle": "TheWeekndVEVO",
                "categoryId": "10",
            },
            "contentDetails": {"duration": "PT3M20S"},
        }
    )
    assert yt.artists == ["The Weeknd"] and yt.duration_ms == 200000 and yt.official
    apple = normalize_song(
        {
            "id": "i.library",
            "attributes": {
                "name": "Blinding Lights",
                "artistName": "The Weeknd",
                "playParams": {"catalogId": "123"},
                "artwork": {"url": "https://example.com/{w}x{h}.jpg"},
            },
        }
    )
    assert apple.provider_id == "123" and "160x160" in apple.artwork_url
    assert duration("PT1H2M3S") == 3723000
    assert not normalize_video({"id": "x", "snippet": {"title": "Private video"}}).available


def test_feature_credit_spelling_and_remastered_versions():
    assert normalize("Artist featuring Guest") == normalize("Artist ft. Guest")
    assert normalize("Artist feat. Guest") == normalize("Artist featuring Guest")
    source = Track(title="Song", artists=["Artist"], provider="apple", provider_id="1")
    assert score(source, source.model_copy(update={"title": "Song (Remastered)"})) < 0.7
