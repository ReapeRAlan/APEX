"""
APEX Authentication Service — JWT multi-role auth.

Roles: inspector / subdelegado / director_semarnat / tecnico_regenera / admin
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from ..config import settings
from ..db.session import SessionLocal
from ..db.models import User

logger = logging.getLogger("apex.auth")

# ── Password hashing ──
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ── JWT ──
security = HTTPBearer(auto_error=False)

VALID_ROLES = {"inspector", "subdelegado", "director_semarnat", "tecnico_regenera", "admin"}

# ── Role-based permissions ──
ROLE_PERMISSIONS = {
    "inspector": ["read:alerts", "read:beliefs", "read:routes"],
    "subdelegado": ["read:alerts", "read:beliefs", "read:routes", "read:reports", "write:monitoring"],
    "director_semarnat": ["read:*", "write:monitoring"],
    "tecnico_regenera": ["read:*", "write:*"],
    "admin": ["*"],
}


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(
    user_id: int,
    email: str,
    role: str,
    expires_hours: Optional[int] = None,
) -> str:
    """Create a JWT token."""
    if expires_hours is None:
        expires_hours = settings.JWT_EXPIRE_HOURS

    expire = datetime.utcnow() + timedelta(hours=expires_hours)
    payload = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token."""
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token inválido: {e}",
        )


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[dict]:
    """
    FastAPI dependency to extract current user from JWT.
    Returns None if no token (allows unauthenticated endpoints to work).
    """
    if credentials is None:
        return None

    payload = decode_token(credentials.credentials)
    return {
        "id": int(payload["sub"]),
        "email": payload["email"],
        "role": payload["role"],
    }


def require_role(*roles: str):
    """FastAPI dependency factory to require specific roles."""
    def _check(
        credentials: HTTPAuthorizationCredentials = Depends(security),
    ):
        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Se requiere autenticación",
            )
        payload = decode_token(credentials.credentials)
        user_role = payload.get("role", "")
        if user_role not in roles and "admin" not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Rol '{user_role}' no tiene permiso. Se requiere: {', '.join(roles)}",
            )
        return {
            "id": int(payload["sub"]),
            "email": payload["email"],
            "role": user_role,
        }
    return _check


def authenticate_user(email: str, password: str) -> Optional[dict]:
    """Verify credentials and return user dict or None."""
    with SessionLocal() as session:
        user = session.query(User).filter(User.email == email).first()
        if user is None:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        if not user.is_active:
            return None
        return {
            "id": user.id,
            "email": user.email,
            "role": user.role,
            "full_name": user.full_name,
        }


def create_user(
    email: str,
    password: str,
    role: str = "inspector",
    full_name: Optional[str] = None,
) -> dict:
    """Create a new user."""
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role: {role}. Valid: {VALID_ROLES}")

    with SessionLocal() as session:
        existing = session.query(User).filter(User.email == email).first()
        if existing:
            raise ValueError(f"User with email {email} already exists")

        user = User(
            email=email,
            hashed_password=hash_password(password),
            role=role,
            full_name=full_name,
        )
        session.add(user)
        session.commit()
        session.refresh(user)

        return {
            "id": user.id,
            "email": user.email,
            "role": user.role,
            "full_name": user.full_name,
        }
