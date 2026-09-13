import time
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from app.core.config import config
from app.core.database import Session
from app.core.security import decrypt
from app.models.entities import Account
from app.services.tokens import access_token


def test_oauth_state_bound_to_session_and_consumed(client, monkeypatch):
    monkeypatch.setattr(config, "google_client_id", "client")
    monkeypatch.setattr(config, "google_client_secret", "secret")
    response = client.get("/auth/youtube", follow_redirects=False)
    params = parse_qs(urlparse(response.headers["location"]).query)
    assert params["code_challenge_method"] == ["S256"]
    assert params["access_type"] == ["offline"]
    state = params["state"][0]

    def exchange(provider, data):
        assert provider == "youtube" and len(data["code_verifier"]) > 40
        return {"access_token": "new-access", "refresh_token": "refresh", "expires_in": 3600}

    monkeypatch.setattr("app.api.auth.exchange", exchange)
    result = client.get(f"/auth/youtube/callback?state={state}&code=test", follow_redirects=False)
    assert result.status_code == 303
    assert "new-access" not in result.text
    assert client.get(f"/auth/youtube/callback?state={state}&code=test").status_code == 400
    with Session() as db:
        account = db.scalar(select(Account).where(Account.provider == "youtube"))
        assert decrypt(account.refresh_token) == "refresh"


def test_token_refresh_retains_refresh_token(client, monkeypatch):
    from app.core.security import encrypt

    with Session() as db:
        account = db.scalar(select(Account).where(Account.provider == "youtube"))
        account.expires_at = time.time() - 1
        account.refresh_token = encrypt("original-refresh")
        db.commit()

        def exchange(provider, data):
            assert data["refresh_token"] == "original-refresh"
            return {"access_token": "renewed", "expires_in": 3600}

        monkeypatch.setattr("app.services.tokens.exchange", exchange)
        assert access_token(db, account) == "renewed"
        assert decrypt(account.refresh_token) == "original-refresh"
