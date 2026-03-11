import os
import secrets
from fastapi import Request, HTTPException


_ADMIN_TOKEN_ENV = "OMEGA_ADMIN_TOKEN"
_TRUST_LOCAL_ENV = "OMEGA_TRUST_LOCAL_ADMIN"
_LOOPBACKS = {"127.0.0.1", "::1", "localhost"}


def _is_loopback_ip(value: str) -> bool:
    raw = (value or "").strip().lower()
    if not raw:
        return False
    # Handle accidental port attachment (e.g. "127.0.0.1:12345")
    if ":" in raw and raw.count(":") == 1 and "." in raw:
        raw = raw.split(":", 1)[0]
    return raw in _LOOPBACKS


def _first_forwarded_ip(request: Request) -> str:
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if not xff:
        return ""
    return xff.split(",", 1)[0].strip()


def _extract_jwt_claims(request: Request) -> dict | None:
    """Extract and verify JWT claims from Authorization header or query param."""
    token = None

    # 1. Check Authorization: Bearer <token>
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]

    # 2. Check review_token query param (for review links)
    if not token:
        token = request.query_params.get("review_token")

    if not token:
        return None

    from routers.auth import verify_jwt
    return verify_jwt(token)


def admin_required(request: Request):
    """FastAPI dependency: allows admin-level access via JWT, static token, or localhost trust."""
    # --- JWT Bearer token (highest priority) ---
    claims = _extract_jwt_claims(request)
    if claims and claims.get("role") == "admin":
        request.state.user = claims
        return True

    # --- Localhost trust (dev mode) ---
    remote = request.client.host if request.client else ""
    forwarded = _first_forwarded_ip(request)
    omega_env = (os.environ.get("OMEGA_ENV", "development") or "").strip().lower()
    default_local_trust = "1" if omega_env in {"dev", "development", "local"} else "0"
    trust_local = (os.environ.get(_TRUST_LOCAL_ENV, default_local_trust) or "").strip().lower() in {"1", "true", "yes", "on"}

    if trust_local and _is_loopback_ip(remote) and (not forwarded or _is_loopback_ip(forwarded)):
        request.state.user = {"sub": "local", "role": "admin"}
        return True

    # --- Static admin token (legacy) ---
    configured = (os.environ.get(_ADMIN_TOKEN_ENV) or "").strip()
    if configured:
        provided = request.headers.get("X-Omega-Admin-Token", "")
        if provided and secrets.compare_digest(provided, configured):
            request.state.user = {"sub": "token", "role": "admin"}
            return True

    raise HTTPException(status_code=401, detail="Authentication required")


def reviewer_or_admin(request: Request):
    """FastAPI dependency: allows reviewer OR admin access via JWT."""
    claims = _extract_jwt_claims(request)
    if claims and claims.get("role") in ("admin", "reviewer"):
        request.state.user = claims
        return True

    # Fall through to admin_required for localhost trust / static token
    return admin_required(request)
