# app/services/content_helpers.py
"""
Shared content-library helpers — safe to import from both API routers AND Celery tasks.

Extracted from app.api.v1.ai_generation so that Celery task modules no longer
depend on HTTP route files (tight coupling that makes the worker fragile).

Public API
----------
resolve_brand(db, brand_id)          → str   brand context block (cached 1h)
resolve_default_brand_id(db, user_id)→ str|None
save_content(db, user_id, ...)       → str|None  content_id
"""

from datetime import datetime, timezone
from typing import Optional

from app.db.appwrite_client import AppwriteClient
from app.services import brand_validator as _bv
from app.services.cache_service import cache, brand_context_key
from app.utils.logger import logger


# ── Brand resolution ────────────────────────────────────────────────────

def resolve_default_brand_id(
        db: AppwriteClient,
        user_id: str) -> Optional[str]:
    """Return the user's default brand_id, if any. Cheap, cached helper."""
    if not user_id:
        return None
    try:
        from app.services.cache_service import default_brand_key
        cached = cache.get(default_brand_key(user_id))
        if cached:
            return cached if isinstance(cached, str) else None
        res = (
            db.table("brand_profiles")
            .select("id")
            .eq("user_id", user_id)
            .eq("is_default", True)
            .limit(1)
            .execute()
        )
        if res.data:
            bid = res.data[0].get("id")
            if bid:
                from app.services.cache_service import default_brand_key as _dbk
                cache.set(_dbk(user_id), bid, ttl=3600)
                return bid
    except Exception:
        pass
    return None


def resolve_brand(db: AppwriteClient, brand_id: Optional[str]) -> str:
    """
    Resolve brand_id to a rich brand context block.

    Uses brand_validator.build_brand_block; caches for 1 hour to cut DB load.
    Returns "" when brand_id is None or the brand cannot be found.
    """
    if not brand_id:
        return ""

    cached = cache.get(brand_context_key(brand_id))
    if isinstance(cached, str) and cached:
        return cached

    try:
        res = db.table("brand_profiles").select(
            "*").eq("id", brand_id).execute()
        if not res.data:
            return ""
        b = res.data[0]
    except Exception:
        return ""

    block = _bv.build_brand_block(b)
    if block:
        cache.set(brand_context_key(brand_id), block, ttl=3600)
    return block


# ── Content library save ────────────────────────────────────────────────

def save_content(
    db: AppwriteClient,
    user_id: str,
    tenant_id: str,
    brand_id: Optional[str],
    title: str,
    content: str,
    content_type: str,
    metadata: dict,
    platform: Optional[str] = None,
) -> Optional[str]:
    """
    Save generated content to the content library.

    Always writes:
      • updated_at  — ISO timestamp (keeps column in sync with $updatedAt)
      • platform    — the channel name, parallel to content_type
      • brand_id    — falls back to the user's default brand if not supplied
      • quality_score — extracted from metadata.validation when present

    Returns the new content_id or None on failure.
    """
    try:
        effective_brand_id = brand_id or resolve_default_brand_id(db, user_id)

        plat = (platform or content_type or "").strip().lower()
        if plat in ("", "content", "post"):
            plat = (content_type or "").strip().lower()

        now_iso = datetime.now(timezone.utc).isoformat()
        payload = {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "title": title,
            "content": content,
            "content_type": content_type,
            "status": "draft",
            "metadata": metadata,
            "updated_at": now_iso,
            "platform": plat,
        }
        if effective_brand_id:
            payload["brand_id"] = effective_brand_id

        if isinstance(metadata, dict) and "validation" in metadata:
            validation = metadata.get("validation", {})
            if isinstance(validation, dict):
                payload["quality_score"] = validation.get(
                    "overall_quality_score", "fair")

        saved = db.table("content").insert(payload).execute()
        return saved.data[0]["id"] if saved.data else None

    except Exception as e:
        import traceback as _tb
        logger.error(
            f"[save_content] FAILED for user={user_id} type={content_type} "
            f"title={title!r}\n  error={type(e).__name__}: {e}\n"
            + _tb.format_exc()
        )
        return None
