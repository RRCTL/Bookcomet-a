"""Aggregated workspace activity for cross-browser polling (company-scoped)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.bank_statements import BankUploadActiveRow, list_active_bank_upload_jobs_for_company
from app.api.deps import get_current_company_id, get_current_user
from app.api.jobs import JobStatusResponse, _job_to_out
from app.database import get_db
from app.models.background_job import BackgroundJob
from app.models.identity import User

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


class WorkspaceActivityResponse(BaseModel):
    bank_uploads: list[BankUploadActiveRow]
    background_jobs: list[JobStatusResponse]


@router.get("/activity", response_model=WorkspaceActivityResponse)
async def get_workspace_activity(
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> WorkspaceActivityResponse:
    """Single poll: in-flight bank uploads (in-memory) + queued/running background jobs (DB)."""
    _ = user
    bank_raw = list_active_bank_upload_jobs_for_company(company_id)
    bank_uploads = [BankUploadActiveRow.model_validate(r) for r in bank_raw]
    rows = (
        db.query(BackgroundJob)
        .filter(
            BackgroundJob.company_id == company_id,
            BackgroundJob.status.in_(("queued", "running")),
        )
        .order_by(BackgroundJob.created_at.desc())
        .limit(50)
        .all()
    )
    return WorkspaceActivityResponse(
        bank_uploads=bank_uploads,
        background_jobs=[_job_to_out(j) for j in rows],
    )
