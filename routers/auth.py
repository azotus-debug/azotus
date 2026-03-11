"""JWT authentication endpoints for the Omega editor."""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import bcrypt
import jwt
from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel

import config

logger = logging.getLogger("OmegaAuth")

router = APIRouter(tags=["auth"])

# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def create_jwt(claims: dict, expiry_hours: Optional[int] = None) -> str:
    """Create a signed JWT with the given claims."""
    exp_hours = expiry_hours or config.OMEGA_JWT_EXPIRY_HOURS
    payload = {
        **claims,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=exp_hours),
    }
    return jwt.encode(payload, config.OMEGA_JWT_SECRET, algorithm="HS256")


def verify_jwt(token: str) -> Optional[dict]:
    """Verify and decode a JWT. Returns claims dict or None."""
    try:
        return jwt.decode(token, config.OMEGA_JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# ---------------------------------------------------------------------------
# User file helpers
# ---------------------------------------------------------------------------

def _load_users() -> list:
    """Load users from the JSON users file."""
    users_path = Path(config.OMEGA_USERS_FILE)
    if not users_path.exists():
        return []
    try:
        with open(users_path, "r") as f:
            return json.load(f)
    except Exception:
        logger.warning("Failed to load users file: %s", users_path)
        return []


def _find_user(username: str) -> Optional[dict]:
    """Find a user by username."""
    for user in _load_users():
        if user.get("username") == username:
            return user
    return None


def _verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str


class ReviewLinkRequest(BaseModel):
    job_id: str
    email: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/api/auth/login")
async def login(body: LoginRequest):
    """Authenticate with username + password, returns a JWT."""
    user = _find_user(body.username)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not _verify_password(body.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    role = user.get("role", "admin")
    token = create_jwt({"sub": body.username, "role": role})

    return {
        "token": token,
        "user": {"username": body.username, "role": role},
        "expires_in_hours": config.OMEGA_JWT_EXPIRY_HOURS,
    }


@router.post("/api/auth/refresh")
async def refresh(request: Request):
    """Refresh a valid JWT for a new one with extended expiry."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")

    claims = verify_jwt(auth_header[7:])
    if not claims:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    new_token = create_jwt({"sub": claims["sub"], "role": claims.get("role", "admin")})
    return {"token": new_token, "expires_in_hours": config.OMEGA_JWT_EXPIRY_HOURS}


@router.get("/api/auth/me")
async def me(request: Request):
    """Return the current user identity from the JWT."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")

    claims = verify_jwt(auth_header[7:])
    if not claims:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return {
        "username": claims.get("sub"),
        "role": claims.get("role", "admin"),
        "job_id": claims.get("job_id"),
    }


@router.post("/api/auth/review-link")
async def create_review_link(body: ReviewLinkRequest, request: Request):
    """Generate a shareable review link with a scoped JWT."""
    from routers import admin_required
    admin_required(request)

    reviewer_sub = body.email or "reviewer"
    token = create_jwt(
        {"sub": reviewer_sub, "role": "reviewer", "job_id": body.job_id},
        expiry_hours=config.OMEGA_JWT_REVIEW_EXPIRY_HOURS,
    )

    base_url = os.environ.get("OMEGA_PUBLIC_URL", "").strip()
    if not base_url:
        host = request.headers.get("host", "localhost:3000")
        scheme = request.headers.get("x-forwarded-proto", "http")
        base_url = f"{scheme}://{host}"

    review_url = f"{base_url}/editor/{body.job_id}?review_token={token}"

    return {
        "review_url": review_url,
        "token": token,
        "expires_in_hours": config.OMEGA_JWT_REVIEW_EXPIRY_HOURS,
    }
