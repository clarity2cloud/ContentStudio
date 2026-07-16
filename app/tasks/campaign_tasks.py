"""
Background tasks for campaign generation.

Campaign content generation is dispatched to Celery queue 'ai' when campaign size
exceeds threshold (3+ items). This decouples campaign generation from HTTP request,
preventing timeouts and resource starvation when multiple campaigns are generated
concurrently.

Each task:
- Runs in a background worker (separate process)
- Calls campaign_pipeline.generate_campaign()
- Saves results to campaign_content + content tables
- Deducts credits from user's tenant
- Can be polled via GET /api/v1/tasks/{task_id}
"""

import asyncio
import time as _time
from typing import Optional, List

from app.celery_app import celery_app
from app.services.campaign_pipeline import campaign_pipeline
from app.utils.logger import logger


# Helper to run async code in both sync and async contexts
def _run_async(coro):
    """Run a coroutine safely whether or not an event loop is already running."""
    try:
        # Try to get the running event loop
        _loop = asyncio.get_running_loop()
    except RuntimeError:
        # No event loop running, use asyncio.run()
        return asyncio.run(coro)
    else:
        # Event loop is running, use run_until_complete() or create a task
        # Since we can't use run_until_complete() on a running loop,
        # we need to schedule it as a task and wait
        import concurrent.futures

        # Create a new thread to run the coroutine
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()


# 5-min timeout for large campaigns
@celery_app.task(bind=True, queue="ai", time_limit=300)
def generate_campaign_background(
    self,
    campaign_id: str,
    platforms: List[str],
    duration_days: int,
    objective: str,
    audience: str,
    cta: str,
    brand_context: str,
    user_context: Optional[str],
    tenant_id: str,
    user_id: str,
    brand_id: Optional[str] = None,
    tone: Optional[str] = None,
) -> dict:
    """
    Background task for campaign content generation via intelligent pipeline.

    Runs asynchronously in a Celery worker. Handles campaign pipeline execution,
    credit deduction, and result tracking.

    Args:
        campaign_id: ID of campaign
        platforms: List of channels (e.g., ['blog', 'twitter', 'linkedin', ...])
        duration_days: Number of days for daily mode
        objective: Campaign objective/topic
        audience: Target audience description
        cta: Call-to-action
        brand_context: Brand information
        user_context: User/budget context
        tenant_id: Tenant ID
        user_id: User ID
        brand_id: Optional brand ID
        tone: Optional tone

    Returns:
        dict: {
            "success": bool,
            "campaign_id": str,
            "job_id": str | None,
            "total_items": int,
            "error": str | None,
        }
    """
    try:
        t0 = _time.monotonic()

        logger.info(
            f"[CAMPAIGN_BG] Campaign generation started: campaign_id={campaign_id}, "
            f"duration_days={duration_days}, platforms={len(platforms)}, "
            f"total_items={(duration_days * len(platforms))}")

        # Call campaign pipeline (async, wrapped with helper)
        pipeline_result = _run_async(
            campaign_pipeline.generate_campaign(
                platforms=platforms,
                duration_days=duration_days,
                objective=objective,
                audience=audience,
                cta=cta,
                brand_context=brand_context,
                user_context=user_context,
                tenant_id=tenant_id,
                user_id=user_id,
                campaign_id=campaign_id,
                brand_id=brand_id or "",
                tone=tone or "professional",
            )
        )

        job_id = pipeline_result.get("job_id")
        if not job_id:
            logger.error("[CAMPAIGN_BG] Pipeline returned no job_id")
            return {
                "success": False,
                "campaign_id": campaign_id,
                "job_id": None,
                "total_items": duration_days * len(platforms),
                "error": "Pipeline returned no job_id",
            }

        total_items = duration_days * len(platforms)

        logger.info(
            "[CAMPAIGN_BG] Campaign generation queued successfully: "
            f"job_id={job_id}, duration={((_time.monotonic() - t0) * 1000):.1f}ms")

        return {
            "success": True,
            "campaign_id": campaign_id,
            "job_id": job_id,
            "total_items": total_items,
            "error": None,
        }

    except Exception as exc:
        logger.exception(
            f"[CAMPAIGN_BG] Campaign generation task failed: {exc}")
        return {
            "success": False,
            "campaign_id": campaign_id,
            "job_id": None,
            "total_items": duration_days * len(platforms),
            "error": f"Background task failed: {str(exc)}",
        }
