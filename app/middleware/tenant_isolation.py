# app/middleware/tenant_isolation.py
"""
Tenant Isolation Helpers
═══════════════════════════════════════════════════════════════════════════════

Query-level helpers that scope database operations to a tenant/user, used by
routers that fetch or list user-owned data (brand profiles, etc.):

  - apply_tenant_filter(query, tenant_id) — append a tenant_id filter to a query
  - assert_tenant(doc, tenant_id)         — 403 if a fetched doc is cross-tenant
  - assert_owner(doc, user_id, tenant_id) — 404/403 if doc isn't owned by caller
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, status

from app.utils.logger import logger


def apply_tenant_filter(query, tenant_id: Optional[str]):
    """
    Append a `.eq("tenant_id", tenant_id)` to any AppwriteClient query builder.
    Pass-through if tenant_id is missing (single-tenant / legacy).
    """
    if not tenant_id:
        return query
    try:
        return query.eq("tenant_id", tenant_id)
    except Exception:
        # Builder doesn't support .eq — caller is using a different abstraction
        return query


def assert_tenant(doc: dict, tenant_id: Optional[str]) -> None:
    """
    Raise 403 if a single fetched doc does NOT belong to the calling tenant.
    Used by routers that fetch by id then verify ownership.
    """
    if not tenant_id:
        return  # legacy / single-tenant tokens are not blocked
    doc_tenant = doc.get("tenant_id") if isinstance(doc, dict) else None
    if doc_tenant and doc_tenant != tenant_id:
        logger.warning(
            f"[TENANT] Cross-tenant access blocked: doc tenant={doc_tenant} caller={tenant_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: resource belongs to a different tenant.",
        )


def assert_owner(
        doc: dict,
        user_id: Optional[str],
        tenant_id: Optional[str] = None) -> None:
    """
    Raise 403 if doc does not belong to (user_id) and (tenant_id, if provided).
    """
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found.")
    if user_id:
        doc_user = doc.get("user_id")
        if doc_user and doc_user != user_id:
            logger.warning(
                f"[TENANT] User mismatch blocked: doc.user={doc_user} caller={user_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied.")
    assert_tenant(doc, tenant_id)
