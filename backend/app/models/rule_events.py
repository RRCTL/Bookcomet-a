import uuid

from sqlalchemy import Column, DateTime, JSON, String
from sqlalchemy.sql import func

from app.database import Base


class CompanyRuleHitEvent(Base):
    __tablename__ = "company_rule_hit_events"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False, index=True)
    trace_id = Column(String, nullable=True, index=True)
    rule_id = Column(String, nullable=False, index=True)
    source = Column(String, nullable=True, index=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)


class CompanyRuleAuditLog(Base):
    __tablename__ = "company_rule_audit_logs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False, index=True)
    trace_id = Column(String, nullable=True, index=True)
    rule_id = Column(String, nullable=True, index=True)
    action = Column(String, nullable=False, index=True)
    actor_user_id = Column(String, nullable=True, index=True)
    before_json = Column(JSON, nullable=True)
    after_json = Column(JSON, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
