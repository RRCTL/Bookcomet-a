"""JWT encode/decode behaviour for access and MFA challenge tokens (PyJWT)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_mfa_challenge_token,
    decode_access_token,
    decode_mfa_challenge_token,
)


def test_access_token_roundtrip_claims():
    user_id = str(uuid.uuid4())
    token = create_access_token(user_id, "user@example.com", session_version=7)
    payload = decode_access_token(token)
    assert payload["sub"] == user_id
    assert payload["email"] == "user@example.com"
    assert payload["type"] == "access"
    assert payload["sv"] == 7
    assert "exp" in payload
    assert "iat" in payload


def test_access_token_uses_configured_hs_algorithm():
    token = create_access_token(str(uuid.uuid4()), "algo@example.com")
    header = jwt.get_unverified_header(token)
    assert header["alg"] == settings.jwt_algorithm
    assert settings.jwt_algorithm.startswith("HS")


def test_decode_access_token_rejects_missing_and_garbage():
    with pytest.raises(HTTPException) as missing:
        decode_access_token(None)
    assert missing.value.status_code == 401

    with pytest.raises(HTTPException) as garbage:
        decode_access_token("not-a-jwt")
    assert garbage.value.status_code == 401
    assert "invalid or expired" in garbage.value.detail.lower()


def test_decode_access_token_rejects_expired():
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expired = jwt.encode(
        {
            "sub": user_id,
            "email": "expired@example.com",
            "exp": now - timedelta(minutes=1),
            "iat": now - timedelta(minutes=10),
            "type": "access",
            "sv": 0,
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(HTTPException) as exc:
        decode_access_token(expired)
    assert exc.value.status_code == 401


def test_decode_access_token_rejects_wrong_type_and_tampered():
    mfa = create_mfa_challenge_token(str(uuid.uuid4()))
    with pytest.raises(HTTPException) as wrong_type:
        decode_access_token(mfa)
    assert wrong_type.value.status_code == 401
    assert "type" in wrong_type.value.detail.lower()

    good = create_access_token(str(uuid.uuid4()), "ok@example.com")
    tampered = good[:-4] + ("AAAA" if not good.endswith("AAAA") else "BBBB")
    with pytest.raises(HTTPException) as bad_sig:
        decode_access_token(tampered)
    assert bad_sig.value.status_code == 401


def test_mfa_challenge_token_roundtrip_and_shape():
    user_id = str(uuid.uuid4())
    token = create_mfa_challenge_token(user_id)
    payload = decode_mfa_challenge_token(token)
    assert payload["sub"] == user_id
    assert payload["type"] == "mfa_challenge"
    assert "email" not in payload
    assert "sv" not in payload
    assert "exp" in payload
    assert "iat" in payload


def test_decode_mfa_challenge_rejects_access_expired_and_missing():
    access = create_access_token(str(uuid.uuid4()), "x@example.com")
    with pytest.raises(HTTPException) as wrong_type:
        decode_mfa_challenge_token(access)
    assert wrong_type.value.status_code == 401
    assert "mfa" in wrong_type.value.detail.lower()

    with pytest.raises(HTTPException) as missing:
        decode_mfa_challenge_token(None)
    assert missing.value.status_code == 401

    now = datetime.now(timezone.utc)
    expired = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "exp": now - timedelta(minutes=1),
            "iat": now - timedelta(minutes=6),
            "type": "mfa_challenge",
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(HTTPException) as exc:
        decode_mfa_challenge_token(expired)
    assert exc.value.status_code == 401
    assert "mfa challenge" in exc.value.detail.lower()
