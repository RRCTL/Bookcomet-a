"""Local PC security hardening: auth gate, path segments, upload size."""
from __future__ import annotations

import os
import unittest
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api import identity as identity_api
from app.api import settings as settings_api
from app.api.deps import allow_legacy_header_auth, get_current_company_id, validate_tenant_id
from app.core.security import create_access_token
from app.database import Base, get_db
from app.models.identity import Company, Membership, User
from app.services.file_storage import LocalDiskStorage, assert_upload_size


class TenantIdValidationTest(unittest.TestCase):
    def test_rejects_path_traversal(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            validate_tenant_id("..\\Users\\x", field_name="company_id")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_accepts_uuid(self) -> None:
        cid = str(uuid.uuid4())
        self.assertEqual(validate_tenant_id(cid), cid)


class UploadSizeTest(unittest.TestCase):
    def test_assert_upload_size_rejects_oversize(self) -> None:
        with self.assertRaises(ValueError):
            assert_upload_size(b"x" * (2 * 1024 * 1024), max_upload_size_mb=1)


class StoragePathTest(unittest.TestCase):
    def test_save_rejects_traversal_company_id(self) -> None:
        root = Path(os.environ.get("TEMP") or ".") / f"bc-upload-test-{uuid.uuid4().hex}"
        try:
            store = LocalDiskStorage(str(root))
            with self.assertRaises(ValueError):
                store.save("../evil", "task1", "file1", b"hi", ".bin")
        finally:
            if root.exists():
                for p in sorted(root.rglob("*"), reverse=True):
                    if p.is_file():
                        p.unlink(missing_ok=True)
                    elif p.is_dir():
                        p.rmdir()
                root.rmdir()


class AuthGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_legacy = os.environ.get("ALLOW_LEGACY_HEADER_AUTH")
        os.environ.pop("ALLOW_LEGACY_HEADER_AUTH", None)

    def tearDown(self) -> None:
        if self._prev_legacy is None:
            os.environ.pop("ALLOW_LEGACY_HEADER_AUTH", None)
        else:
            os.environ["ALLOW_LEGACY_HEADER_AUTH"] = self._prev_legacy

    def test_legacy_flag_defaults_off(self) -> None:
        self.assertFalse(allow_legacy_header_auth())

    def _app_client(self) -> tuple[TestClient, str, str]:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine)
        db = SessionLocal()
        user_id = str(uuid.uuid4())
        company_id = str(uuid.uuid4())
        email = "sec@example.com"
        db.add(
            User(
                id=user_id,
                username="sec_user",
                email=email,
                display_name="Sec",
                is_active=True,
                is_verified=True,
            )
        )
        db.add(Company(id=company_id, name="Sec Co"))
        db.add(
            Membership(
                id=str(uuid.uuid4()),
                user_id=user_id,
                company_id=company_id,
                role="owner",
            )
        )
        db.commit()
        db.close()

        app = FastAPI()
        app.include_router(settings_api.router)
        app.include_router(identity_api.router)

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

        return TestClient(app), user_id, email

    def test_unauthenticated_settings_and_company_rejected(self) -> None:
        client, user_id, email = self._app_client()
        self.assertEqual(client.get("/settings/env").status_code, 401)
        self.assertEqual(client.get("/_company").status_code, 401)
        self.assertEqual(client.post("/identity/bootstrap-default").status_code, 403)

        token = create_access_token(user_id, email)
        headers = {"Authorization": f"Bearer {token}", "X-Company-ID": "default"}
        self.assertEqual(client.get("/_company", headers=headers).status_code, 403)
