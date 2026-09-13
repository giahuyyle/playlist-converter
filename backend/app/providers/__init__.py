from contextlib import contextmanager

from sqlalchemy import select

from app.core.config import config
from app.models.entities import Account
from app.providers.base import ProviderError


@contextmanager
def provider_for(db, user_id, name):
    from app.services.tokens import access_token

    account = db.scalar(select(Account).where(Account.user_id == user_id, Account.provider == name))
    if not account:
        raise ProviderError(f"Connect your {name} account first")
    if config.demo_mode:
        from app.providers.demo import DemoProvider

        provider = DemoProvider(name, user_id)
    else:
        from app.providers.apple import AppleProvider
        from app.providers.spotify import SpotifyProvider
        from app.providers.youtube import YouTubeProvider

        factories = {
            "apple": lambda t: AppleProvider(t, account.storefront),
            "youtube": YouTubeProvider,
            "spotify": SpotifyProvider,
        }
        if name not in factories:
            raise ProviderError("Unsupported provider")
        provider = factories[name](access_token(db, account))
        if name != "apple":
            # A large playlist may take longer than one access token's lifetime.
            provider.prepare_auth = lambda: access_token(db, account)
    try:
        yield provider
    finally:
        if hasattr(provider, "close"):
            provider.close()
