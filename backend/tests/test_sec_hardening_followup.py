"""SEC-CODE-010..013: session revoke on tenant deps, membership scope, env API, WS auth."""
from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api import identity as identity_api
from app.api import settings as settings_api
from app.api import workflows as workflows_api
from app.api.deps import get_current_company_id
from app.core.security import create_access_token
from app.database import Base, get_db
from app.models.identity import Company, Membership, User


def _build_client() -> tuple[TestClient, str, str, str, str]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    user_id = str(uuid.uuid4())
    other_user_id = str(uuid.uuid4())
    company_a = str(uuid.uuid4())
    company_b = str(uuid.uuid4())
    email = "owner@example.com"
    db.add(
        User(
            id=user_id,
            username="owner_a",
            email=email,
            display_name="Owner A",
            is_active=True,
            is_verified=True,
            session_version=0,
        )
    )
    db.add(
        User(
            id=other_user_id,
            username="member_b",
            email="member@example.com",
            display_name="Member B",
            is_active=True,
            is_verified=True,
            session_version=0,
        )
    )
    db.add(Company(id=company_a, name="Company A"))
    db.add(Company(id=company_b, name="Company B"))
    db.add(
        Membership(
            id=str(uuid.uuid4()),
            user_id=user_id,
            company_id=company_a,
            role="owner",
        )
    )
    db.add(
        Membership(
            id=str(uuid.uuid4()),
            user_id=other_user_id,
            company_id=company_b,
            role="owner",
        )
    )
    db.commit()
    db.close()

    app = FastAPI()
    app.include_router(settings_api.router)
    app.include_router(identity_api.router)
    app.include_router(workflows_api.router)

    def override_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db

    @app.get("/_company")
    def company_probe(cid: str = Depends(get_current_company_id)):
        return {"company_id": cid}

    return TestClient(app), user_id, email, company_a, company_b


class SessionRevokeOnTenantDepTest(unittest.TestCase):
    def test_revoked_token_rejected_by_company_scope(self) -> None:
        client, user_id, email, company_a, _company_b = _build_client()
        token = create_access_token(user_id, email, session_version=0)
        headers = {"Authorization": f"Bearer {token}", "X-Company-ID": company_a}
        self.assertEqual(client.get("/_company", headers=headers).status_code, 200)

        for dep, override in client.app.dependency_overrides.items():
            if dep is get_db:
                gen = override()
                db = next(gen)
                user = db.query(User).filter(User.id == user_id).first()
                user.session_version = 1
                db.commit()
                try:
                    next(gen)
                except StopIteration:
                    pass
                break

        me = client.get("/_company", headers=headers)
        self.assertEqual(me.status_code, 401)
        self.assertIn("revoked", me.json()["detail"].lower())


class IdentityMembershipScopeTest(unittest.TestCase):
    def test_cannot_add_membership_to_other_company(self) -> None:
        client, user_id, email, company_a, company_b = _build_client()
        token = create_access_token(user_id, email, session_version=0)
        headers = {"Authorization": f"Bearer {token}", "X-Company-ID": company_a}
        new_id = str(uuid.uuid4())
        for dep, override in client.app.dependency_overrides.items():
            if dep is get_db:
                gen = override()
                db = next(gen)
                db.add(
                    User(
                        id=new_id,
                        username="new_user",
                        email="new.user@example.com",
                        display_name="New User",
                        is_active=True,
                    )
                )
                db.commit()
                try:
                    next(gen)
                except StopIteration:
                    pass
                break

        denied = client.post(
            "/identity/memberships",
            headers=headers,
            json={"user_id": new_id, "company_id": company_b, "role": "owner"},
        )
        self.assertEqual(denied.status_code, 403, denied.text)

        allowed = client.post(
            "/identity/memberships",
            headers=headers,
            json={"user_id": new_id, "company_id": company_a, "role": "accountant"},
        )
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertEqual(allowed.json()["company_id"], company_a)
        self.assertEqual(allowed.json()["role"], "accountant")

    def test_rejects_unknown_role(self) -> None:
        client, user_id, email, company_a, _company_b = _build_client()
        token = create_access_token(user_id, email, session_version=0)
        headers = {"Authorization": f"Bearer {token}", "X-Company-ID": company_a}
        new_id = str(uuid.uuid4())
        for dep, override in client.app.dependency_overrides.items():
            if dep is get_db:
                gen = override()
                db = next(gen)
                db.add(
                    User(
                        id=new_id,
                        username="role_user",
                        email="role.user@example.com",
                        display_name="Role User",
                        is_active=True,
                    )
                )
                db.commit()
                try:
                    next(gen)
                except StopIteration:
                    pass
                break
        bad = client.post(
            "/identity/memberships",
            headers=headers,
            json={"user_id": new_id, "role": "superadmin"},
        )
        self.assertEqual(bad.status_code, 400)


class SettingsEnvHardeningTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.env_path = str(Path(self._tmp.name) / ".env")
        Path(self.env_path).write_text(
            "\n".join(
                [
                    "JWT_SECRET_KEY=ci-test-jwt-secret-key-at-least-32-chars",
                    "DATABASE_URL=postgresql://user:pass@db.internal/app",
                    "REGISTER_INVITE_CODE=invite-secret-value",
                    "REDIS_URL=redis://:secret@localhost:6379/0",
                    "LOG_LEVEL=INFO",
                    "AI_ENHANCE_USE_REASONER=false",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        self._orig_env_path = settings_api._env_path
        settings_api._env_path = lambda: self.env_path  # type: ignore[method-assign]

    def tearDown(self) -> None:
        settings_api._env_path = self._orig_env_path  # type: ignore[method-assign]
        self._tmp.cleanup()

    def test_masks_urls_and_invite_and_hides_abs_path(self) -> None:
        client, user_id, email, company_a, _company_b = _build_client()
        token = create_access_token(user_id, email, session_version=0)
        headers = {"Authorization": f"Bearer {token}", "X-Company-ID": company_a}
        resp = client.get("/settings/env", headers=headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["env_path"], ".env")
        by_key = {row["key"]: row for row in body["settings"]}
        self.assertEqual(by_key["DATABASE_URL"]["value"], "***")
        self.assertEqual(by_key["REGISTER_INVITE_CODE"]["value"], "***")
        self.assertEqual(by_key["REDIS_URL"]["value"], "***")
        self.assertEqual(by_key["JWT_SECRET_KEY"]["value"], "***")
        self.assertEqual(by_key["LOG_LEVEL"]["value"], "INFO")
        self.assertNotIn("invite-secret-value", resp.text)
        self.assertNotIn("postgresql://", resp.text)

    def test_blocks_protected_key_writes(self) -> None:
        client, user_id, email, company_a, _company_b = _build_client()
        token = create_access_token(user_id, email, session_version=0)
        headers = {"Authorization": f"Bearer {token}", "X-Company-ID": company_a}
        resp = client.post(
            "/settings/env",
            headers=headers,
            json={"settings": [{"key": "JWT_SECRET_KEY", "value": "hacked-secret-should-not-apply"}]},
        )
        self.assertEqual(resp.status_code, 403, resp.text)
        on_disk = Path(self.env_path).read_text(encoding="utf-8")
        self.assertIn("ci-test-jwt-secret-key-at-least-32-chars", on_disk)
        self.assertNotIn("hacked-secret-should-not-apply", on_disk)

        ok = client.post(
            "/settings/env",
            headers=headers,
            json={"settings": [{"key": "AI_ENHANCE_USE_REASONER", "value": "true"}]},
        )
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertIn("AI_ENHANCE_USE_REASONER=true", Path(self.env_path).read_text(encoding="utf-8"))


class WorkflowWsAuthTest(unittest.TestCase):
    def test_query_string_token_is_ignored(self) -> None:
        client, user_id, email, _company_a, _company_b = _build_client()
        token = create_access_token(user_id, email, session_version=0)
        run_id = str(uuid.uuid4())
        with client.websocket_connect(f"/api/workflows/runs/{run_id}/ws?token={token}") as ws:
            # No first-message auth and no Authorization header → server closes 4401.
            try:
                ws.receive_text()
                closed = False
            except Exception:
                closed = True
        self.assertTrue(closed)

    def test_first_message_auth_rejects_revoked_token(self) -> None:
        client, user_id, email, _company_a, _company_b = _build_client()
        token = create_access_token(user_id, email, session_version=0)
        app = client.app
        for dep, override in app.dependency_overrides.items():
            if dep is get_db:
                gen = override()
                db = next(gen)
                user = db.query(User).filter(User.id == user_id).first()
                user.session_version = 9
                db.commit()
                try:
                    next(gen)
                except StopIteration:
                    pass
                break
        run_id = str(uuid.uuid4())
        with client.websocket_connect(f"/api/workflows/runs/{run_id}/ws") as ws:
            ws.send_text(f'{{"type":"auth","token":"{token}"}}')
            try:
                ws.receive_text()
                closed = False
            except Exception:
                closed = True
        self.assertTrue(closed)
