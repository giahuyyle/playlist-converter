"""Run against disposable, migrated PostgreSQL + RabbitMQ with DEMO_MODE=true.

Uses two actual worker processes and duplicate RabbitMQ messages. Does not reset data.
"""

import json
import subprocess
import sys
import time
import uuid

import pika
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import config
from app.core.database import Session
from app.main import app
from app.models.entities import Job
from app.workers.runner import connection, relay_once


def wait(client, cid, status):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        data = client.get(f"/api/conversions/{cid}").json()
        if data["status"] == status:
            return data
        if data["status"] == "failed":
            raise AssertionError(data["error"])
        time.sleep(0.25)
    raise AssertionError(f"Timed out waiting for {status}")


def main():
    if not config.demo_mode or not config.database_url.startswith("postgresql"):
        raise RuntimeError("Requires DEMO_MODE=true and disposable PostgreSQL infrastructure")
    workers = [
        subprocess.Popen([sys.executable, "-m", "app.workers.runner", "worker"]) for _ in range(2)
    ]
    conn = None
    try:
        conn, channel = connection()
        channel.confirm_delivery()
        with TestClient(app, headers={"X-Requested-With": "PlaylistConverter"}) as client:
            client.post("/auth/session")
            client.post("/auth/demo/youtube")
            client.post("/auth/demo/apple")
            response = client.post(
                "/api/conversions",
                json={
                    "source_provider": "youtube",
                    "destination_provider": "apple",
                    "source_playlist_id": "demo",
                    "name": "Queue smoke",
                    "request_key": str(uuid.uuid4()),
                },
            )
            assert response.status_code == 202, response.text
            cid = response.json()["id"]
            relay_once(channel)
            data = wait(client, cid, "review_required")
            for match in data["matches"]:
                if match["status"] in ("review", "unmatched"):
                    body = {"candidate_index": 0} if match["candidates"] else {"skip": True}
                    assert (
                        client.patch(
                            f"/api/conversions/{cid}/matches/{match['id']}", json=body
                        ).status_code
                        == 200
                    )
            assert client.post(f"/api/conversions/{cid}/confirm").status_code == 202
            with Session() as db:
                job = db.scalar(
                    select(Job).where(Job.conversion_id == cid, Job.status == "pending")
                )
                job_id = job.id
            relay_once(channel)
            for _ in range(3):
                channel.basic_publish(
                    exchange="",
                    routing_key="conversion.requested",
                    body=json.dumps({"job_id": job_id, "conversion_id": cid}),
                    properties=pika.BasicProperties(delivery_mode=2),
                )
            result = wait(client, cid, "completed")
            tracks = client.get(
                f"/api/playlists/apple/{result['destination_playlist_id']}/tracks"
            ).json()
            assert len(tracks) == 5 and result["transferred_tracks"] == 5
            assert len(client.get("/api/playlists/apple").json()) == 2
            print(
                "PASS: PostgreSQL outbox → RabbitMQ → two workers → review → confirmed transfer; duplicate delivery produced one playlist with five tracks."
            )
    finally:
        for worker in workers:
            worker.terminate()
        for worker in workers:
            worker.wait(timeout=10)
        if conn and conn.is_open:
            conn.close()


if __name__ == "__main__":
    main()
