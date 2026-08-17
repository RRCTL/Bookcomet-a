import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text, Boolean, UniqueConstraint
from sqlalchemy.sql import func

from app.database import Base


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id = Column(String, primary_key=True)
    company_id = Column(String, ForeignKey("companies.id"), nullable=False, index=True)
    task_id = Column(String, ForeignKey("chat_tasks.id"), nullable=False, unique=True, index=True)
    owner_user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    folder_id = Column(String, ForeignKey("workflow_folders.id"), nullable=True, index=True)
    processing_mode = Column(String(20), nullable=False)
    graph_json = Column(JSON, nullable=False, default=dict)
    run_status = Column(String(30), nullable=False, default="draft")
    node_states_json = Column(JSON, nullable=True)
    title = Column(Text, nullable=True)
    title_generated = Column(Boolean, default=False)
    snapshot_message_id = Column(String, nullable=True)
    console_log_json = Column(JSON, nullable=True)
    archived_at = Column(DateTime, nullable=True)
    processing_removed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class WorkflowFolder(Base):
    __tablename__ = "workflow_folders"

    id = Column(String, primary_key=True)
    company_id = Column(String, ForeignKey("companies.id"), nullable=False, index=True)
    name = Column(Text, nullable=False)
    parent_id = Column(String, ForeignKey("workflow_folders.id"), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    # Module/processing-mode this folder is scoped to (AR/AP/BANK/OTHER/...).
    # NULL = untyped (legacy) folder that accepts any module.
    mode = Column(String(20), nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class WorkflowTemplate(Base):
    __tablename__ = "workflow_templates"

    id = Column(String, primary_key=True)
    company_id = Column(String, ForeignKey("companies.id"), nullable=False, index=True)
    name = Column(Text, nullable=False)
    processing_mode = Column(String(20), nullable=False)
    graph_json = Column(JSON, nullable=False, default=dict)
    is_default = Column(Boolean, default=False)
    created_by = Column(String, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class WorkflowSkill(Base):
    __tablename__ = "workflow_skills"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, ForeignKey("companies.id"), nullable=False, index=True)
    mode = Column(String(20), nullable=False)
    skill_key = Column(String(120), nullable=False)
    structured_json = Column(JSON, nullable=False, default=dict)
    generated_markdown = Column(Text, nullable=False, default="")
    version = Column(Integer, nullable=False, default=1)
    updated_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("company_id", "mode", "skill_key", name="uq_workflow_skill_company_mode_key"),
    )


class WorkflowSkillVersion(Base):
    __tablename__ = "workflow_skill_versions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    skill_id = Column(String, ForeignKey("workflow_skills.id"), nullable=False, index=True)
    company_id = Column(String, ForeignKey("companies.id"), nullable=False, index=True)
    mode = Column(String(20), nullable=False)
    skill_key = Column(String(120), nullable=False)
    version = Column(Integer, nullable=False)
    structured_json = Column(JSON, nullable=False, default=dict)
    generated_markdown = Column(Text, nullable=False, default="")
    saved_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    saved_at = Column(DateTime, server_default=func.now())


class WorkflowRunFile(Base):
    __tablename__ = "workflow_run_files"

    id = Column(String, primary_key=True)
    run_id = Column(String, ForeignKey("workflow_runs.id"), nullable=False, index=True)
    task_file_id = Column(String, ForeignKey("task_files.id"), nullable=False, index=True)
    vlm_job_id = Column(String, ForeignKey("background_jobs.id"), nullable=True)
    file_status = Column(String(20), nullable=False, default="pending")
    gate_result = Column(String(40), nullable=True)
    error_text = Column(Text, nullable=True)
    result_summary_json = Column(JSON, nullable=True)
    upload_batch_id = Column(String, nullable=True, index=True)
    uploaded_at = Column(DateTime, nullable=True)
    batch_committed_at = Column(DateTime, nullable=True)
    batch_table_preset = Column(String, nullable=True)
    batch_receipt_signal = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class WorkflowNodeExecution(Base):
    __tablename__ = "workflow_node_executions"

    id = Column(String, primary_key=True)
    run_id = Column(String, ForeignKey("workflow_runs.id"), nullable=False, index=True)
    company_id = Column(String, ForeignKey("companies.id"), nullable=False, index=True)
    node_id = Column(String, nullable=False, index=True)
    node_type = Column(String(40), nullable=False)
    item_key = Column(String, nullable=True, index=True)
    cache_key = Column(String(64), nullable=True, index=True)
    input_hash = Column(String(64), nullable=True)
    content_id = Column(String(64), nullable=True)
    storage_path = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    error_text = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    provider = Column(String(40), nullable=True)
    model = Column(String(120), nullable=True)
    token_usage_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class WorkflowPool2Package(Base):
    __tablename__ = "workflow_pool2_packages"

    id = Column(String, primary_key=True)
    run_id = Column(String, ForeignKey("workflow_runs.id"), nullable=False, index=True)
    company_id = Column(String, ForeignKey("companies.id"), nullable=False, index=True)
    task_id = Column(String, ForeignKey("chat_tasks.id"), nullable=False, index=True)
    processing_mode = Column(String(20), nullable=False)
    package_id = Column(String(64), nullable=False)
    storage_path = Column(Text, nullable=False)
    manifest_json = Column(JSON, nullable=True)
    snapshot_message_id = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
