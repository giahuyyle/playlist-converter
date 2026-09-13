import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.core.config import config
from app.core.database import get_db
from app.core.security import current_user, session_token
from app.models.entities import Account, OAuthState, User
from app.providers.apple import AppleProvider, developer_token
from app.services.tokens import OAUTH, credentials, exchange, save_account

router = APIRouter(prefix="/auth", tags=["auth"])


def set_cookie(response, user_id):
    response.set_cookie(
        "session",
        session_token(user_id),
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        max_age=86400 * 30,
    )


@router.post("/session")
def start_session(request: Request, response: Response, db=Depends(get_db)):
    try:
        user = current_user(request, db)
    except HTTPException:
        user = User()
        db.add(user)
        db.commit()
    set_cookie(response, user.id)
    return {"id": user.id}


@router.get("/me")
def me(user=Depends(current_user), db=Depends(get_db)):
    connected = list(db.scalars(select(Account.provider).where(Account.user_id == user.id)))
    return {
        "id": user.id,
        "connected": connected,
        "demo": config.demo_mode,
        "available": {
            "youtube": config.demo_mode
            or bool(config.google_client_id and config.google_client_secret),
            "apple": config.demo_mode
            or bool(config.apple_team_id and config.apple_key_id and config.apple_private_key_path),
            "spotify": config.demo_mode
            or bool(config.spotify_client_id and config.spotify_client_secret),
        },
    }


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("session")
    return {"ok": True}


@router.delete("/{provider}")
def disconnect(provider: str, user=Depends(current_user), db=Depends(get_db)):
    db.execute(delete(Account).where(Account.user_id == user.id, Account.provider == provider))
    db.commit()
    return {"ok": True}


@router.get("/apple/token")
def apple_token(user=Depends(current_user)):
    return {"developer_token": developer_token()}


class AppleAuth(BaseModel):
    music_user_token: str = Field(min_length=1, max_length=10000)


@router.post("/apple")
def connect_apple(body: AppleAuth, user=Depends(current_user), db=Depends(get_db)):
    provider = AppleProvider(body.music_user_token)
    try:
        storefront = provider.request("GET", "/v1/me/storefront")["data"][0]["id"]
    finally:
        provider.close()
    save_account(db, user.id, "apple", {"access_token": body.music_user_token}, storefront)
    return {"connected": True}


@router.post("/demo/{provider}")
def connect_demo(provider: str, user=Depends(current_user), db=Depends(get_db)):
    if not config.demo_mode or provider not in ("apple", "youtube", "spotify"):
        raise HTTPException(404)
    save_account(db, user.id, provider, {"access_token": "demo"})
    return {"connected": True}


@router.get("/{provider}/callback")
def oauth_callback(
    provider: str,
    request: Request,
    state: str = "",
    code: str = "",
    error: str = "",
    user=Depends(current_user),
    db=Depends(get_db),
):
    saved = db.scalar(select(OAuthState).where(OAuthState.id == state).with_for_update())
    if (
        not saved
        or saved.user_id != user.id
        or saved.provider != provider
        or saved.expires_at < time.time()
    ):
        raise HTTPException(400, "Invalid or expired OAuth state")
    verifier = saved.verifier
    db.delete(saved)
    db.commit()
    if error or not code:
        return RedirectResponse(config.frontend_url + "?connection=cancelled", status_code=303)
    tokens = exchange(
        provider,
        {
            "code": code,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": credentials(provider)["redirect_uri"],
        },
    )
    save_account(db, user.id, provider, tokens)
    return RedirectResponse(config.frontend_url, status_code=303)


@router.get("/{provider}")
def oauth_login(provider: str, user=Depends(current_user), db=Depends(get_db)):
    if provider not in OAUTH:
        raise HTTPException(404)
    c = credentials(provider)
    if not c["client_id"] or not c["client_secret"]:
        raise HTTPException(503, f"{provider} is not configured on the server")
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    db.execute(delete(OAuthState).where(OAuthState.expires_at < time.time()))
    db.add(
        OAuthState(
            id=state,
            user_id=user.id,
            provider=provider,
            verifier=verifier,
            expires_at=time.time() + 600,
        )
    )
    db.commit()
    params = {
        "client_id": c["client_id"],
        "redirect_uri": c["redirect_uri"],
        "response_type": "code",
        "scope": OAUTH[provider]["scope"],
        "state": state,
        "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode(),
        "code_challenge_method": "S256",
    }
    if provider == "youtube":
        params.update(access_type="offline", prompt="consent")
    return RedirectResponse(OAUTH[provider]["authorize"] + "?" + urlencode(params))
