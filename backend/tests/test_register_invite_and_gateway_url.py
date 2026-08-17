"""Register invite gate + gateway URL validation."""
from __future__ import annotations

import os
import unittest
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api import auth as auth_api
from app.api import health as health_api
from app.core.config import settings
from app.core.gateway_settings import validate_gateway_url
from app.database import Base, get_db


class RegisterInviteTest(unittest.TestCase):
    def setUp(self) -> None:
        self._prev = settings.register_invite_code
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.SessionLocal = sessionmaker(bind=engine)
        app = FastAPI()
        app.include_router(auth_api.router, prefix="/auth")
        app.include_router(health_api.router)

        def override_db():
            s = self.SessionLocal()
            try:
                yield s
            finally:
                s.close()

        app.dependency_overrides[get_db] = override_db
        self.client = TestClient(app)

    def tearDown(self) -> None:
        settings.register_invite_code = self._prev

    def test_open_register_when_invite_unset(self) -> None:
        settings.register_invite_code = ""
        h = self.client.get("/health")
        self.assertEqual(h.status_code, 200)
        self.assertFalse(h.json().get("register_invite_required"))
        uname = f"u{uuid.uuid4().hex[:8]}"
        r = self.client.post(
            "/auth/register",
            json={
                "username": uname,
                "display_name": "User",
                "password": "TestOnly1",
            },
        )
        self.assertEqual(r.status_code, 201, r.text)

    def test_invite_required_when_set(self) -> None:
        settings.register_invite_code = "tunnel-secret-99"
        h = self.client.get("/health")
        self.assertTrue(h.json().get("register_invite_required"))
        uname = f"u{uuid.uuid4().hex[:8]}"
        bad = self.client.post(
            "/auth/register",
            json={
                "username": uname,
                "display_name": "User",
                "password": "TestOnly1",
                "invite_code": "wrong",
            },
        )
        self.assertEqual(bad.status_code, 403)
        good = self.client.post(
            "/auth/register",
            json={
                "username": uname,
                "display_name": "User",
                "password": "TestOnly1",
                "invite_code": "tunnel-secret-99",
            },
        )
        self.assertEqual(good.status_code, 201, good.text)


class GatewayUrlValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._env = settings.app_env
        self._prev_flag = os.environ.get("GATEWAY_BLOCK_PRIVATE_URLS")

    def tearDown(self) -> None:
        settings.app_env = self._env
        if self._prev_flag is None:
            os.environ.pop("GATEWAY_BLOCK_PRIVATE_URLS", None)
        else:
            os.environ["GATEWAY_BLOCK_PRIVATE_URLS"] = self._prev_flag

    def test_local_allows_loopback(self) -> None:
        settings.app_env = "local"
        os.environ.pop("GATEWAY_BLOCK_PRIVATE_URLS", None)
        self.assertEqual(
            validate_gateway_url("http://127.0.0.1:8080/v1"),
            "http://127.0.0.1:8080/v1",
        )

    def test_non_local_blocks_loopback(self) -> None:
        settings.app_env = "production"
        os.environ.pop("GATEWAY_BLOCK_PRIVATE_URLS", None)
        with self.assertRaises(ValueError):
            validate_gateway_url("http://127.0.0.1:8080/v1")

    def test_rejects_non_http(self) -> None:
        settings.app_env = "local"
        with self.assertRaises(ValueError):
            validate_gateway_url("file:///etc/passwd")
