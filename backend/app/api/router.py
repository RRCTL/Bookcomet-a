from fastapi import APIRouter

from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.ocr import router as ocr_router
from app.api.reconciliation import router as reconciliation_router
from app.api.bank_statements import router as bank_statements_router
from app.api.settings import router as settings_router
from app.api.identity import router as identity_router
from app.api.company_context import router as company_context_router
from app.api.compliance import router as compliance_router
from app.api.ai_chat import router as ai_chat_router
from app.api.tasks import router as tasks_router
from app.api.token_usage import router as token_usage_router
from app.api.other import router as other_router
from app.api.rule_memory import router as rule_memory_router
from app.api.company_manual import router as company_manual_router
from app.api.exclusion_rules import router as exclusion_rules_router
from app.api.company_rules import router as company_rules_router
from app.api.dashboard import router as dashboard_router
from app.api.companies import router as companies_router
from app.api.jobs import router as jobs_router
from app.api.workflows import router as workflows_router
from app.api.ocr_journals import router as ocr_journals_router
from app.api.workspace_activity import router as workspace_activity_router


api_router = APIRouter()
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(health_router, tags=["health"])
api_router.include_router(ocr_router, tags=["ocr"])
api_router.include_router(reconciliation_router, prefix="/reconciliation", tags=["reconciliation"])
api_router.include_router(ocr_journals_router, prefix="/ocr-journals", tags=["ocr-journals"])
api_router.include_router(bank_statements_router, tags=["bank-statements"])
api_router.include_router(settings_router, tags=["settings"])
api_router.include_router(identity_router, tags=["identity"])
api_router.include_router(company_context_router, tags=["company-context"])
api_router.include_router(compliance_router, tags=["compliance"])
api_router.include_router(ai_chat_router, tags=["ai-chat"])
api_router.include_router(jobs_router, tags=["jobs"])
api_router.include_router(workflows_router, tags=["workflows"])
api_router.include_router(workspace_activity_router, tags=["workspace"])
api_router.include_router(tasks_router, tags=["tasks"])
api_router.include_router(token_usage_router, tags=["token-usage"])
api_router.include_router(other_router, tags=["other"])
api_router.include_router(rule_memory_router, tags=["rule-memory"])
api_router.include_router(company_manual_router, tags=["company-manual"])
api_router.include_router(exclusion_rules_router, tags=["exclusion-rules"])
api_router.include_router(company_rules_router, tags=["company-rules"])
api_router.include_router(dashboard_router, tags=["dashboard"])
api_router.include_router(companies_router, tags=["companies"])

