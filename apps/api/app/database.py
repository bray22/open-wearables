from collections.abc import Generator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


def get_database_session() -> Generator[Session]:
    with Session(get_engine()) as session:
        yield session


DatabaseSession = Annotated[Session, Depends(get_database_session)]
