"""Company-manual wizard merge, exists, and per-workspace completion."""
from __future__ import annotations

import re
import uuid
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — register mappers
from app.api.company_manual import (
    BankAccountInput,
    BankSettingsInput,
    WizardAnswers,
    _build_manual_from_wizard,
    _coalesce_text,
    _is_wizard_completed,
    _knowledge_context_body,
    _mark_wizard_completed,
    _merge_manual_content,
    _wizard_content_language_instruction,
    router as company_manual_router,
)
from app.core.security import create_access_token
from app.database import Base, get_db
from app.models.company_context import CompanyProfile, CompanyRule
from app.models.company_manual import CompanyManual
from app.models.identity import Company, Membership, User
from app.services.rule_governance import RULE_TYPE_COMPANY_CONTEXT


def test_merge_manual_content_keeps_existing_and_appends_wizard() -> None:
    existing = "# Existing notes\n\nKeep this vendor list."
    generated = "# Company Manual — Acme\n\n## Key Clients\n\nNew client"
    merged = _merge_manual_content(existing, generated)
    assert "Keep this vendor list." in merged
    assert "New client" in merged
    assert "Setup wizard update" in merged


def test_merge_manual_content_skips_duplicate_and_empty() -> None:
    existing = "Full knowledge already includes the draft."
    assert _merge_manual_content(existing, "the draft") == existing
    assert _merge_manual_content("", "only new") == "only new"
    assert _merge_manual_content("only old", "") == "only old"
    assert _merge_manual_content("short", "short plus more") == "short plus more"


def test_coalesce_text_prefers_richer_copy() -> None:
    assert _coalesce_text("", "b") == "b"
    assert _coalesce_text("abc", "ab") == "abc"
    assert "one" in _coalesce_text("one", "two")
    assert "two" in _coalesce_text("one", "two")


_CJK = re.compile(r"[\u3400-\u9fff]")


def test_wizard_draft_chrome_is_english_and_keeps_user_chinese() -> None:
    english = WizardAnswers(
        company_name="Acme",
        bank_settings=BankSettingsInput(
            payment_method="bank",
            director_account=BankAccountInput(account_nickname="Director's Current Account"),
        ),
    )
    english_draft = _build_manual_from_wizard(english, "Acme")
    assert "suspense account" in english_draft
    assert _CJK.search(english_draft) is None
    assert "Write added guidance in English" in _wizard_content_language_instruction(english)

    chinese = WizardAnswers(
        company_name="範例貿易",
        key_clients="主要客戶：甲公司，月結 30 天。",
        key_vendors="供應商：乙公司。",
    )
    chinese_draft = _build_manual_from_wizard(chinese, "範例貿易")
    assert "主要客戶：甲公司，月結 30 天。" in chinese_draft
    assert "供應商：乙公司。" in chinese_draft
    instruction = _wizard_content_language_instruction(chinese)
    assert "Keep their Chinese text exactly" in instruction
    assert "Do not translate their answers into English" in instruction
    assert "English only" not in instruction

    existing_zh = _wizard_content_language_instruction(
        WizardAnswers(company_name="Acme"),
        existing_md="已有公司知識：保留中文內容。",
    )
    assert "Keep their Chinese text exactly" in existing_zh


def test_wizard_completed_flag_preserves_other_settings() -> None:
    profile = CompanyProfile(
        id=str(uuid.uuid4()),
        company_id=str(uuid.uuid4()),
        custom_settings={"currency": "HKD"},
    )
    assert _is_wizard_completed(profile) is False
    _mark_wizard_completed(profile)
    assert profile.custom_settings["currency"] == "HKD"
    assert _is_wizard_completed(profile) is True


def _client_with_two_companies() -> tuple[TestClient, str, str, str, str, sessionmaker]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    db = SessionLocal()
    user_id = str(uuid.uuid4())
    email = "wizard@example.com"
    company_a = str(uuid.uuid4())
    company_b = str(uuid.uuid4())
    db.add(User(id=user_id, username="wizard_user", email=email, display_name="Wizard", is_active=True, is_verified=True))
    db.add(Company(id=company_a, name="Company A"))
    db.add(Company(id=company_b, name="Company B"))
    db.add(Membership(id=str(uuid.uuid4()), user_id=user_id, company_id=company_a, role="owner"))
    db.add(Membership(id=str(uuid.uuid4()), user_id=user_id, company_id=company_b, role="owner"))
    db.commit()
    db.close()

    app = FastAPI()
    app.include_router(company_manual_router, tags=["company-manual"])

    def override_db() -> object:
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    return TestClient(app), user_id, email, company_a, company_b, SessionLocal


class CompanyManualWizardApiTest(unittest.TestCase):
    def test_exists_and_generate_are_scoped_per_workspace(self) -> None:
        client, user_id, email, company_a, company_b, SessionLocal = _client_with_two_companies()
        token = create_access_token(user_id, email)

        def headers(company_id: str) -> dict[str, str]:
            return {
                "Authorization": f"Bearer {token}",
                "X-Company-ID": company_id,
                "X-User-ID": user_id,
            }

        empty_a = client.get("/company/manual/exists", headers=headers(company_a))
        self.assertEqual(empty_a.status_code, 200)
        self.assertEqual(empty_a.json(), {"exists": False, "wizard_completed": False})

        db = SessionLocal()
        db.add(
            CompanyManual(
                id=str(uuid.uuid4()),
                company_id=company_a,
                content="# Existing knowledge\n\nKeep the old vendor notes.",
                version=1,
                updated_by_type="user",
            )
        )
        db.add(
            CompanyProfile(
                id=str(uuid.uuid4()),
                company_id=company_a,
                company_name="Company A",
                custom_settings={"currency": "HKD"},
            )
        )
        db.commit()
        db.close()

        seeded = client.get("/company/manual/exists", headers=headers(company_a))
        self.assertEqual(seeded.json(), {"exists": True, "wizard_completed": False})
        empty_b = client.get("/company/manual/exists", headers=headers(company_b))
        self.assertEqual(empty_b.json(), {"exists": False, "wizard_completed": False})

        generated = client.post(
            "/company/manual/generate",
            headers=headers(company_a),
            json={
                "company_name": "Company A",
                "key_clients": "New client from wizard",
                "generate_rule_memory": False,
                "generate_coa": False,
            },
        )
        self.assertEqual(generated.status_code, 200, generated.text)
        body = generated.json()["content"]
        self.assertIn("Keep the old vendor notes.", body)
        self.assertIn("New client from wizard", body)

        done_a = client.get("/company/manual/exists", headers=headers(company_a))
        self.assertEqual(done_a.json(), {"exists": True, "wizard_completed": True})
        still_empty_b = client.get("/company/manual/exists", headers=headers(company_b))
        self.assertEqual(still_empty_b.json(), {"exists": False, "wizard_completed": False})

        db = SessionLocal()
        profile = db.query(CompanyProfile).filter(CompanyProfile.company_id == company_a).first()
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.custom_settings.get("currency"), "HKD")
        self.assertTrue(profile.custom_settings.get("wizard_completed"))
        knowledge = _knowledge_context_body(db, company_a)
        self.assertIn("Keep the old vendor notes.", knowledge)
        self.assertIn("New client from wizard", knowledge)
        db.close()

    def test_exists_true_when_only_knowledge_context_exists(self) -> None:
        client, user_id, email, company_a, _company_b, SessionLocal = _client_with_two_companies()
        token = create_access_token(user_id, email)
        db = SessionLocal()
        db.add(
            CompanyRule(
                id=str(uuid.uuid4()),
                company_id=company_a,
                rule_name="Business context",
                rule_type=RULE_TYPE_COMPANY_CONTEXT,
                rule_json={"use_when": None, "body": "Typed knowledge only."},
                priority=0,
                is_active=True,
            )
        )
        db.commit()
        db.close()

        status = client.get(
            "/company/manual/exists",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Company-ID": company_a,
                "X-User-ID": user_id,
            },
        )
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json(), {"exists": True, "wizard_completed": False})
