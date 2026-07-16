"""
Simplified dependencies for open-source development.
No authentication required - all endpoints work with demo user credentials.
"""

from typing import Optional
from fastapi import Depends
from app.core.database import get_db
from app.db.appwrite_client import AppwriteClient


# Demo user — all requests use this
_DEMO_USER_ID = "demo-user"
_DEMO_TENANT_ID = "demo-tenant"


async def get_current_user_id() -> str:
    """Return demo user ID for open-source development."""
    return _DEMO_USER_ID


async def get_current_tenant_id() -> str:
    """Return demo tenant ID for open-source development."""
    return _DEMO_TENANT_ID


async def get_current_user_id_optional() -> Optional[str]:
    """Return demo user ID (always present in open-source)."""
    return _DEMO_USER_ID


async def get_current_tenant_id_optional() -> Optional[str]:
    """Return demo tenant ID (always present in open-source)."""
    return _DEMO_TENANT_ID


async def require_admin() -> str:
    """Return demo user ID (all users are admins in open-source)."""
    return _DEMO_USER_ID


async def get_current_user(
    user_id: str = Depends(get_current_user_id),
    db: AppwriteClient = Depends(get_db),
) -> dict:
    """Return minimal user profile for open-source."""
    return {
        "id": user_id,
        "tenant_id": _DEMO_TENANT_ID,
        "email": "demo@opensource.local",
        "role": "admin",
    }
