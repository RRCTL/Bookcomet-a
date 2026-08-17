from __future__ import annotations

import uuid
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - register mappers
from app.api.rule_memory import router as rule_memory_router
from app.core.security import create_access_token
from app.database import Base, get_db
from app.models.identity import Company, Membership, User


def _client_with_two_companies() -> tuple[TestClient, str, str, str, str]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    db = SessionLocal()
    user_id = str(uuid.uuid4())
    email = "rules@example.com"
    company_a = str(uuid.uuid4())
    company_b = str(uuid.uuid4())
    db.add(User(id=user_id, username="rules_user", email=email, display_name="Rules User", is_active=True, is_verified=True))
    db.add(Company(id=company_a, name="Company A"))
    db.add(Company(id=company_b, name="Company B"))
    db.add(Membership(id=str(uuid.uuid4()), user_id=user_id, company_id=company_a, role="owner"))
    db.add(Membership(id=str(uuid.uuid4()), user_id=user_id, company_id=company_b, role="owner"))
    db.commit()
    db.close()

    app = FastAPI()
    app.include_router(rule_memory_router, tags=["rule-memory"])

    def override_db() -> object:
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    return TestClient(app), user_id, email, company_a, company_b


class RuleMemoryCompanyIsolationTest(unittest.TestCase):
    def test_same_mode_memory_is_isolated_by_company_scope(self) -> None:
        client, user_id, email, company_a, company_b = _client_with_two_companies()
        token = create_access_token(user_id, email)

        def headers(company_id: str) -> dict[str, str]:
            return {
                "Authorization": f"Bearer {token}",
                "X-Company-ID": company_id,
                "X-User-ID": user_id,
            }

        a_initial = client.get("/company/memory/AR", headers=headers(company_a))
        b_initial = client.get("/company/memory/AR", headers=headers(company_b))
        self.assertEqual(a_initial.status_code, 200)
        self.assertEqual(b_initial.status_code, 200)

        a_body = "# AR Rules Memory - Company A\n\n## AI Behaviour Instructions\n- A only\n"
        b_body = "# AR Rules Memory - Company B\n\n## AI Behaviour Instructions\n- B only\n"

        a_save = client.put(
            "/company/memory/AR",
            headers=headers(company_a),
            json={"content": a_body, "version": a_initial.json()["version"]},
        )
        b_save = client.put(
            "/company/memory/AR",
            headers=headers(company_b),
            json={"content": b_body, "version": b_initial.json()["version"]},
        )
        self.assertEqual(a_save.status_code, 200)
        self.assertEqual(b_save.status_code, 200)

        a_read = client.get("/company/memory/AR", headers=headers(company_a))
        b_read = client.get("/company/memory/AR", headers=headers(company_b))

        self.assertEqual(a_read.json()["content"], a_body)
        self.assertEqual(b_read.json()["content"], b_body)
        self.assertEqual(a_read.json()["company_id"], company_a)
        self.assertEqual(b_read.json()["company_id"], company_b)


if __name__ == "__main__":
    unittest.main()
