"""Create or refresh a local test login (idempotent).

Default credentials:
  Username: bookcomet
  Password: TestOnly1

Optional email (also accepted at login): bookcomet-test@example.com

Usage (from backend/):
  python scripts/create_test_user.py

Optional env overrides:
  TEST_USER_USERNAME, TEST_USER_EMAIL, TEST_USER_PASSWORD, TEST_USER_DISPLAY_NAME
"""
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import func

from app.core.password_policy import validate_password_strength
from app.core.security import hash_password
from app.database import SessionLocal
from app.models.identity import Company, Membership, User


def main() -> None:
    username = (os.environ.get("TEST_USER_USERNAME") or "bookcomet").strip().lower()
    email_raw = (os.environ.get("TEST_USER_EMAIL") or "bookcomet-test@example.com").strip().lower()
    email = email_raw or None
    password = os.environ.get("TEST_USER_PASSWORD") or "TestOnly1"
    display_name = (os.environ.get("TEST_USER_DISPLAY_NAME") or "Test User").strip()

    validate_password_strength(password)

    db = SessionLocal()
    try:
        user = db.query(User).filter(func.lower(User.username) == username).first()
        if not user and email:
            user = db.query(User).filter(func.lower(User.email) == email).first()
        pwd_hash = hash_password(password)

        if user:
            user.username = username
            user.email = email
            user.hashed_password = pwd_hash
            user.display_name = display_name
            user.is_active = True
            user.is_verified = True
            user.verification_token = None
            user.verification_token_expiry = None
            user.failed_login_count = 0
            user.locked_until = None
            db.flush()
            membership = db.query(Membership).filter(Membership.user_id == user.id).first()
            if not membership:
                company = Company(id=str(uuid.uuid4()), name=f"{display_name}'s Company")
                db.add(company)
                db.add(
                    Membership(
                        id=str(uuid.uuid4()),
                        user_id=user.id,
                        company_id=company.id,
                        role="owner",
                    )
                )
                db.flush()
            db.commit()
            print(f"Updated existing user: {username}")
        else:
            uid = str(uuid.uuid4())
            user = User(
                id=uid,
                username=username,
                email=email,
                display_name=display_name,
                hashed_password=pwd_hash,
                is_active=True,
                is_verified=True,
                verification_token=None,
                verification_token_expiry=None,
                failed_login_count=0,
            )
            db.add(user)
            company = Company(id=str(uuid.uuid4()), name=f"{display_name}'s Company")
            db.add(company)
            db.add(
                Membership(
                    id=str(uuid.uuid4()),
                    user_id=uid,
                    company_id=company.id,
                    role="owner",
                )
            )
            db.flush()
            db.commit()
            print(f"Created test user: {username}")

        print("")
        print("  Sign in with:")
        print(f"    Username: {username}")
        if email:
            print(f"    Email:    {email}  (also accepted)")
        print(f"    Password: {password}")
        print("")
        print("  (Do not use this account in production.)")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
