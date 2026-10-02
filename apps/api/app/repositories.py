from uuid import UUID

from sqlalchemy.orm import Session

from app.models import WearableAccountMapping


class WearableAccountRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_supabase_user_id(self, supabase_user_id: UUID) -> WearableAccountMapping | None:
        return self.session.get(WearableAccountMapping, supabase_user_id)

    def upsert(self, supabase_user_id: UUID, open_wearables_user_id: UUID) -> WearableAccountMapping:
        mapping = self.get_by_supabase_user_id(supabase_user_id)
        if mapping is None:
            mapping = WearableAccountMapping(
                supabase_user_id=supabase_user_id,
                open_wearables_user_id=open_wearables_user_id,
            )
            self.session.add(mapping)
        else:
            mapping.open_wearables_user_id = open_wearables_user_id
        self.session.commit()
        self.session.refresh(mapping)
        return mapping
