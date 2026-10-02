from functools import lru_cache
from os import environ

from pydantic import BaseModel, Field


class Settings(BaseModel):
    database_url: str = "postgresql+psycopg://postgres:postgres@127.0.0.1:54322/postgres"
    open_wearables_base_url: str = "http://127.0.0.1:8000"
    open_wearables_api_key: str = ""
    open_wearables_connect_timeout_seconds: float = Field(default=2.0, gt=0)
    open_wearables_read_timeout_seconds: float = Field(default=10.0, gt=0)

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            database_url=environ.get(
                "ELEVATE_DATABASE_URL",
                "postgresql+psycopg://postgres:postgres@127.0.0.1:54322/postgres",
            ),
            open_wearables_base_url=environ.get("OPEN_WEARABLES_BASE_URL", "http://127.0.0.1:8000"),
            open_wearables_api_key=environ.get("OPEN_WEARABLES_API_KEY", ""),
            open_wearables_connect_timeout_seconds=float(
                environ.get("OPEN_WEARABLES_CONNECT_TIMEOUT_SECONDS", "2")
            ),
            open_wearables_read_timeout_seconds=float(environ.get("OPEN_WEARABLES_READ_TIMEOUT_SECONDS", "10")),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_environment()
