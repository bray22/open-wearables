from datetime import datetime, timedelta, timezone
from html import escape
from typing import Annotated, Literal
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings
from app.constants.provider_urls import from_url_slug
from app.database import DbSession
from app.schemas.enums import ProviderName
from app.schemas.model_crud.credentials import AuthorizationURLResponse
from app.schemas.model_crud.data_priority import (
    BulkProviderSettingsUpdate,
    ProviderSettingRead,
    ProviderSettingUpdate,
)
from app.services import DeveloperDep, user_connection_service
from app.services.provider_settings_service import ProviderSettingsService
from app.services.providers.base_strategy import BaseProviderStrategy
from app.services.providers.factory import ProviderFactory

router = APIRouter()
factory = ProviderFactory()
settings_service = ProviderSettingsService()


def resolve_provider(slug: str) -> ProviderName:
    # 400 rather than 404 keeps the response the enum-typed parameter used to give.
    try:
        return ProviderName(from_url_slug(slug))
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown provider: '{slug}'")


def get_oauth_strategy(provider: ProviderName) -> BaseProviderStrategy:
    """Helper to get provider strategy and ensure it supports OAuth."""
    strategy = factory.get_provider(provider.value)

    if not strategy.oauth:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider '{provider.value}' does not support OAuth",
        )
    return strategy


@router.get(
    "/{provider}/authorize",
    summary="Get Provider Authorization URL",
    response_model=AuthorizationURLResponse,
    tags=["External: Providers"],
)
def authorize_provider(
    provider: str,
    user_id: Annotated[UUID, Query(description="User ID to connect")],
    flow_origin: Annotated[Literal["web", "mobile"], Query(description="Client that initiated this flow")] = "web",
):
    """
    Initiate OAuth flow for a provider.

    Returns authorization URL where user should be redirected to log in.
    """
    strategy = get_oauth_strategy(resolve_provider(provider))

    assert strategy.oauth
    auth_url, state = strategy.oauth.get_authorization_url(user_id, flow_origin)
    return AuthorizationURLResponse(authorization_url=auth_url, state=state)


def mobile_oauth_return(
    provider: str,
    status_value: Literal["success", "error"],
    error_code: str | None = None,
) -> HTMLResponse:
    """Attempt the fixed app deep link and leave a manual fallback if it cannot open."""
    params = {"provider": provider, "status": status_value}
    if error_code:
        params["error"] = error_code
    deep_link = f"elevate://wearables/callback?{urlencode(params)}"
    safe_deep_link = escape(deep_link, quote=True)
    heading = "Connection complete" if status_value == "success" else "Connection not completed"
    message = (
        "Return to ELEVATE to view the connection."
        if status_value == "success"
        else "You can return to ELEVATE and try again."
    )
    html = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<meta http-equiv=\"refresh\" content=\"0;url={safe_deep_link}\">"
        "<title>Return to ELEVATE</title></head><body>"
        f"<main><h1>{heading}</h1><p>{message}</p><a href=\"{safe_deep_link}\">Return to ELEVATE</a></main>"
        "</body></html>"
    )
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


def web_oauth_return() -> str:
    """Build the configured web destination without accepting callback URL input."""
    destination = settings.elevate_web_return_url
    parsed = urlsplit(destination)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path != "/member/wearables"
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("ELEVATE_WEB_RETURN_URL must be an absolute URL ending in /member/wearables.")
    return destination


@router.head("/{provider}/callback", tags=["System: OAuth"])
def probe_oauth_callback(provider: str) -> None:
    """Answer the reachability probe Withings sends when the callback URL is registered."""
    get_oauth_strategy(resolve_provider(provider))


@router.get("/{provider}/callback", tags=["System: OAuth"])
def oauth_callback(
    provider: str,
    db: DbSession,
    code: Annotated[str | None, Query(description="Authorization code from provider")] = None,
    state: Annotated[str | None, Query(description="State parameter for CSRF protection")] = None,
    error: Annotated[str | None, Query()] = None,
    error_description: Annotated[str | None, Query()] = None,
):
    """
    OAuth callback endpoint.

    Provider redirects here after user authorizes. Exchanges code for tokens.
    """
    if not state:
        return RedirectResponse(
            url="/api/v1/oauth/error?message=Missing+OAuth+state",
            status_code=303,
        )

    provider_name = resolve_provider(provider)
    strategy = get_oauth_strategy(provider_name)

    assert strategy.oauth
    oauth_state, code_verifier = strategy.oauth.consume_callback_state(state)

    if error:
        if oauth_state.flow_origin == "mobile":
            error_code = "cancelled" if error == "access_denied" else "provider_error"
            return mobile_oauth_return(provider_name.value, "error", error_code)
        return RedirectResponse(
            url=f"/api/v1/oauth/error?message={error}:+{error_description or 'Unknown+error'}",
            status_code=303,
        )

    if not code:
        if oauth_state.flow_origin == "mobile":
            return mobile_oauth_return(provider_name.value, "error", "provider_error")
        return RedirectResponse(
            url="/api/v1/oauth/error?message=Missing+OAuth+code",
            status_code=303,
        )

    try:
        oauth_state = strategy.oauth.complete_callback(db, code, oauth_state, code_verifier)
    except Exception:
        if oauth_state.flow_origin == "mobile":
            return mobile_oauth_return(provider_name.value, "error", "connection_failed")
        raise

    # Stamp last_synced_at=now so the first periodic sync uses the connection
    # timestamp as its live-sync cursor and won't attempt to pull all history.
    user_connection_service.stamp_last_synced_at(db, oauth_state.user_id, provider_name.value)

    # Grace-period flag: automatically kick off a historical sync so integrators
    # who haven't yet adopted the explicit /sync/historical call still get backfill.
    # Controlled by HISTORICAL_SYNC_ON_CONNECT (default: true).
    if settings.historical_sync_on_connect:
        caps = strategy.capabilities
        if caps.webhook_callback:
            # this code is going to be removed later, so leave inner imports heres
            from app.integrations.celery.tasks import start_garmin_full_backfill

            start_garmin_full_backfill.delay(str(oauth_state.user_id))
        elif caps.rest_pull:
            from app.integrations.celery.tasks import sync_vendor_data

            now = datetime.now(timezone.utc)
            start_date = (now - timedelta(days=90)).isoformat()
            sync_vendor_data.delay(
                user_id=str(oauth_state.user_id),
                start_date=start_date,
                end_date=now.isoformat(),
                providers=[provider_name.value],
                is_historical=True,
            )

    if oauth_state.flow_origin == "mobile":
        return mobile_oauth_return(provider_name.value, "success")

    return RedirectResponse(url=web_oauth_return(), status_code=303)


@router.get("/success", tags=["System: OAuth"])
def oauth_success(
    provider: Annotated[str, Query()],
    user_id: Annotated[str, Query()],
) -> dict:
    """Simple success page after OAuth completion."""
    return {
        "success": True,
        "message": f"Successfully connected to {provider}",
        "user_id": user_id,
        "provider": provider,
    }


@router.get("/error", tags=["System: OAuth"])
def oauth_error(
    message: Annotated[str, Query()] = "OAuth authentication failed",
) -> dict:
    """OAuth error page."""
    return {
        "success": False,
        "message": message,
    }


@router.get("/providers", response_model=list[ProviderSettingRead], tags=["External: Providers"])
def get_providers(
    db: DbSession,
    enabled_only: Annotated[bool, Query(description="Return only enabled providers")] = False,
    cloud_only: Annotated[bool, Query(description="Return only cloud (OAuth) providers")] = False,
):
    """
    Get providers with their configuration and metadata.

    Query params:
    - enabled_only: Filter to only enabled providers (default: False, returns all)
    - cloud_only: Filter to only providers with cloud OAuth API (default: False)

    Returns full provider details including name, icon_url, has_cloud_api, is_enabled.
    """
    all_providers = settings_service.get_all_providers(db)

    return [p for p in all_providers if (not enabled_only or p.is_enabled) and (not cloud_only or p.has_cloud_api)]


@router.put("/providers/{provider}", response_model=ProviderSettingRead, tags=["Internal: Providers"])
def update_provider_setting(
    provider: str,
    update: ProviderSettingUpdate,
    db: DbSession,
    _developer: DeveloperDep,
):
    """Update is_enabled and/or live_sync_mode for a single provider."""
    try:
        return settings_service.update_provider_setting(db, provider, update)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put("/providers", response_model=list[ProviderSettingRead], tags=["Internal: Providers"])
def bulk_update_providers(
    updates: BulkProviderSettingsUpdate,
    db: DbSession,
    _developer: DeveloperDep,
):
    """
    Bulk update provider settings.

    Accepts a map of provider_id -> is_enabled and updates all providers at once.
    This is the primary endpoint for the admin UI to save checkbox states.
    """
    return settings_service.bulk_update_providers(db, updates.providers)
