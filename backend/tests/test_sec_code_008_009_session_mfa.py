"""SEC-CODE-008 session revoke + SEC-CODE-009 TOTP MFA."""
from __future__ import annotations

import uuid

import pyotp
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api import auth as auth_api
from app.core.security import create_access_token, decode_access_token
from app.database import Base, get_db
from app.models.identity import User


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(auth_api.router, prefix="/auth")
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        c.session_factory = SessionLocal  # type: ignore[attr-defined]
        yield c


def _register_and_login(client: TestClient, username: str = "alice") -> tuple[str, str]:
    password = "Secret123!"
    r = client.post(
        "/auth/register",
        json={
            "username": username,
            "display_name": "Alice",
            "password": password,
        },
    )
    assert r.status_code == 201, r.text
    login = client.post(
        "/auth/login",
        json={"identifier": username, "password": password},
    )
    assert login.status_code == 200, login.text
    body = login.json()
    assert body.get("mfa_required") is False
    assert body.get("access_token")
    return body["access_token"], password


def test_revoke_sessions_invalidates_access_token(client: TestClient):
    token, _ = _register_and_login(client, "revoker")
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/auth/me", headers=headers).status_code == 200

    revoked = client.post("/auth/revoke-sessions", headers=headers)
    assert revoked.status_code == 200, revoked.text

    me = client.get("/auth/me", headers=headers)
    assert me.status_code == 401
    assert "revoked" in me.json()["detail"].lower()


def test_mfa_login_challenge_and_verify(client: TestClient):
    token, password = _register_and_login(client, "mfa_user")
    headers = {"Authorization": f"Bearer {token}"}

    setup = client.post("/auth/mfa/setup", headers=headers)
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    code = pyotp.TOTP(secret).now()

    enabled = client.post("/auth/mfa/enable", headers=headers, json={"code": code})
    assert enabled.status_code == 200, enabled.text

    login = client.post(
        "/auth/login",
        json={"identifier": "mfa_user", "password": password},
    )
    assert login.status_code == 200, login.text
    body = login.json()
    assert body["mfa_required"] is True
    assert body.get("mfa_token")
    assert not body.get("access_token")

    code2 = pyotp.TOTP(secret).now()
    verified = client.post(
        "/auth/mfa/verify",
        json={"mfa_token": body["mfa_token"], "code": code2},
    )
    assert verified.status_code == 200, verified.text
    access = verified.json()["access_token"]
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {access}"}).status_code == 200


def test_access_token_embeds_session_version():
    token = create_access_token(str(uuid.uuid4()), "u@x.com", session_version=3)
    payload = decode_access_token(token)
    assert payload["sv"] == 3
