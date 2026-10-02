from uuid import UUID

from app.open_wearables import OpenWearablesClient
from app.repositories import WearableAccountRepository
from app.schemas import WearableAccountResponse, WearableConnectionStatus


class WearableAccountService:
    def __init__(self, repository: WearableAccountRepository, open_wearables: OpenWearablesClient) -> None:
        self.repository = repository
        self.open_wearables = open_wearables

    def get_account(self, supabase_user_id: UUID) -> WearableAccountResponse:
        mapping = self.repository.get_by_supabase_user_id(supabase_user_id)
        if mapping is None:
            return WearableAccountResponse(
                user_id=supabase_user_id,
                open_wearables_user_id=None,
                connection_status=WearableConnectionStatus.UNMAPPED,
            )

        open_wearables_user = self.open_wearables.get_user(str(mapping.open_wearables_user_id))
        status = (
            WearableConnectionStatus.CONNECTED
            if open_wearables_user.has_active_connection
            else WearableConnectionStatus.DISCONNECTED
        )
        return WearableAccountResponse(
            user_id=supabase_user_id,
            open_wearables_user_id=mapping.open_wearables_user_id,
            connection_status=status,
        )
