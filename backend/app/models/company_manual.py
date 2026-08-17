"""
Company Manual model.

One manual per company (no mode split — it is a global business knowledge document).
Stores free-text Markdown with sections:
  ## Key Clients
  ## Key Vendors
  ## Risk & Compliance Rules
  ## Seasonal Patterns
  ## Company Glossary

Up to MAX_VERSIONS past versions are kept for rollback.
"""
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.database import Base

MAX_MANUAL_VERSIONS = 5

MANUAL_SECTIONS = [
    "Key Clients",
    "Key Vendors",
    "Risk & Compliance Rules",
    "Seasonal Patterns",
    "Company Glossary",
]


class CompanyManual(Base):
    __tablename__ = "company_manuals"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, ForeignKey("companies.id"), nullable=False, unique=True, index=True)
    content = Column(Text, nullable=False, default="")
    version = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    updated_by_user = Column(String, nullable=True)
    updated_by_type = Column(String, nullable=False, default="user")  # user | ai | wizard | system


class CompanyManualVersion(Base):
    __tablename__ = "company_manual_versions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    manual_id = Column(String, ForeignKey("company_manuals.id"), nullable=False, index=True)
    company_id = Column(String, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    content = Column(Text, nullable=False, default="")
    saved_at = Column(DateTime, server_default=func.now())
    saved_by = Column(String, nullable=True)
    saved_by_type = Column(String, nullable=False, default="user")
