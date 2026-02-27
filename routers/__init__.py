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


def admin_required(request: Request):
    """FastAPI dependency that replicates Flask's @admin_required decorator."""
    remote = request.client.host if request.client else ""
    forwarded = _first_forwarded_ip(request)
    omega_env = (os.environ.get("OMEGA_ENV", "development") or "").strip().lower()
    default_local_trust = "1" if omega_env in {"dev", "development", "local"} else "0"
    trust_local = (os.environ.get(_TRUST_LOCAL_ENV, default_local_trust) or "").strip().lower() in {"1", "true", "yes", "on"}

    # Trust local direct calls by default, but do not trust proxy-forwarded remote clients.
    if trust_local and _is_loopback_ip(remote) and (not forwarded or _is_loopback_ip(forwarded)):
        return True

    configured = (os.environ.get(_ADMIN_TOKEN_ENV) or "").strip()
    if not configured:
        raise HTTPException(status_code=403, detail="Admin access required")
    provided = request.headers.get("X-Omega-Admin-Token", "")
    if not provided or not secrets.compare_digest(provided, configured):
        raise HTTPException(status_code=403, detail="Admin access required")
    return True
