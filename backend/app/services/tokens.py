import time

import httpx
from sqlalchemy import select

from app.core.config import config
from app.core.security import decrypt, encrypt
from app.models.entities import Account
from app.providers.base import ProviderError

OAUTH = {
    "youtube": {
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "scope": "https://www.googleapis.com/auth/youtube.force-ssl",
    },
    "spotify": {
        "authorize": "https://accounts.spotify.com/authorize",
        "token": "https://accounts.spotify.com/api/token",
        "scope": "playlist-read-private playlist-read-collaborative playlist-modify-private playlist-modify-public user-read-private",
    },
}


def credentials(provider):
    prefix = "google" if provider == "youtube" else provider
    return {
        key: getattr(config, f"{prefix}_{key}")
        for key in ("client_id", "client_secret", "redirect_uri")
    }


def exchange(provider, data):
    c = credentials(provider)
    try:
        response = httpx.post(
            OAUTH[provider]["token"],
            data={
                **data,
                "client_id": c["client_id"],
                "client_secret": c["client_secret"],
            },
            timeout=30,
        )
    except httpx.HTTPError:
        raise ProviderError("Authorization service is unavailable", retryable=True) from None
    if response.status_code != 200:
        raise ProviderError(
            "Authorization failed; reconnect your account",
            retryable=response.status_code >= 500 or response.status_code == 429,
        )
    return response.json()


def save_account(db, user_id, provider, tokens, storefront="us"):
    account = db.scalar(
        select(Account).where(Account.user_id == user_id, Account.provider == provider)
    )
    if not account:
        account = Account(user_id=user_id, provider=provider)
        db.add(account)
    account.access_token = encrypt(tokens["access_token"])
    if tokens.get("refresh_token"):
        account.refresh_token = encrypt(tokens["refresh_token"])
    account.expires_at = time.time() + tokens["expires_in"] if tokens.get("expires_in") else None
    account.storefront = storefront
    db.commit()
    return account


def access_token(db, account):
    if account.expires_at and account.expires_at < time.time() + 300:
        # Serialize refreshes, including across API/worker processes.
        account = db.scalar(
            select(Account)
            .where(Account.id == account.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if account is None:
            raise ProviderError("Account disconnected; reconnect before retrying")
        if account.expires_at and account.expires_at < time.time() + 300:
            if not account.refresh_token:
                raise ProviderError("Provider authorization expired; reconnect your account")
            tokens = exchange(
                account.provider,
                {
                    "grant_type": "refresh_token",
                    "refresh_token": decrypt(account.refresh_token),
                },
            )
            account = save_account(
                db, account.user_id, account.provider, tokens, account.storefront
            )
        else:
            db.commit()
    return decrypt(account.access_token)
