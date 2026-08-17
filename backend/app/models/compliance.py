import uuid

from sqlalchemy import Column, DateTime, JSON, String, Text
from sqlalchemy.sql import func

from app.database import Base


class OcrCompletionEvent(Base):
    __tablename__ = "ocr_completion_events"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False, index=True)
    trace_id = Column(String, nullable=False, index=True)
    filename = Column(String, nullable=True)
    stage = Column(String, nullable=False, index=True)  # ocr_complete | ai_complete
    source = Column(String, nullable=True, index=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)


class AuditPackageArchive(Base):
    __tablename__ = "audit_package_archives"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False, index=True)
    trace_id_filter = Column(String, nullable=True, index=True)
    content_hash = Column(String, nullable=False, index=True)
    payload_json = Column(JSON, nullable=False)
    immutable_note = Column(Text, nullable=False)
    created_by = Column(String, nullable=True, index=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
