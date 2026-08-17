from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.sql import func

from app.database import Base


class BackgroundJob(Base):
    """
    Durable async job (OCR, AI chat) so work continues after the client disconnects.
    Poll GET /api/jobs/{id} until status is completed or failed.
    """

    __tablename__ = "background_jobs"

    id = Column(String, primary_key=True)
    company_id = Column(String, ForeignKey("companies.id"), nullable=False, index=True)
    owner_user_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    job_type = Column(String(32), nullable=False, index=True)  # ocr | ai_chat
    status = Column(String(20), nullable=False, default="queued", index=True)
    # queued | running | completed | failed
    trace_id = Column(String(64), nullable=True)
    task_id = Column(String, ForeignKey("chat_tasks.id"), nullable=True, index=True)
    request_json = Column(JSON, nullable=True)
    result_json = Column(JSON, nullable=True)
    error_text = Column(Text, nullable=True)
    progress_percent = Column(String(16), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
