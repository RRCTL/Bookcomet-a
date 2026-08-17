import uuid
from typing import Optional
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import (
    allow_legacy_header_auth,
    get_current_company_id,
    get_current_user,
    require_company_owner,
    validate_tenant_id,
)
from app.database import get_db
from app.models.identity import Company, Membership, User

router = APIRouter()


class CreateUserRequest(BaseModel):
    email: str
    display_name: str
    user_id: Optional[str] = None


class CreateCompanyRequest(BaseModel):
    name: str
    company_id: Optional[str] = None


# SEC-CODE-011: only these roles may be assigned via /identity/memberships.
_ALLOWED_MEMBERSHIP_ROLES = frozenset({"owner", "accountant"})


class CreateMembershipRequest(BaseModel):
    user_id: str
    company_id: str | None = None
    role: str = "accountant"


@router.post("/identity/bootstrap-default")
async def bootstrap_default_identity(db: Session = Depends(get_db)):
    """Idempotent bootstrap for local development (legacy header auth only)."""
    if not allow_legacy_header_auth():
        raise HTTPException(
            status_code=403,
            detail="Legacy identity bootstrap disabled. Set ALLOW_LEGACY_HEADER_AUTH=true for local scripts.",
        )
    user = db.query(User).filter(User.id == "system").first()
    if user is None:
        user = User(id="system", email="system@local.test", display_name="Local System User")
        db.add(user)

    company = db.query(Company).filter(Company.id == "default").first()
    if company is None:
        company = Company(id="default", name="Default Local Company")
        db.add(company)

    membership = db.query(Membership).filter(
        Membership.user_id == "system",
        Membership.company_id == "default",
    ).first()
    if membership is None:
        membership = Membership(
            id=str(uuid.uuid4()),
            user_id="system",
            company_id="default",
            role="owner",
        )
        db.add(membership)

    db.flush()
    db.commit()
    return {
        "user_id": user.id,
        "company_id": company.id,
        "membership_role": membership.role,
        "status": "ok",
    }


@router.post("/identity/users")
async def create_user(
    payload: CreateUserRequest,
    _user: User = Depends(get_current_user),
    _company_id: str = Depends(require_company_owner),
    db: Session = Depends(get_db),
):
    email = payload.email.strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(status_code=400, detail="Invalid email format")

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=409, detail="Email already exists")

    user_id = payload.user_id or str(uuid.uuid4())
    if payload.user_id is not None:
        user_id = validate_tenant_id(user_id, field_name="user_id")

    user = User(
        id=user_id,
        email=email,
        display_name=payload.display_name.strip(),
    )
    db.add(user)
    db.commit()
    return {"id": user.id, "email": user.email, "display_name": user.display_name}


@router.post("/identity/companies")
async def create_company(
    payload: CreateCompanyRequest,
    _user: User = Depends(get_current_user),
    _company_id: str = Depends(require_company_owner),
    db: Session = Depends(get_db),
):
    # SEC-CODE-011: always mint a new id — do not accept a client-chosen company_id.
    company_id = str(uuid.uuid4())
    company = Company(
        id=company_id,
        name=payload.name.strip(),
    )
    db.add(company)
    db.commit()
    return {"id": company.id, "name": company.name}


@router.post("/identity/memberships")
async def create_membership(
    payload: CreateMembershipRequest,
    _user: User = Depends(get_current_user),
    company_id: str = Depends(require_company_owner),
    db: Session = Depends(get_db),
):
    user_id = validate_tenant_id(payload.user_id, field_name="user_id")
    # SEC-CODE-011: memberships may only target the active X-Company-ID.
    requested = (payload.company_id or "").strip()
    if requested and requested != company_id:
        raise HTTPException(
            status_code=403,
            detail="Memberships can only be created for the active company",
        )
    role = (payload.role or "accountant").strip().lower()
    if role not in _ALLOWED_MEMBERSHIP_ROLES:
        raise HTTPException(status_code=400, detail="Invalid membership role")
    user = db.query(User).filter(User.id == user_id).first()
    company = db.query(Company).filter(Company.id == company_id).first()
    if not user or not company:
        raise HTTPException(status_code=404, detail="User or company not found")

    existing = db.query(Membership).filter(
        Membership.user_id == user_id,
        Membership.company_id == company_id,
    ).first()
    if existing:
        return {
            "id": existing.id,
            "user_id": existing.user_id,
            "company_id": existing.company_id,
            "role": existing.role,
        }

    membership = Membership(
        id=str(uuid.uuid4()),
        user_id=user_id,
        company_id=company_id,
        role=role,
    )
    db.add(membership)
    db.commit()
    return {
        "id": membership.id,
        "user_id": membership.user_id,
        "company_id": membership.company_id,
        "role": membership.role,
    }


@router.get("/identity/me")
async def get_identity_scope(
    current_user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    user_id = current_user.id
    user = current_user
    memberships = db.query(Membership).filter(Membership.user_id == user_id).all()
    return {
        "user_id": user_id,
        "company_id": company_id,
        "display_name": user.display_name if user else None,
        "memberships": [
            {
                "company_id": m.company_id,
                "role": m.role,
            }
            for m in memberships
        ],
    }
