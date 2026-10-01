from functools import lru_cache
from os import environ
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import ExpiredSignatureError, PyJWKClient, PyJWTError
from pydantic import BaseModel


class AuthenticatedUser(BaseModel):
    id: UUID


bearer_scheme = HTTPBearer(auto_error=False)


@lru_cache
def get_jwks_client() -> PyJWKClient:
    jwks_url = environ.get(
        "SUPABASE_JWKS_URL",
        "http://127.0.0.1:54321/auth/v1/.well-known/jwks.json",
    )
    return PyJWKClient(jwks_url)


def authentication_error(detail: str = "Invalid authentication credentials") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> AuthenticatedUser:
    if credentials is None:
        raise authentication_error("Missing bearer token")

    token = credentials.credentials
    try:
        signing_key = get_jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],
            audience=environ.get("SUPABASE_JWT_AUDIENCE", "authenticated"),
            issuer=environ.get("SUPABASE_JWT_ISSUER", "http://127.0.0.1:54321/auth/v1"),
        )
        return AuthenticatedUser(id=UUID(str(claims["sub"])))
    except ExpiredSignatureError as exc:
        raise authentication_error("Token has expired") from exc
    except (KeyError, PyJWTError, ValueError) as exc:
        raise authentication_error() from exc


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
