"""
Audit logging — writes security/compliance events to the dedicated, append-only
`audit_logs` collection (separate from `usage_logs`, which is credit metering).

If the dedicated collection is not yet provisioned, it transparently falls back
to `usage_logs` so auditing never silently stops during a rollout.

Usage:
    from app.utils.audit import audit_log
    await audit_log(db, user_id, "login", request=request)
    await audit_log(db, user_id, "brand.create", resource_id=brand_id, details={"name": name})
    await audit_log(db, user_id, "social.publish", resource_id=post_id,
                    tenant_id=tenant_id, details={"platforms": ["x", "linkedin"]}, request=request)
"""

import json
from typing import Optional, Any, Dict
from datetime import datetime, timezone

from app.utils.logger import logger

AUDIT_COLLECTION = "audit_logs"


def _client_ip(request) -> str:
    if request is None:
        return ""
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return str(request.client.host if request.client else "")


async def audit_log(
    db,
    user_id: str,
    action: str,
    resource_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    request=None,
    tenant_id: Optional[str] = None,
) -> None:
    """
    Write a non-blocking audit event.

    Primary target: the dedicated append-only `audit_logs` collection.
    Fallback: `usage_logs` (preserves auditing if `audit_logs` isn't provisioned).

    Never raises — audit failures must not break the calling request.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    ip = _client_ip(request)
    method = request.method if request is not None else ""
    path = str(request.url.path) if request is not None else ""

    # ── Primary: dedicated audit_logs collection (structured columns) ───────
    try:
        record: Dict[str, Any] = {
            "user_id": user_id or "",
            "action": action,
            "created_at": now_iso,
        }
        if tenant_id:
            record["tenant_id"] = tenant_id
        if resource_id:
            record["resource_id"] = resource_id
        if ip:
            record["ip"] = ip
        if method:
            record["method"] = method
        if path:
            record["path"] = path
        if details:
            # store the structured payload as JSON (column is 4000 chars)
            blob = json.dumps(details, default=str)
            record["details"] = blob[:4000]

        db.table(AUDIT_COLLECTION).insert(record).execute()
        return
    except Exception as e:
        # Collection may not be provisioned yet — fall through to usage_logs.
        logger.debug(
            f"[AUDIT] dedicated audit_logs write failed, falling back: {e}")

    # ── Fallback: legacy usage_logs (keeps auditing alive during rollout) ───
    try:
        metadata: Dict[str, Any] = dict(details or {})
        if resource_id:
            metadata["resource_id"] = resource_id
        if tenant_id:
            metadata["tenant_id"] = tenant_id
        if ip:
            metadata["ip"] = ip
        if path:
            metadata["path"] = path
        if method:
            metadata["method"] = method
        metadata["timestamp"] = now_iso

        db.table("usage_logs").insert({
            "user_id": user_id,
            "feature": action,
            "metadata": metadata,
        }).execute()
    except Exception as e:
        logger.warning(
            f"Audit log failed (non-fatal): action={action} user={user_id} err={e}")
