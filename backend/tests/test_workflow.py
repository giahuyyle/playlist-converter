import pytest
from conftest import run_jobs
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import Session
from app.main import app
from app.models.entities import Account, Job, Mapping
from app.providers.base import ProviderError
from app.providers.demo import DemoProvider
from app.workers.runner import process


def create(client, source="youtube", destination="apple", key="one"):
    response = client.post(
        "/api/conversions",
        json={
            "source_provider": source,
            "destination_provider": destination,
            "source_playlist_id": "demo",
            "name": "Test favorites",
            "request_key": key,
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


def ready(client, source="youtube", destination="apple"):
    cid = create(client, source, destination)
    run_jobs()
    data = client.get(f"/api/conversions/{cid}").json()
    assert data["status"] == "review_required", data
    assert data["destination_playlist_id"] is None
    for match in data["matches"]:
        if match["status"] in ("review", "unmatched"):
            body = {"candidate_index": 0} if match["candidates"] else {"skip": True}
            assert (
                client.patch(f"/api/conversions/{cid}/matches/{match['id']}", json=body).status_code
                == 200
            )
    return cid


@pytest.mark.parametrize(
    "source,destination",
    [
        ("youtube", "apple"),
        ("apple", "youtube"),
        ("spotify", "apple"),
        ("apple", "spotify"),
        ("youtube", "spotify"),
        ("spotify", "youtube"),
    ],
)
def test_full_workflow_and_duplicate_delivery(client, source, destination):
    cid = ready(client, source, destination)
    assert client.post(f"/api/conversions/{cid}/confirm").status_code == 202
    assert client.post(f"/api/conversions/{cid}/confirm").status_code == 202
    run_jobs()
    data = client.get(f"/api/conversions/{cid}").json()
    assert data["status"] == "completed"
    assert data["transferred_tracks"] == 5
    assert data["failed_tracks"] == 1
    with Session() as db:
        ids = list(db.scalars(select(Job.id)))
    for jid in ids:
        process(jid)
    tracks = client.get(
        f"/api/playlists/{destination}/{data['destination_playlist_id']}/tracks"
    ).json()
    assert len(tracks) == 5
    assert [t["title"] for t in tracks] == [
        "Blinding Lights",
        "Get Lucky",
        "Dreams",
        "Teardrop",
        "Midnight City",
    ]


def test_confirmation_blocks_unresolved_and_ownership(client):
    cid = create(client)
    run_jobs()
    assert client.post(f"/api/conversions/{cid}/confirm").status_code == 409
    with TestClient(app, headers={"X-Requested-With": "PlaylistConverter"}) as other:
        other.post("/auth/session")
        assert other.get(f"/api/conversions/{cid}").status_code == 404
        assert other.post(f"/api/conversions/{cid}/confirm").status_code == 404
        assert other.get(f"/api/conversions/{cid}/events").status_code == 404
        assert other.get("/api/conversions").json() == []


def test_idempotency_and_encryption(client):
    cid = create(client)
    assert create(client) == cid
    result = client.post(
        "/api/conversions",
        json={
            "source_provider": "youtube",
            "destination_provider": "spotify",
            "source_playlist_id": "demo",
            "name": "Test favorites",
            "request_key": "one",
        },
    )
    assert result.status_code == 409
    with Session() as db:
        assert len(list(db.scalars(select(Job)))) == 1
        assert all(a.access_token != "demo" for a in db.scalars(select(Account)))


def test_csrf_and_invalid_oauth(client):
    assert client.post("/auth/session", headers={"X-Requested-With": ""}).status_code == 403
    assert (
        client.post("/auth/session", headers={"Origin": "https://attacker.example"}).status_code
        == 403
    )
    assert client.get("/auth/youtube/callback?state=wrong&code=fake").status_code == 400


def test_transient_failure_checkpoints_and_retries(client, monkeypatch):
    original = DemoProvider.search_tracks
    seen = []

    def flaky(self, track):
        seen.append(track.title)
        if track.title == "Dreams" and seen.count("Dreams") == 1:
            raise ProviderError("Rate limited", retryable=True, retry_after=20)
        return original(self, track)

    monkeypatch.setattr(DemoProvider, "search_tracks", flaky)
    cid = create(client)
    run_jobs()
    data = client.get(f"/api/conversions/{cid}").json()
    assert data["status"] == "queued"
    assert len([m for m in data["matches"] if m["status"] != "pending"]) == 2
    with Session() as db:
        job = db.scalar(select(Job))
        job.available_at = 0
        db.commit()
    run_jobs()
    assert client.get(f"/api/conversions/{cid}").json()["status"] == "review_required"
    assert seen.count("Blinding Lights") == 1


@pytest.mark.parametrize("operation", ["create", "append"])
def test_ambiguous_write_is_reconciled_without_duplication(client, monkeypatch, operation):
    cid = ready(client)
    method = "create_playlist" if operation == "create" else "add_tracks"
    original = getattr(DemoProvider, method)

    def ambiguous(self, *args):
        original(self, *args)
        raise ProviderError("Connection lost after write", ambiguous=True)

    monkeypatch.setattr(DemoProvider, method, ambiguous)
    client.post(f"/api/conversions/{cid}/confirm")
    run_jobs()
    data = client.get(f"/api/conversions/{cid}").json()
    assert data["status"] == "failed" and data["pending_write"]
    assert client.post(f"/api/conversions/{cid}/retry").status_code == 409
    monkeypatch.setattr(DemoProvider, method, original)
    assert client.post(f"/api/conversions/{cid}/reconcile").status_code == 200
    assert client.post(f"/api/conversions/{cid}/retry").status_code == 202
    run_jobs()
    data = client.get(f"/api/conversions/{cid}").json()
    assert data["status"] == "completed"
    assert (
        len(client.get(f"/api/playlists/apple/{data['destination_playlist_id']}/tracks").json())
        == 5
    )
    assert len(client.get("/api/playlists/apple").json()) == 2


def test_absent_uncertain_write_is_not_replayed(client, monkeypatch):
    cid = ready(client)

    def uncertain(self, *args):
        raise ProviderError("Timeout before response", ambiguous=True)

    monkeypatch.setattr(DemoProvider, "create_playlist", uncertain)
    client.post(f"/api/conversions/{cid}/confirm")
    run_jobs()
    assert client.post(f"/api/conversions/{cid}/reconcile").status_code == 400
    assert client.post(f"/api/conversions/{cid}/retry").status_code == 409


def test_verified_cache_is_scoped_and_reused(client, monkeypatch):
    ready(client)
    with Session() as db:
        assert any(m.verified for m in db.scalars(select(Mapping)))

    def no_search(self, track):
        if track.title != "Lost recording":
            raise AssertionError("Expected cached mapping")
        return []

    monkeypatch.setattr(DemoProvider, "search_tracks", no_search)
    cid = create(client, key="two")
    run_jobs()
    assert client.get(f"/api/conversions/{cid}").json()["status"] == "review_required"


def test_sse_final_snapshot(client):
    cid = ready(client)
    response = client.get(f"/api/conversions/{cid}/events")
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "review_required" in response.text


def test_permanent_failure_and_retry_exhaustion(client, monkeypatch):
    def fail(self, track):
        raise ProviderError("Unavailable", retryable=True)

    monkeypatch.setattr(DemoProvider, "search_tracks", fail)
    cid = create(client)
    for _ in range(5):
        with Session() as db:
            job = db.scalar(select(Job))
            job.available_at = 0
            db.commit()
        run_jobs()
    with Session() as db:
        assert db.scalar(select(Job)).status == "dead"
    assert client.get(f"/api/conversions/{cid}").json()["status"] == "failed"


def test_cache_upsert_preserves_manual_correction(client):
    from app.models.entities import Conversion, Match
    from app.services.conversions import remember

    cid = ready(client)
    with Session() as db:
        c = db.get(Conversion, cid)
        match = db.scalar(select(Match).where(Match.conversion_id == cid, Match.status == "manual"))
        original = dict(match.destination)
        match.destination = {**original, "provider_id": "wrong"}
        remember(db, c, match, "us", verified=False)
        db.commit()
        mapping = db.scalar(select(Mapping).where(Mapping.source_id == match.source["provider_id"]))
        assert mapping.verified and mapping.destination == original
