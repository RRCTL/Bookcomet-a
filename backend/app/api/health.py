from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import settings

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    # Safe, non-secret flags for UI gating (same contract as GET /health).
    feature_process_intake_v2: bool = False
    ap_cross_vlm_configured: bool = False
    ap_auto_cross_verify_enabled: bool = False
    ap_cross_verify_pipeline_active: bool = False
    # True when REGISTER_INVITE_CODE is set — UI should collect invite_code (never the secret itself).
    register_invite_required: bool = False


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    cross_ok = bool(settings.ap_cross_vlm_model.strip())
    auto_on = settings.ap_auto_cross_verify_enabled
    return HealthResponse(
        status="ok",
        feature_process_intake_v2=settings.feature_process_intake_v2,
        ap_cross_vlm_configured=cross_ok,
        ap_auto_cross_verify_enabled=auto_on,
        ap_cross_verify_pipeline_active=cross_ok and auto_on,
        register_invite_required=bool(settings.register_invite_code),
    )

