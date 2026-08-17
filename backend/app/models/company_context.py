from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.sql import func

from app.database import Base


class CompanyProfile(Base):
    __tablename__ = "company_profiles"

    id = Column(String, primary_key=True)
    company_id = Column(String, ForeignKey("companies.id"), nullable=False, unique=True, index=True)
    industry = Column(String, nullable=True)
    accounting_basis = Column(String, nullable=True)
    fiscal_year_end = Column(String, nullable=True)
    company_name = Column(String, nullable=True)
    company_name_keywords = Column(JSON, nullable=True)
    custom_settings = Column(JSON, nullable=True)
    profile_md = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class CompanyRule(Base):
    __tablename__ = "company_rules"

    id = Column(String, primary_key=True)
    company_id = Column(String, ForeignKey("companies.id"), nullable=False, index=True)
    rule_name = Column(String, nullable=False)
    rule_type = Column(String, nullable=False, default="company_custom")
    vendor_pattern = Column(String, nullable=True)
    keyword_pattern = Column(String, nullable=True)
    amount_pattern = Column(String, nullable=True)
    document_type = Column(String, nullable=True)
    rule_json = Column(JSON, nullable=False)
    priority = Column(Integer, nullable=False, default=100)
    is_active = Column(Boolean, nullable=False, default=True)
    hit_count = Column(Integer, nullable=False, default=0)
    last_hit_at = Column(DateTime, nullable=True)
    last_hit_source = Column(String, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    notes = Column(Text, nullable=True)

