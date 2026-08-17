"""
Company Rule Memory models.

Each company has one RuleMemory row per processing mode
(AR, AP, BANK, OTHER).
The content column stores structured Markdown that the AI and the deterministic
rule engine both read.  Up to MAX_VERSIONS past versions are kept per row.
"""
import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.database import Base

VALID_MODES = {"AR", "AP", "BANK", "OTHER"}
MAX_VERSIONS = 5


class CompanyRuleMemory(Base):
    __tablename__ = "company_rule_memories"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, ForeignKey("companies.id"), nullable=False, index=True)
    mode = Column(String, nullable=False)          # AR | AP | BANK | OTHER
    content = Column(Text, nullable=False, default="")
    version = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    updated_by_user = Column(String, nullable=True)
    updated_by_type = Column(String, nullable=False, default="user")  # user | ai | import | system
    is_active = Column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("company_id", "mode", name="uq_rule_memory_company_mode"),
    )


class CompanyRuleMemoryVersion(Base):
    __tablename__ = "company_rule_memory_versions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    memory_id = Column(String, ForeignKey("company_rule_memories.id"), nullable=False, index=True)
    company_id = Column(String, nullable=False, index=True)
    mode = Column(String, nullable=False)
    version = Column(Integer, nullable=False)
    content = Column(Text, nullable=False, default="")
    saved_at = Column(DateTime, server_default=func.now())
    saved_by = Column(String, nullable=True)
    saved_by_type = Column(String, nullable=False, default="user")
