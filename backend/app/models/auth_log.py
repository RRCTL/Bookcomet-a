from sqlalchemy import Column, DateTime, String
from sqlalchemy.sql import func

from app.database import Base


class AuthAuditLog(Base):
    __tablename__ = "auth_audit_logs"

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=True, index=True)
    event_type = Column(String, nullable=False, index=True)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    detail = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
