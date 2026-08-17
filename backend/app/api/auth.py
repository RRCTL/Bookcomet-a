import hashlib
import hmac
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.password_policy import validate_password_strength
from app.core.mfa import new_totp_secret, provisioning_uri, verify_totp
from app.core.security import (
    create_access_token,
    create_mfa_challenge_token,
    create_refresh_token,
    decode_access_token,
    decode_mfa_challenge_token,
    hash_password,
    verify_password,
)
from app.api.deps import get_current_user
from app.core.text_limits import MAX_DISPLAY_NAME_CHARS, MAX_USERNAME_CHARS
from app.database import get_db
from app.models.auth_log import AuthAuditLog
from app.models.identity import Company, Membership, User

logger = logging.getLogger(__name__)

router = APIRouter()

_REFRESH_COOKIE = "refresh_token"
_LOCKOUT_ATTEMPTS = 5
_LOCKOUT_MINUTES = 15
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,%d}$" % MAX_USERNAME_CHARS)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    username: str = Field(..., max_length=MAX_USERNAME_CHARS)
    display_name: str = Field(..., max_length=MAX_DISPLAY_NAME_CHARS)
    password: str
    email: EmailStr | None = None
    # Required only when server has REGISTER_INVITE_CODE set (tunnel / shared host).
    invite_code: str | None = Field(default=None, max_length=256)

    @field_validator("username")
    @classmethod
    def _validate_username(cls, v: str) -> str:
        username = v.strip().lower()
        if not _USERNAME_RE.match(username):
            raise ValueError(
                f"Username must be 3–{MAX_USERNAME_CHARS} characters and use only "
                "letters, numbers, dots, underscores, or hyphens."
            )
        return username

    @field_validator("password")
    @classmethod
    def _validate_password(cls, v: str) -> str:
        validate_password_strength(v)
        return v


def _invite_code_matches(provided: str | None, expected: str) -> bool:
    got = (provided or "").strip()
    if len(got) != len(expected):
        return False
    return hmac.compare_digest(got.encode("utf-8"), expected.encode("utf-8"))


class LoginRequest(BaseModel):
    # Username or email
    identifier: str = Field(..., min_length=1, max_length=256)
    password: str = Field(..., max_length=256)


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _validate_new_password(cls, v: str) -> str:
        validate_password_strength(v)
        return v


class UpdateProfileRequest(BaseModel):
    display_name: str = Field(..., max_length=MAX_DISPLAY_NAME_CHARS)

    @field_validator("display_name")
    @classmethod
    def _validate_display_name(cls, v: str) -> str:
        name = v.strip()
        if not name:
            raise ValueError("Display name is required")
        return name


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginResponse(BaseModel):
    """Normal login or MFA challenge (SEC-CODE-009)."""
    access_token: str | None = None
    token_type: str = "bearer"
    mfa_required: bool = False
    mfa_token: str | None = None


class MfaVerifyRequest(BaseModel):
    mfa_token: str = Field(..., min_length=1, max_length=4096)
    code: str = Field(..., min_length=4, max_length=16)


class MfaEnableRequest(BaseModel):
    code: str = Field(..., min_length=4, max_length=16)


class MfaDisableRequest(BaseModel):
    password: str = Field(..., max_length=256)
    code: str = Field(..., min_length=4, max_length=16)


class MfaSetupResponse(BaseModel):
    secret: str
    otpauth_url: str
    mfa_enabled: bool


class UserResponse(BaseModel):
    id: str
    username: str
    email: str | None
    display_name: str
    is_verified: bool
    mfa_enabled: bool = False


class MessageResponse(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _normalize_identifier(identifier: str) -> str:
    return identifier.strip().lower()


def _user_by_identifier(db: Session, identifier: str) -> User | None:
    key = _normalize_identifier(identifier)
    if not key:
        return None
    return (
        db.query(User)
        .filter(
            or_(
                func.lower(User.username) == key,
                func.lower(User.email) == key,
            )
        )
        .first()
    )


def _utc_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _refresh_lifetime_days() -> int:
    return max(1, int(settings.refresh_token_expire_days))


def _token_subject(user: User) -> str:
    return user.email or user.username


def _session_version(user: User) -> int:
    return int(getattr(user, "session_version", 0) or 0)


def _bump_session_version(user: User) -> None:
    """SEC-CODE-008: invalidate outstanding access tokens."""
    user.session_version = _session_version(user) + 1


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        is_verified=True,
        mfa_enabled=bool(getattr(user, "mfa_enabled", False)),
    )


def _issue_session_tokens(
    user: User,
    *,
    request: Request,
    response: Response,
    db: Session,
    now: datetime | None = None,
) -> TokenResponse:
    stamp = now or datetime.now(timezone.utc)
    refresh_token = create_refresh_token()
    user.refresh_token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
    user.refresh_token_expires_at = stamp + timedelta(days=_refresh_lifetime_days())
    user.is_verified = True
    db.commit()
    access_token = create_access_token(
        user.id,
        _token_subject(user),
        session_version=_session_version(user),
    )
    _set_refresh_cookie(response, refresh_token)
    return TokenResponse(access_token=access_token)


def _require_user_from_access_header(request: Request, db: Session) -> User:
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    payload = decode_access_token(token or None)
    user = db.query(User).filter(User.id == payload["sub"]).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found")
    token_sv = payload.get("sv", 0)
    try:
        token_sv_i = int(token_sv or 0)
    except (TypeError, ValueError):
        token_sv_i = 0
    if token_sv_i != _session_version(user):
        raise HTTPException(status_code=401, detail="Session revoked — sign in again")
    return user


def _log_event(db: Session, event_type: str, user_id: str | None, request: Request, detail: str = "") -> None:
    db.add(AuthAuditLog(
        id=str(uuid.uuid4()),
        user_id=user_id,
        event_type=event_type,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        detail=detail,
    ))


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=token,
        httponly=True,
        secure=(settings.app_env != "local"),
        samesite="strict",
        max_age=settings.refresh_token_expire_days * 86400,
        path="/auth/refresh",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key=_REFRESH_COOKIE, path="/auth/refresh")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=MessageResponse)
def register(body: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    """Create a local account (username + password; email optional).

    When ``REGISTER_INVITE_CODE`` is set, ``invite_code`` must match (tunnel mode).
    When unset, anyone on the reachable host may self-register (local PC default).
    """
    expected_invite = settings.register_invite_code
    if expected_invite and not _invite_code_matches(body.invite_code, expected_invite):
        raise HTTPException(
            status_code=403,
            detail="Invalid or missing invite code",
        )

    username = body.username
    existing_username = db.query(User).filter(func.lower(User.username) == username).first()
    if existing_username:
        raise HTTPException(status_code=400, detail="Username already taken")

    email_norm: str | None = None
    if body.email is not None:
        email_norm = _normalize_email(str(body.email))
        existing_email = db.query(User).filter(func.lower(User.email) == email_norm).first()
        if existing_email:
            raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        id=str(uuid.uuid4()),
        username=username,
        email=email_norm,
        display_name=body.display_name.strip(),
        hashed_password=hash_password(body.password),
        is_active=True,
        is_verified=True,
        failed_login_count=0,
    )
    db.add(user)

    company = Company(id=str(uuid.uuid4()), name=f"{body.display_name.strip()}'s Company")
    db.add(company)
    db.add(Membership(
        id=str(uuid.uuid4()),
        user_id=user.id,
        company_id=company.id,
        role="owner",
    ))

    _log_event(db, "register", user.id, request)
    db.commit()

    return {"message": "Account created. You can now sign in."}


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    """Authenticate with username or email; may return an MFA challenge."""
    user = _user_by_identifier(db, body.identifier)

    invalid_err = HTTPException(status_code=401, detail="Invalid username or password")

    if not user or not user.hashed_password:
        raise invalid_err

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    now = datetime.now(timezone.utc)
    if user.locked_until:
        locked_until_aware = (
            user.locked_until.replace(tzinfo=timezone.utc)
            if user.locked_until.tzinfo is None
            else user.locked_until
        )
        if now < locked_until_aware:
            minutes_left = int((locked_until_aware - now).total_seconds() / 60) + 1
            raise HTTPException(
                status_code=403,
                detail=f"Account locked. Try again in {minutes_left} minute(s).",
            )

    if not verify_password(body.password, user.hashed_password):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        if user.failed_login_count >= _LOCKOUT_ATTEMPTS:
            user.locked_until = now + timedelta(minutes=_LOCKOUT_MINUTES)
            user.failed_login_count = 0
            _log_event(db, "login_failed", user.id, request, "account locked")
        else:
            _log_event(db, "login_failed", user.id, request)
        db.commit()
        raise invalid_err

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    user.is_verified = True

    if bool(getattr(user, "mfa_enabled", False)) and (user.mfa_secret or "").strip():
        mfa_token = create_mfa_challenge_token(user.id)
        _log_event(db, "login_mfa_challenge", user.id, request)
        db.commit()
        return LoginResponse(mfa_required=True, mfa_token=mfa_token)

    _log_event(db, "login", user.id, request)
    tokens = _issue_session_tokens(user, request=request, response=response, db=db, now=now)
    return LoginResponse(access_token=tokens.access_token)


@router.post("/logout", response_model=MessageResponse)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """Clear the refresh token cookie and invalidate the stored token."""
    refresh_token = request.cookies.get(_REFRESH_COOKIE)
    if refresh_token:
        token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        user = db.query(User).filter(User.refresh_token_hash == token_hash).first()
        if user:
            user.refresh_token_hash = None
            user.refresh_token_expires_at = None
            _log_event(db, "logout", user.id, request)
            db.commit()

    _clear_refresh_cookie(response)
    return {"message": "Logged out"}


@router.post("/refresh", response_model=TokenResponse)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    """Rotate the refresh token and issue a new access token."""
    refresh_token = request.cookies.get(_REFRESH_COOKIE)
    if not refresh_token:
        raise HTTPException(status_code=401, detail="No refresh token")

    token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
    user = (
        db.query(User)
        .filter(User.refresh_token_hash == token_hash)
        .with_for_update()
        .first()
    )
    if not user or not user.is_active:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    now = datetime.now(timezone.utc)
    exp = _utc_aware(user.refresh_token_expires_at)
    if exp is not None and now > exp:
        user.refresh_token_hash = None
        user.refresh_token_expires_at = None
        db.commit()
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    tokens = _issue_session_tokens(user, request=request, response=response, db=db, now=now)
    return tokens


@router.post("/mfa/verify", response_model=TokenResponse)
def mfa_verify(
    body: MfaVerifyRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Complete login after password + TOTP (SEC-CODE-009)."""
    payload = decode_mfa_challenge_token(body.mfa_token)
    user = db.query(User).filter(User.id == payload["sub"]).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found")
    if not bool(getattr(user, "mfa_enabled", False)) or not (user.mfa_secret or "").strip():
        raise HTTPException(status_code=400, detail="MFA is not enabled for this account")
    if not verify_totp(user.mfa_secret or "", body.code):
        _log_event(db, "mfa_failed", user.id, request)
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid authentication code")
    _log_event(db, "login", user.id, request, "mfa")
    return _issue_session_tokens(user, request=request, response=response, db=db)


@router.post("/mfa/setup", response_model=MfaSetupResponse)
def mfa_setup(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Start TOTP enrollment; scan otpauth_url, then POST /auth/mfa/enable."""
    if bool(getattr(user, "mfa_enabled", False)):
        raise HTTPException(status_code=400, detail="MFA is already enabled")
    secret = new_totp_secret()
    user.mfa_secret = secret
    user.mfa_enabled = False
    db.commit()
    return MfaSetupResponse(
        secret=secret,
        otpauth_url=provisioning_uri(secret, account_name=user.username or user.id),
        mfa_enabled=False,
    )


@router.post("/mfa/enable", response_model=MessageResponse)
def mfa_enable(
    body: MfaEnableRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Confirm TOTP enrollment with a valid code from the authenticator app."""
    secret = (user.mfa_secret or "").strip()
    if not secret:
        raise HTTPException(status_code=400, detail="Call /auth/mfa/setup first")
    if not verify_totp(secret, body.code):
        raise HTTPException(status_code=400, detail="Invalid authentication code")
    user.mfa_enabled = True
    _log_event(db, "mfa_enabled", user.id, request)
    db.commit()
    return {"message": "MFA enabled"}


@router.post("/mfa/disable", response_model=MessageResponse)
def mfa_disable(
    body: MfaDisableRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Disable TOTP MFA (requires password + current code)."""
    if not user.hashed_password or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if bool(getattr(user, "mfa_enabled", False)):
        if not verify_totp(user.mfa_secret or "", body.code):
            raise HTTPException(status_code=400, detail="Invalid authentication code")
    user.mfa_enabled = False
    user.mfa_secret = None
    _log_event(db, "mfa_disabled", user.id, request)
    db.commit()
    return {"message": "MFA disabled"}


@router.post("/revoke-sessions", response_model=MessageResponse)
def revoke_sessions(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """SEC-CODE-008: revoke refresh cookie and all outstanding access tokens."""
    _bump_session_version(user)
    user.refresh_token_hash = None
    user.refresh_token_expires_at = None
    _log_event(db, "sessions_revoked", user.id, request)
    db.commit()
    _clear_refresh_cookie(response)
    return {"message": "All sessions revoked. Sign in again."}


@router.get("/me", response_model=UserResponse)
def get_me(request: Request, db: Session = Depends(get_db)):
    """Return the authenticated user's profile."""
    user = _require_user_from_access_header(request, db)
    return _user_response(user)


@router.patch("/me", response_model=UserResponse)
def update_me(body: UpdateProfileRequest, request: Request, db: Session = Depends(get_db)):
    """Update the authenticated user's display name."""
    user = _require_user_from_access_header(request, db)
    user.display_name = body.display_name
    db.commit()
    db.refresh(user)
    return _user_response(user)


@router.post("/change-password", response_model=MessageResponse)
def change_password(body: ChangePasswordRequest, request: Request, db: Session = Depends(get_db)):
    """Change password while authenticated (requires current password)."""
    user = _require_user_from_access_header(request, db)

    if not user.hashed_password or not verify_password(body.old_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    user.hashed_password = hash_password(body.new_password)
    user.refresh_token_hash = None
    user.refresh_token_expires_at = None
    _bump_session_version(user)
    _log_event(db, "password_changed", user.id, request)
    db.commit()

    return {"message": "Password changed successfully"}
