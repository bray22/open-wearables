from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import CurrentUser
from app.database import DatabaseSession
from app.open_wearables import (
    OpenWearablesClient,
    OpenWearablesResponseError,
    OpenWearablesUnavailableError,
    OpenWearablesUserNotFoundError,
    get_open_wearables_client,
)
from app.repositories import WearableAccountRepository
from app.schemas import WearableAccountResponse
from app.services import WearableAccountService

router = APIRouter(prefix="/me", tags=["users"])


def get_wearable_account_service(
    session: DatabaseSession,
    open_wearables: Annotated[OpenWearablesClient, Depends(get_open_wearables_client)],
) -> WearableAccountService:
    return WearableAccountService(WearableAccountRepository(session), open_wearables)


WearableAccountServiceDependency = Annotated[WearableAccountService, Depends(get_wearable_account_service)]


@router.get("/wearable-account", response_model=WearableAccountResponse)
def wearable_account(user: CurrentUser, service: WearableAccountServiceDependency) -> WearableAccountResponse:
    try:
        return service.get_account(user.id)
    except OpenWearablesUserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The mapped Open Wearables user does not exist",
        ) from exc
    except OpenWearablesUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Open Wearables is unavailable",
        ) from exc
    except OpenWearablesResponseError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Open Wearables rejected or returned an invalid response",
        ) from exc
