from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel


class WearableConnectionStatus(StrEnum):
    UNMAPPED = "unmapped"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


class WearableAccountResponse(BaseModel):
    user_id: UUID
    open_wearables_user_id: UUID | None
    connection_status: WearableConnectionStatus


class OpenWearablesUser(BaseModel):
    id: UUID
    has_active_connection: bool = False
