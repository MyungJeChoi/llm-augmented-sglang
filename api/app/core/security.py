from __future__ import annotations

from fastapi import Header, HTTPException, status

from app.core.config import settings


def require_admin_api_key(x_api_key: str | None = Header(default=None, alias="X-API-KEY")) -> None:
    """Very small auth layer for admin endpoints.

    This is intentionally minimal for the scaffold:
    - One shared API key (configured via ADMIN_API_KEY)
    - No user database / RBAC

    In production, replace with proper authentication + authorization.
    """

    if not x_api_key or x_api_key != settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing admin API key",
        )
