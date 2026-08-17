"""
Tests for company hard-delete (purge) and DELETE /companies/{id} guards.
"""
from __future__ import annotations

import uuid
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — register mappers
from app.api.companies import router as companies_router
from app.api import deps
from app.core.security import create_access_token
from app.database import Base, get_db
from app.models.identity import Company, Membership, User
from app.services.company_purge import delete_company_purge


class CompanyPurgeSqliteTest(unittest.TestCase):
    def test_delete_company_purge_removes_row(self) -> None:
        e = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(e)
        S = sessionmaker(bind=e)
        db = S()
        uid = str(uuid.uuid4())
        c1, c2 = str(uuid.uuid4()), str(uuid.uuid4())
        db.add(User(id=uid, username="t_user", email="t@t.com", display_name="T", is_active=True, is_verified=True))
        db.add(Company(id=c1, name="W1"))
        db.add(Company(id=c2, name="W2"))
        db.add(Membership(id=str(uuid.uuid4()), user_id=uid, company_id=c1, role="owner"))
        db.add(Membership(id=str(uuid.uuid4()), user_id=uid, company_id=c2, role="owner"))
        db.commit()

        delete_company_purge(db, c1)
        db.commit()
        self.assertIsNone(db.query(Company).filter(Company.id == c1).first())
        self.assertIsNotNone(db.query(Company).filter(Company.id == c2).first())


def _client_with_db() -> tuple[TestClient, str, str, str, str, sessionmaker]:
    e = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(e)
    S = sessionmaker(bind=e)
    db = S()
    uid = str(uuid.uuid4())
    c1, c2 = str(uuid.uuid4()), str(uuid.uuid4())
    u = User(id=uid, username="o_user", email="o@o.com", display_name="O", is_active=True, is_verified=True)
    db.add(u)
    db.add(Company(id=c1, name="Alpha Co"))
    db.add(Company(id=c2, name="Beta Co"))
    db.add(Membership(id=str(uuid.uuid4()), user_id=uid, company_id=c1, role="owner"))
    db.add(Membership(id=str(uuid.uuid4()), user_id=uid, company_id=c2, role="owner"))
    db.commit()
    uid, uemail = u.id, u.email
    db.close()

    tapp = FastAPI()
    tapp.include_router(companies_router, tags=["companies"])

    def override_db() -> object:
        s = S()
        try:
            yield s
        finally:
            s.close()

    async def override_user() -> User:
        s2 = S()
        try:
            return s2.query(User).filter(User.id == uid).one()
        finally:
            s2.close()

    tapp.dependency_overrides[get_db] = override_db
    tapp.dependency_overrides[deps.get_current_user] = override_user
    return TestClient(tapp), uid, uemail, c1, c2, S


class CompaniesDeleteAPITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._client, cls._uid, cls._email, cls._c1, cls._c2, cls._S = _client_with_db()
        tok = create_access_token(cls._uid, cls._email)
        cls._headers = {"Authorization": f"Bearer {tok}", "X-Company-ID": cls._c1}

    def test_last_workspace_rejected(self) -> None:
        e = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(e)
        S = sessionmaker(bind=e)
        db = S()
        uid = str(uuid.uuid4())
        cx = str(uuid.uuid4())
        u = User(id=uid, username="solo_user", email="solo@x.com", display_name="S", is_active=True, is_verified=True)
        db.add(u)
        db.add(Company(id=cx, name="Only"))
        db.add(Membership(id=str(uuid.uuid4()), user_id=uid, company_id=cx, role="owner"))
        db.commit()
        db.close()

        tapp = FastAPI()
        tapp.include_router(companies_router, tags=["companies"])

        def override_db() -> object:
            s = S()
            try:
                yield s
            finally:
                s.close()

        async def override_user() -> User:
            s2 = S()
            try:
                return s2.query(User).filter(User.id == uid).one()
            finally:
                s2.close()

        tapp.dependency_overrides[get_db] = override_db
        tapp.dependency_overrides[deps.get_current_user] = override_user
        client = TestClient(tapp)
        tok = create_access_token(uid, "solo@x.com")
        r = client.request(
            "DELETE",
            f"/companies/{cx}",
            json={"confirm_name": "Only"},
            headers={"Authorization": f"Bearer {tok}", "X-Company-ID": cx},
        )
        self.assertEqual(r.status_code, 400)

    def test_name_mismatch_400(self) -> None:
        r = self._client.request(
            "DELETE",
            f"/companies/{self._c1}",
            json={"confirm_name": "Wrong"},
            headers=self._headers,
        )
        self.assertEqual(r.status_code, 400)

    def test_non_owner_403(self) -> None:
        e = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(e)
        S = sessionmaker(bind=e)
        db = S()
        uid_o = str(uuid.uuid4())
        uid_m = str(uuid.uuid4())
        c_id = str(uuid.uuid4())
        db.add(
            User(
                id=uid_o,
                username="own_user",
                email="own@x.com",
                display_name="Own",
                is_active=True,
                is_verified=True,
            )
        )
        db.add(
            User(
                id=uid_m,
                username="mem_user",
                email="mem@x.com",
                display_name="Mem",
                is_active=True,
                is_verified=True,
            )
        )
        db.add(Company(id=c_id, name="SharedCo"))
        c_other = str(uuid.uuid4())
        db.add(Company(id=c_other, name="OtherCo"))
        db.add(Membership(id=str(uuid.uuid4()), user_id=uid_o, company_id=c_id, role="owner"))
        db.add(
            Membership(
                id=str(uuid.uuid4()), user_id=uid_m, company_id=c_id, role="accountant"
            )
        )
        db.add(
            Membership(
                id=str(uuid.uuid4()), user_id=uid_m, company_id=c_other, role="owner"
            )
        )
        db.commit()
        db.close()

        tapp = FastAPI()
        tapp.include_router(companies_router, tags=["companies"])

        def override_db() -> object:
            s = S()
            try:
                yield s
            finally:
                s.close()

        mem_user_holder: dict[str, str] = {"id": uid_m}

        async def override_user() -> User:
            s2 = S()
            try:
                return s2.query(User).filter(User.id == mem_user_holder["id"]).one()
            finally:
                s2.close()

        tapp.dependency_overrides[get_db] = override_db
        tapp.dependency_overrides[deps.get_current_user] = override_user
        client = TestClient(tapp)
        tok = create_access_token(uid_m, "mem@x.com")
        r = client.request(
            "DELETE",
            f"/companies/{c_id}",
            json={"confirm_name": "SharedCo"},
            headers={"Authorization": f"Bearer {tok}", "X-Company-ID": c_id},
        )
        self.assertEqual(r.status_code, 403)

    def test_success_returns_suggested_id(self) -> None:
        r = self._client.request(
            "DELETE",
            f"/companies/{self._c1}",
            json={"confirm_name": "Alpha Co"},
            headers=self._headers,
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data.get("deleted_id"), self._c1)
        self.assertEqual(data.get("suggested_company_id"), self._c2)


if __name__ == "__main__":
    unittest.main()
