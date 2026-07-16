# app/tasks/ai_tasks.py
"""
Celery tasks for heavy AI generation workloads.

generate_multi_platform_background
  - Runs multi-platform generation (up to 23 platforms × 30-120 s each) in a
    Celery worker so the HTTP request returns a task_id immediately.
  - The FastAPI endpoint /ai/generate/multi-platform already works synchronously
    (asyncio.gather across platforms) — this task is used when the optional
    `background=true` query param is passed.
  - Results are stored in the Redis result backend for 2 hours.
  - Clients poll GET /api/v1/tasks/{task_id} to retrieve the result.
"""

import asyncio
import logging
from typing import List, Optional

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="app.tasks.ai_tasks.generate_multi_platform_background",
    queue="ai",
    max_retries=2,
    default_retry_delay=30,
    time_limit=660,       # 11-minute hard kill (23 platforms × ~28 s avg)
    soft_time_limit=600,  # Raise SoftTimeLimitExceeded at 10 min → retry
    acks_late=True,
)
def generate_multi_platform_background(
    self,
    platforms: List[str],
    topic: Optional[str],
    tone: str,
    include_hashtags: bool,
    include_emojis: bool,
    brand_context: str,
    custom_instructions: Optional[str],
    user_context: Optional[str],
    brand_id: Optional[str],
    tenant_id: str,
    user_id: str,
) -> dict:
    """
    Background multi-platform content generation.

    Returns the same structure as the synchronous endpoint:
      {
        "results": [...],
        "platforms_requested": [...],
        "platforms_succeeded": int,
        "platforms_failed": int,
        "status": "success"
      }

    The result is stored in Redis and accessible via the task_id.
    """
    try:
        from app.services.ai_service import ai_service

        result = asyncio.run(
            ai_service.generate_multi_platform(
                platforms=platforms,
                topic=topic,
                tone=tone,
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
        logger.info(
            "✅ [Celery] multi-platform done: "
            f"{result.get('platforms_succeeded', 0)}/{len(platforms)} platforms")
        return {"status": "success", **result}

    except Exception as exc:
        logger.error(
            f"❌ [Celery] generate_multi_platform_background failed: {exc}")
        raise self.retry(exc=exc)
