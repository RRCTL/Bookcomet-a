from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.sql import func

from app.database import Base


class ChatTask(Base):
    __tablename__ = "chat_tasks"

    id = Column(String, primary_key=True)
    company_id = Column(String, ForeignKey("companies.id"), nullable=False, index=True)
    owner_user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(Text)
    processing_mode = Column(String(10))        # AR / AP / BANK / RECON / REPORT
    status = Column(String(20), default="idle") # idle / queued / processing / completed / failed
    is_shared_to_company = Column(Boolean, default=False)
    file_count = Column(Integer, default=0)
    page_count = Column(Integer, default=0)
    has_spreadsheet = Column(Boolean, default=False)
    bank_batch_ids = Column(JSON)
    ledger_batch_ids = Column(JSON)
    dup_warning = Column(Text)
    title_generated = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime, nullable=True)  # soft delete — GDPR right to erasure


class TaskMessage(Base):
    __tablename__ = "task_messages"

    id = Column(String, primary_key=True)
    task_id = Column(String, ForeignKey("chat_tasks.id"), nullable=False, index=True)
    sequence_index = Column(Integer, nullable=False)
    role = Column(String(20))           # user / assistant / system
    content_text = Column(Text)
    content_type = Column(String(30), default="text")  # text / ocr_result / spreadsheet / bank_txn / recon / report
    payload_json = Column(JSON)         # rich data (OCR rows, patches, etc.) — never logged
    created_at = Column(DateTime, server_default=func.now())


class TaskFile(Base):
    __tablename__ = "task_files"

    id = Column(String, primary_key=True)
    task_id = Column(String, ForeignKey("chat_tasks.id"), nullable=False, index=True)
    original_filename = Column(Text)
    storage_path = Column(Text)         # server-internal only, never sent to client
    file_size_bytes = Column(Integer)
    page_count = Column(Integer, default=1)
    mime_type = Column(String(100))
    import_batch_id = Column(Text, nullable=True)  # links to bank_transactions batch
    created_at = Column(DateTime, server_default=func.now())
    deleted_at = Column(DateTime, nullable=True)   # soft delete


class TaskStateSnapshot(Base):
    __tablename__ = "task_state_snapshots"

    id = Column(String, primary_key=True)
    task_id = Column(String, ForeignKey("chat_tasks.id"), nullable=False, index=True)
    state_type = Column(Text)           # recon_pool | report_data | spreadsheet
    payload_json = Column(JSON)
    version = Column(Integer, default=1)
    created_at = Column(DateTime, server_default=func.now())


class TaskAuditLog(Base):
    __tablename__ = "task_audit_log"

    id = Column(String, primary_key=True)
    task_id = Column(String, ForeignKey("chat_tasks.id"), nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    action = Column(Text)  # created|viewed|message_added|shared|unshared|file_uploaded|file_downloaded|deleted|hard_deleted
    ip_address = Column(Text)
    user_agent = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
