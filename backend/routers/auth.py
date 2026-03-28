"""
APEX Auth Router — Login, registration, and user management.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..services.auth_service import (
    authenticate_user,
    create_access_token,
    create_user,
    get_current_user,
    require_role,
)

logger = logging.getLogger("apex.auth")
router = APIRouter()


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str
    role: str = "inspector"
    full_name: Optional[str] = None


@router.post("/auth/login")
async def login(req: LoginRequest):
    """Authenticate and return JWT token."""
    user = authenticate_user(req.email, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    token = create_access_token(
        user_id=user["id"],
        email=user["email"],
        role=user["role"],
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "email": user["email"],
            "role": user["role"],
            "full_name": user["full_name"],
        },
    }


@router.post("/auth/register")
async def register(
    req: RegisterRequest,
    current_user: dict = Depends(require_role("admin", "tecnico_regenera")),
):
    """Register a new user (admin/tecnico only)."""
    try:
        user = create_user(
            email=req.email,
            password=req.password,
            role=req.role,
            full_name=req.full_name,
        )
        return {"status": "created", "user": user}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/auth/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    """Get current authenticated user info."""
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")
    return current_user
