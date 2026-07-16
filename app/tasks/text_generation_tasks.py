"""
Background tasks for bulk text generation.

Text generation for multiple platforms or bulk operations is dispatched to Celery
queue 'ai' when background=true. This prevents long-running LLM calls from blocking
HTTP requests and allows concurrent text generation without starving the NVIDIA API.

Each task:
- Runs in a background worker (separate process)
- Calls ai_service generation methods
- Saves results to content library
- Deducts credits from user's tenant
- Can be polled via GET /api/v1/tasks/{task_id}
"""

import asyncio
import concurrent.futures
import time as _time
from typing import Optional, List

from app.celery_app import celery_app
from app.services.ai_service import ai_service
from app.db.appwrite_client import get_appwrite_client
from app.utils.logger import logger


# Helper to run async code in both sync and async contexts
def _run_async(coro):
    """Run a coroutine safely whether or not an event loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    else:
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()


@celery_app.task(bind=True, queue="ai", time_limit=120)
def generate_multi_platform_background(
    self,
    platforms: List[str],
    topic: Optional[str],
    include_hashtags: bool,
    include_emojis: bool,
    brand_context: str,
    user_context: Optional[str],
    custom_instructions: Optional[str],
    tenant_id: str,
    user_id: str,
    brand_id: Optional[str] = None,
    effective_tone: str = "casual",
    save: bool = True,
) -> dict:
    """
    Background task for generating content across multiple platforms.

    Runs asynchronously in a Celery worker. Handles multi-platform generation,
    content library save, and credit deduction.

    Args:
        platforms: List of platform names (e.g., ['twitter', 'linkedin', 'instagram'])
        topic: Topic/theme for content
        include_hashtags: Whether to include hashtags
        include_emojis: Whether to include emojis
        brand_context: Brand information/context
        user_context: User/budget context
        custom_instructions: Additional instructions for generation
        tenant_id: Tenant ID
        user_id: User ID
        brand_id: Optional brand ID
        effective_tone: Tone for content (default: "casual")
        save: Whether to save results to content library

    Returns:
        dict: {
            "success": bool,
            "results": List[dict],  # Platform-specific content
            "content_ids": dict,    # Platform -> content_id mappings
            "saved": bool,
            "error": str | None,
        }
    """
    try:
        t0 = _time.monotonic()

        logger.info(
            f"[TEXT_GEN_BG] Multi-platform generation started: user={user_id[:8]}, "
            f"platforms={len(platforms)}, topic={topic[:40] if topic else 'N/A'}")

        # Generate content across all platforms (async, wrapped with helper)
        result = _run_async(
            ai_service.generate_multi_platform(
                platforms=platforms,
                topic=topic,
                tone=effective_tone,
                include_hashtags=include_hashtags,
                include_emojis=include_emojis,
                custom_instructions=custom_instructions,
                brand_context=brand_context,
                user_context=user_context,
                brand_id=brand_id,
                tenant_id=tenant_id,
                user_id=user_id,
            )
        )

        # Save results to content library if requested
        content_ids: dict = {}
        if save:
            from app.services.content_helpers import save_content as _save_content

            CONTENT_TYPE_ALIASES = {
                "instagram": "instagram_caption",
                "facebook": "facebook_post",
                "twitter": "tweet",
                "linkedin": "linkedin_post",
            }

            try:
                db = get_appwrite_client()
                for item in (result.get("results") or []):
                    if isinstance(item, dict) and not item.get("error"):
                        platform = item.get("platform", "unknown")
                        text = item.get("content", "")
                        content_type = CONTENT_TYPE_ALIASES.get(
                            platform, platform)
                        cid = _save_content(
                            db,
                            user_id,
                            tenant_id,
                            brand_id,
                            title=f"{platform.title()}: {(topic or 'Multi-platform')[:50]}",
                            content=text,
                            content_type=content_type,
                            metadata=item.get("metadata", {"platform": platform}),
                            platform=platform,
                        )
                        if cid:
                            content_ids[platform] = cid
            except Exception as exc:
                logger.warning(
                    f"[TEXT_GEN_BG] Failed to save content to library (non-fatal): {exc}"
                )

        logger.info(
            "[TEXT_GEN_BG] Multi-platform generation completed: "
            f"duration={((_time.monotonic() - t0) * 1000):.1f}ms"
        )

        return {
            "success": True,
            **result,
            "saved": save,
            "content_ids": content_ids,
            "error": None,
        }

    except Exception as exc:
        logger.exception(
            f"[TEXT_GEN_BG] Multi-platform generation task failed: {exc}")
        return {
            "success": False,
            "results": [],
            "content_ids": {},
            "saved": False,
            "error": f"Background task failed: {str(exc)}",
        }


@celery_app.task(bind=True, queue="ai", time_limit=180)
def fill_week_background(
    self,
    tenant_id: str,
    user_id: str,
    topic: Optional[str],
    platforms: List[str],
    brand_id: Optional[str] = None,
    count_per_day: int = 1,
    user_context: Optional[str] = None,
) -> dict:
    """
    Background task for generating and scheduling a full week of content.

    Runs asynchronously in a Celery worker. Handles week-long content generation
    and automatic scheduling.

    Args:
        tenant_id: Tenant ID
        user_id: User ID
        topic: Content topic
        platforms: List of platforms
        brand_id: Optional brand ID
        count_per_day: Number of posts per day (default: 1)
        user_context: User/budget context

    Returns:
        dict: {
            "success": bool,
            "scheduled_count": int,
            "error": str | None,
        }
    """
    try:
        _t0 = _time.monotonic()

        logger.warning(
            "[WEEK_GEN_BG] fill_week_background is a STUB — no content is generated or scheduled. "
            "Implement this task before enabling it in production. "
            f"user={user_id[:8]}, platforms={len(platforms)}")

        return {
            "success": False,
            "scheduled_count": 0,
            "error": "Not implemented — fill_week_background is a stub task.",
        }

    except Exception as exc:
        logger.exception(f"[WEEK_GEN_BG] Week generation task failed: {exc}")
        return {
            "success": False,
            "scheduled_count": 0,
            "error": f"Background task failed: {str(exc)}",
        }


@celery_app.task(bind=True, queue="ai", time_limit=120)
def queue_fill_background(
    self,
    tenant_id: str,
    user_id: str,
    platform: str,
    count: int,
    topic: Optional[str] = None,
    brand_id: Optional[str] = None,
    user_context: Optional[str] = None,
) -> dict:
    """
    Background task for filling queue slots with generated content.

    Runs asynchronously in a Celery worker. Generates content and adds it to
    scheduling queue without blocking.

    Args:
        tenant_id: Tenant ID
        user_id: User ID
        platform: Platform name
        count: Number of items to generate
        topic: Optional topic/theme
        brand_id: Optional brand ID
        user_context: User/budget context

    Returns:
        dict: {
            "success": bool,
            "filled_count": int,
            "error": str | None,
        }
    """
    try:
        _t0 = _time.monotonic()

        logger.warning(
            "[QUEUE_FILL_BG] queue_fill_background is a STUB — no content is generated or queued. "
            "Implement this task before enabling it in production. "
            f"user={user_id[:8]}, platform={platform}, count={count}")

        return {
            "success": False,
            "filled_count": 0,
            "error": "Not implemented — queue_fill_background is a stub task.",
        }

    except Exception as exc:
        logger.exception(f"[QUEUE_FILL_BG] Queue fill task failed: {exc}")
        return {
            "success": False,
            "filled_count": 0,
            "error": f"Background task failed: {str(exc)}",
        }
