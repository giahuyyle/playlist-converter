import os
import tempfile

os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or "sqlite:///" + tempfile.mktemp(
    prefix="playlist-tests-", suffix=".db"
)
os.environ["DEMO_MODE"] = "true"
os.environ["SECRET_KEY"] = "test-only-secret-with-at-least-32-characters"
os.environ["DEV_ENV"] = "dev"
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import Base, Session, engine
from app.main import app
from app.models.entities import Job
from app.workers.runner import process


@pytest.fixture(autouse=True)
def database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def client():
    with TestClient(app, headers={"X-Requested-With": "PlaylistConverter"}) as client:
        client.post("/auth/session")
        client.post("/auth/demo/youtube")
        client.post("/auth/demo/apple")
        client.post("/auth/demo/spotify")
        yield client


def run_jobs():
    with Session() as db:
        ids = list(db.scalars(select(Job.id).where(Job.status == "pending")))
    for job_id in ids:
        process(job_id)
