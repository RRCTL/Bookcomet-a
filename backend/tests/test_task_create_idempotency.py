from __future__ import annotations

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - register mappers
from app.api.tasks import router as tasks_router
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

    user_id = str(uuid.uuid4())
    email = "tasks@example.com"
    company_a = str(uuid.uuid4())
    company_b = str(uuid.uuid4())

    db = SessionLocal()
    db.add(User(id=user_id, username="tasks_user", email=email, display_name="Tasks User", is_active=True, is_verified=True))
    db.add(Company(id=company_a, name="Company A"))
    db.add(Company(id=company_b, name="Company B"))
    db.add(Membership(id=str(uuid.uuid4()), user_id=user_id, company_id=company_a, role="owner"))
    db.add(Membership(id=str(uuid.uuid4()), user_id=user_id, company_id=company_b, role="owner"))
    db.commit()
    db.close()

    app = FastAPI()
    app.include_router(tasks_router, tags=["tasks"])

    def override_db() -> object:
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    return TestClient(app), user_id, email, company_a, company_b


def test_task_create_idempotency_is_company_and_delete_safe() -> None:
    client, user_id, email, company_a, company_b = _client_with_two_companies()
    token = create_access_token(user_id, email)
    task_id = f"rec-{uuid.uuid4()}"

    def headers(company_id: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "X-Company-ID": company_id,
            "X-User-ID": user_id,
        }

    body = {
        "id": task_id,
        "title": "SQL first task",
        "processing_mode": "AP",
        "status": "idle",
    }

    first = client.post("/api/tasks", json=body, headers=headers(company_a))
    assert first.status_code == 201

    same_company_retry = client.post("/api/tasks", json=body, headers=headers(company_a))
    assert same_company_retry.status_code == 201
    assert same_company_retry.json()["company_id"] == company_a

    cross_company_retry = client.post("/api/tasks", json=body, headers=headers(company_b))
    assert cross_company_retry.status_code == 409

    deleted = client.delete(f"/api/tasks/{task_id}", headers=headers(company_a))
    assert deleted.status_code == 204

    deleted_retry = client.post("/api/tasks", json=body, headers=headers(company_a))
    assert deleted_retry.status_code == 409
