import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.identity import Company, Membership, User

router = APIRouter()


class CompanyOut(BaseModel):
    id: str
    name: str
    role: str


class CreateCompanyRequest(BaseModel):
    name: str


class RenameCompanyRequest(BaseModel):
    name: str


class DeleteCompanyBody(BaseModel):
    confirm_name: str


class DeleteCompanyResponse(BaseModel):
    deleted_id: str
    suggested_company_id: str | None = None


@router.get("/companies/mine", response_model=list[CompanyOut])
async def list_my_companies(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all companies the authenticated user belongs to."""
    memberships = (
        db.query(Membership)
        .filter(Membership.user_id == current_user.id)
        .all()
    )

    results: list[CompanyOut] = []
    for m in memberships:
        company = db.query(Company).filter(Company.id == m.company_id).first()
        if company is None:
            continue
        results.append(
            CompanyOut(
                id=company.id,
                name=company.name,
                role=m.role,
            )
        )

    return results


@router.post("/companies", response_model=CompanyOut, status_code=201)
async def create_company(
    body: CreateCompanyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new company and assign the current user as owner."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Company name cannot be empty")

    company = Company(id=str(uuid.uuid4()), name=name)
    db.add(company)

    membership = Membership(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        company_id=company.id,
        role="owner",
    )
    db.add(membership)

    db.commit()

    return CompanyOut(
        id=company.id,
        name=company.name,
        role="owner",
    )


@router.patch("/companies/{company_id}/name", response_model=CompanyOut)
async def rename_company(
    company_id: str,
    body: RenameCompanyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Rename a company. Only the owner (Admin) can rename."""
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.company_id == company_id,
    ).first()

    if membership is None:
        raise HTTPException(status_code=403, detail="You are not a member of this company")
    if membership.role != "owner":
        raise HTTPException(status_code=403, detail="Only the company Admin can rename it")

    company = db.query(Company).filter(Company.id == company_id).first()
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")

    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Company name cannot be empty")

    company.name = name
    db.commit()

    return CompanyOut(
        id=company.id,
        name=company.name,
        role=membership.role,
    )


@router.delete("/companies/{company_id}", response_model=DeleteCompanyResponse)
async def delete_company(
    company_id: str,
    body: DeleteCompanyBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Hard-delete a company and all its data. Owner only; cannot delete your last workspace."""
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.company_id == company_id,
    ).first()

    if membership is None:
        raise HTTPException(status_code=403, detail="You are not a member of this company")
    if membership.role != "owner":
        raise HTTPException(status_code=403, detail="Only the company Admin can delete it")

    mine = (
        db.query(Membership)
        .filter(Membership.user_id == current_user.id)
        .all()
    )
    if len(mine) <= 1:
        raise HTTPException(
            status_code=400,
            detail="You cannot delete your only workspace. Create another workspace first.",
        )

    company = db.query(Company).filter(Company.id == company_id).first()
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")

    conf = body.confirm_name.strip()
    if conf != company.name.strip():
        raise HTTPException(status_code=400, detail="Name does not match this workspace")

    suggested = (
        db.query(Membership)
        .filter(
            Membership.user_id == current_user.id,
            Membership.company_id != company_id,
        )
        .order_by(Membership.company_id.asc())
        .first()
    )
    suggested_id: str | None = suggested.company_id if suggested else None

    from app.services.company_purge import delete_company_purge

    try:
        delete_company_purge(db, company_id)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return DeleteCompanyResponse(deleted_id=company_id, suggested_company_id=suggested_id)
