from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from app.auth import CurrentUser


class HealthResponse(BaseModel):
    status: Literal["ok"]


class MeResponse(BaseModel):
    user_id: str


app = FastAPI(title="Elevate API", version="0.1.0")


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/me", response_model=MeResponse, tags=["users"])
def me(user: CurrentUser) -> MeResponse:
    return MeResponse(user_id=str(user.id))
