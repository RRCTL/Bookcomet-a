import os
import re
import uuid

from fastapi import Depends, Header, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.database import get_db
from app.models.identity import Company, Membership, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

# Safe filesystem / tenant id segment (UUID and simple slugs). Blocks path traversal.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def allow_legacy_header_auth() -> bool:
    """Opt-in unauthenticated system/default header auth for local scripts/tests."""
    return os.getenv("ALLOW_LEGACY_HEADER_AUTH", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def validate_tenant_id(value: str, *, field_name: str = "company_id") -> str:
    """Reject empty, path-like, or otherwise unsafe tenant/storage id segments."""
    cleaned = (value or "").strip()
    if not cleaned or ".." in cleaned or "/" in cleaned or "\\" in cleaned:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}")
    if not _SAFE_ID_RE.match(cleaned):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}")
    return cleaned


def get_trace_id(
    x_trace_id: str | None = Header(default=None, alias="X-Trace-ID")
) -> str:
    """Resolve or generate request trace ID for auditability."""
    trace_id = (x_trace_id or "").strip()
    return trace_id or str(uuid.uuid4())


def _load_active_jwt_user(db: Session, payload: dict) -> User:
    """SEC-CODE-010: require active user whose session_version matches the access token."""
    user = db.query(User).filter(User.id == payload["sub"]).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    if not _session_version_ok(user, payload):
        raise HTTPException(status_code=401, detail="Session revoked — sign in again")
    return user


def get_current_user_id(
    token: str | None = Depends(oauth2_scheme),
    x_user_id: str | None = Header(default=None, alias="X-User-ID"),
    db: Session = Depends(get_db),
) -> str:
    """Resolve current user from JWT, or legacy X-User-ID when explicitly enabled.

    SEC-CODE-010: JWT path also enforces is_active + session_version.
    """
    if token:
        payload = decode_access_token(token)
        return _load_active_jwt_user(db, payload).id
    if allow_legacy_header_auth():
        user_id = (x_user_id or "system").strip()
        if user_id:
            return user_id
    raise HTTPException(status_code=401, detail="Not authenticated")


def _ensure_default_identity(db: Session) -> None:
    """Create local default user/company/membership if missing."""
    default_user = db.query(User).filter(User.id == "system").first()
    if default_user is None:
        default_user = User(
            id="system",
            email="system@local.test",
            display_name="Local System User",
        )
        db.add(default_user)

    default_company = db.query(Company).filter(Company.id == "default").first()
    if default_company is None:
        default_company = Company(id="default", name="Default Local Company")
        db.add(default_company)

    default_membership = db.query(Membership).filter(
        Membership.user_id == "system",
        Membership.company_id == "default",
    ).first()
    if default_membership is None:
        db.add(
            Membership(
                id=str(uuid.uuid4()),
                user_id="system",
                company_id="default",
                role="owner",
            )
        )

    db.flush()
    db.commit()


def _session_version_ok(user: User, payload: dict) -> bool:
    """SEC-CODE-008: access token sv must match users.session_version."""
    token_sv = payload.get("sv", 0)
    try:
        token_sv_i = int(token_sv or 0)
    except (TypeError, ValueError):
        token_sv_i = 0
    user_sv = int(getattr(user, "session_version", 0) or 0)
    return token_sv_i == user_sv


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Validate Bearer JWT and return the authenticated User."""
    payload = decode_access_token(token)
    return _load_active_jwt_user(db, payload)


def get_current_company_id(
    x_company_id: str | None = Header(default=None, alias="X-Company-ID"),
    token: str | None = Depends(oauth2_scheme),
    x_user_id: str | None = Header(default=None, alias="X-User-ID"),
    db: Session = Depends(get_db),
) -> str:
    """Resolve and validate tenant scope for this request.

    Priority:
      1. If a valid JWT is present, use the JWT sub as the authoritative user ID.
      2. Otherwise, only when ALLOW_LEGACY_HEADER_AUTH=true, fall back to X-User-ID.
    """
    company_id = validate_tenant_id(
        (x_company_id or "default").strip() or "default",
        field_name="company_id",
    )

    jwt_user_id: str | None = None
    if token:
        payload = decode_access_token(token)
        # SEC-CODE-010: reject revoked / inactive JWTs on tenant-scoped routes.
        jwt_user_id = _load_active_jwt_user(db, payload).id
    elif not allow_legacy_header_auth():
        raise HTTPException(status_code=401, detail="Not authenticated")

    user_id = (jwt_user_id or (x_user_id or "system")).strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user identity")

    # Legacy local bootstrap (opt-in only)
    if (
        allow_legacy_header_auth()
        and jwt_user_id is None
        and user_id == "system"
        and company_id == "default"
    ):
        _ensure_default_identity(db)
        return company_id

    membership = db.query(Membership).filter(
        Membership.user_id == user_id,
        Membership.company_id == company_id,
    ).first()
    if membership is None:
        raise HTTPException(status_code=403, detail="User is not a member of this company")

    return company_id


def require_company_owner(
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> str:
    """Require JWT user to be owner of the active company (X-Company-ID)."""
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.company_id == company_id,
    ).first()
    if membership is None or membership.role != "owner":
        raise HTTPException(
            status_code=403,
            detail="Only the company owner can perform this action",
        )
    return company_id
