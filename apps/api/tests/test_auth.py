from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app import auth
from app.main import app

ISSUER = "http://127.0.0.1:54321/auth/v1"
AUDIENCE = "authenticated"


class StubJwksClient:
    def __init__(self, public_key: Any) -> None:
        self.public_key = public_key

    def get_signing_key_from_jwt(self, _token: str) -> Any:
        return type("SigningKey", (), {"key": self.public_key})()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def private_key(monkeypatch: pytest.MonkeyPatch) -> Any:
    key = ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setattr(auth, "get_jwks_client", lambda: StubJwksClient(key.public_key()))
    return key


def make_token(private_key: Any, *, subject: UUID, expires_at: datetime) -> str:
    return jwt.encode(
        {
            "sub": str(subject),
            "aud": AUDIENCE,
            "iss": ISSUER,
            "iat": datetime.now(UTC),
            "exp": expires_at,
        },
        private_key,
        algorithm="ES256",
        headers={"kid": "test-key"},
    )


def test_me_returns_authenticated_user_id(client: TestClient, private_key: Any) -> None:
    user_id = uuid4()
    token = make_token(private_key, subject=user_id, expires_at=datetime.now(UTC) + timedelta(minutes=5))

    response = client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {"user_id": str(user_id)}


def test_me_rejects_missing_token(client: TestClient) -> None:
    response = client.get("/me")

    assert response.status_code == 401
    assert response.json() == {"detail": "Missing bearer token"}


def test_me_rejects_expired_token(client: TestClient, private_key: Any) -> None:
    token = make_token(private_key, subject=uuid4(), expires_at=datetime.now(UTC) - timedelta(seconds=1))

    response = client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Token has expired"}


def test_me_rejects_malformed_token(client: TestClient, private_key: Any) -> None:
    response = client.get("/me", headers={"Authorization": "Bearer not-a-jwt"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid authentication credentials"}
