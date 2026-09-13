from abc import ABC, abstractmethod

import httpx


class ProviderError(Exception):
    def __init__(self, message, retryable=False, ambiguous=False, retry_after=0):
        super().__init__(message)
        self.retryable = retryable
        self.ambiguous = ambiguous
        self.retry_after = retry_after


class Provider(ABC):
    @abstractmethod
    def get_playlists(self): ...
    @abstractmethod
    def get_playlist(self, playlist_id): ...
    @abstractmethod
    def get_playlist_tracks(self, playlist_id): ...
    @abstractmethod
    def search_tracks(self, track): ...
    @abstractmethod
    def create_playlist(self, name, description): ...
    @abstractmethod
    def add_tracks(self, playlist_id, tracks): ...


class HTTPProvider(Provider):
    def __init__(self, token, base_url, headers=None):
        self.prepare_auth = None
        self.client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}", **(headers or {})},
            timeout=30,
        )

    def close(self):
        self.client.close()

    def request(self, method, path, **kwargs):
        # Writes are never automatically replayed: timeouts/5xx may follow a successful write.
        if self.prepare_auth:
            self.client.headers["Authorization"] = f"Bearer {self.prepare_auth()}"
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.HTTPError:
            raise ProviderError(
                "Provider connection failed",
                retryable=method == "GET",
                ambiguous=method != "GET",
            ) from None
        if response.status_code >= 400:
            status = response.status_code
            reason = ""
            try:
                reason = str(
                    response.json().get("error", {}).get("errors", [{}])[0].get("reason", "")
                )
            except (ValueError, AttributeError, IndexError, TypeError):
                pass
            if "quota" in reason.lower():
                raise ProviderError("YouTube quota exhausted; retry after your quota resets")
            try:
                delay = min(86400, max(0, float(response.headers.get("Retry-After", 0))))
            except ValueError:
                delay = 60
            message = {
                401: "Provider authorization expired; reconnect your account",
                403: "Provider denied access; check permissions and quota",
                404: "Provider item is unavailable",
                429: "Provider rate limit reached",
            }.get(status, f"Provider request failed ({status})")
            raise ProviderError(
                message,
                retryable=status == 429 or (method == "GET" and status >= 500),
                ambiguous=method != "GET" and status >= 500,
                retry_after=delay,
            )
        return response.json() if response.content else {}
