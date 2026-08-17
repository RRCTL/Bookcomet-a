"""
One-shot: create company_context CompanyRule from existing CompanyManual + CompanyProfile.

  cd backend && python -m scripts.migrate_knowledge_context

Requires DATABASE_URL / same DB config as the API.
"""
from __future__ import annotations

import sys
import uuid

# Allow running as script
if __name__ == "__main__":
    sys.path.insert(0, ".")

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.company_context import CompanyProfile, CompanyRule
from app.models.company_manual import CompanyManual
from fastapi import HTTPException

from app.services.rule_governance import RULE_TYPE_COMPANY_CONTEXT, validate_company_context_payload

CONTEXT_NAME = "Business context"


def _header(profile: CompanyProfile | None) -> str:
    if not profile:
        return ""
    lines: list[str] = []
    if profile.company_name:
        lines.append(f"- **Company:** {profile.company_name}")
    if profile.industry:
        lines.append(f"- **Industry:** {profile.industry}")
    if profile.accounting_basis:
        lines.append(f"- **Accounting basis:** {profile.accounting_basis}")
    if profile.fiscal_year_end:
        lines.append(f"- **Fiscal year end:** {profile.fiscal_year_end}")
    kw = profile.company_name_keywords
    if kw and isinstance(kw, list) and kw:
        lines.append(f"- **Name keywords:** {', '.join(str(x) for x in kw)}")
    if not lines:
        return ""
    return "## Profile\n" + "\n".join(lines)


def run(db: Session) -> int:
    manuals = db.query(CompanyManual).all()
    n = 0
    for man in manuals:
        cid = man.company_id
        existing = (
            db.query(CompanyRule)
            .filter(
                CompanyRule.company_id == cid,
                CompanyRule.rule_type == RULE_TYPE_COMPANY_CONTEXT,
            )
            .first()
        )
        if existing:
            continue
        profile = db.query(CompanyProfile).filter(CompanyProfile.company_id == cid).first()
        h = _header(profile)
        md = (man.content or "").strip()
        parts = [p for p in (h, md) if p]
        body = "\n\n".join(parts).strip()
        if not body:
            continue
        try:
            validate_company_context_payload(body=body, use_when=None)
        except HTTPException:
            continue
        db.add(
            CompanyRule(
                id=str(uuid.uuid4()),
                company_id=cid,
                rule_name=CONTEXT_NAME,
                rule_type=RULE_TYPE_COMPANY_CONTEXT,
                vendor_pattern=None,
                keyword_pattern=None,
                amount_pattern=None,
                document_type=None,
                rule_json={"use_when": None, "body": body},
                priority=0,
                is_active=True,
                notes=None,
                created_by="migrate_knowledge_context",
            )
        )
        n += 1
    db.commit()
    return n


if __name__ == "__main__":
    session = SessionLocal()
    try:
        count = run(session)
        print(f"Inserted {count} company_context row(s).")
    finally:
        session.close()
