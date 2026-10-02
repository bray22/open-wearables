from functools import lru_cache
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.schemas import OpenWearablesUser


class OpenWearablesError(Exception):
    pass


class OpenWearablesUserNotFoundError(OpenWearablesError):
    pass


class OpenWearablesUnavailableError(OpenWearablesError):
    pass


class OpenWearablesResponseError(OpenWearablesError):
    pass


class OpenWearablesClient:
    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None) -> None:
        if not settings.open_wearables_api_key:
            raise ValueError("OPEN_WEARABLES_API_KEY is required")
        timeout = httpx.Timeout(
            connect=settings.open_wearables_connect_timeout_seconds,
            read=settings.open_wearables_read_timeout_seconds,
            write=settings.open_wearables_read_timeout_seconds,
            pool=settings.open_wearables_connect_timeout_seconds,
        )
        self._client = httpx.Client(
            base_url=settings.open_wearables_base_url.rstrip("/"),
            headers={"X-Open-Wearables-API-Key": settings.open_wearables_api_key},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def get_user(self, user_id: str) -> OpenWearablesUser:
        try:
            response = self._client.get(f"/api/v1/users/{user_id}")
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise OpenWearablesUnavailableError("Open Wearables is unavailable") from exc

        if response.status_code == httpx.codes.NOT_FOUND:
            raise OpenWearablesUserNotFoundError("Mapped Open Wearables user was not found")
        if response.status_code >= 500:
            raise OpenWearablesUnavailableError("Open Wearables is unavailable")
        if response.is_error:
            raise OpenWearablesResponseError(f"Open Wearables returned HTTP {response.status_code}")
        try:
            return OpenWearablesUser.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise OpenWearablesResponseError("Open Wearables returned an invalid user response") from exc


@lru_cache
def get_open_wearables_client() -> OpenWearablesClient:
    return OpenWearablesClient(get_settings())


def close_open_wearables_client() -> None:
    if get_open_wearables_client.cache_info().currsize:
        get_open_wearables_client().close()
        get_open_wearables_client.cache_clear()


OpenWearablesUserPayload = dict[str, Any]
