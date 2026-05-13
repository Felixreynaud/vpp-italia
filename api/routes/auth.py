"""Authentication — POST /api/v1/auth/login returns a signed JWT."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, status
from jose import jwt
from pydantic import BaseModel

router = APIRouter()

_DEV_USERS = {
    "admin": "admin",
    "operator": "operator",
}


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/api/v1/auth/login", response_model=LoginResponse, tags=["auth"])
async def login(req: LoginRequest) -> dict[str, Any]:
    """Issue a JWT for the given credentials.

    All environments validate the credentials against _DEV_USERS. In production
    this dict should be replaced by a real user-store lookup (DB + bcrypt).
    """
    secret = os.environ["JWT_SECRET_KEY"]
    algorithm = os.getenv("JWT_ALGORITHM", "HS256")

    expected = _DEV_USERS.get(req.username)
    if expected is None or expected != req.password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    roles = ["admin"] if req.username == "admin" else ["operator"]

    expires = datetime.now(UTC) + timedelta(hours=8)
    payload = {
        "sub": req.username,
        "roles": roles,
        "exp": expires,
    }
    token = jwt.encode(payload, secret, algorithm=algorithm)
    return {"access_token": token, "token_type": "bearer"}
