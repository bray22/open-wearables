from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class WearableAccountMapping(Base):
    __tablename__ = "wearable_account_mappings"

    supabase_user_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    open_wearables_user_id: Mapped[UUID] = mapped_column(Uuid, unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
