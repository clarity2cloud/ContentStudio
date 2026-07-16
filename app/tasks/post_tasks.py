# app/tasks/post_tasks.py
"""
Celery tasks for scheduled post publishing.

publish_post
  - Primary task: publish one scheduled post.
  - Triggered by scheduler_service.schedule_post() via apply_async(eta=...).
  - Retries 3 × with 15-minute backoff.
  - Runs the SAME execution logic as the in-process APScheduler path
    (scheduler_service._execute_scheduled_post), so nothing changes for
    Twitter / Instagram / LinkedIn / Facebook dispatch.

check_and_publish_pending_posts
  - Beat task running every 60 s.
  - Safety net: finds posts whose scheduled_at is in the past but still has
    status="scheduled" (e.g., scheduled before Redis was available, or the
    server was down when the post was due).
  - Dispatches publish_post.delay() for any overdue posts and marks them
    "queued" to prevent double-dispatch.
"""

import asyncio
import logging

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


# ── publish_post ────────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="app.tasks.post_tasks.publish_post",
    queue="posts",
    max_retries=3,
    default_retry_delay=60 * 15,   # 15 minutes between retries
    acks_late=True,
)
def publish_post(self, scheduled_post_id: str) -> dict:
    """
    Publish a single scheduled post.

    Uses the existing scheduler_service execution engine so all platform
    dispatch logic, retry DB updates, and error handling are centralised.
    """
    try:
        # Lazy import to avoid circular imports (scheduler_service → tasks →
        # scheduler_service)
        from app.services.scheduler_service import scheduler_service as _sched
        asyncio.run(_sched._execute_scheduled_post(scheduled_post_id))
        logger.info(f"✅ [Celery] Published post {scheduled_post_id}")
        return {"status": "published", "post_id": scheduled_post_id}
    except Exception as exc:
        logger.error(
            f"❌ [Celery] publish_post failed for {scheduled_post_id}: {exc}")
        # Let Celery retry; if all retries exhausted it will raise the exception
        # which marks the task as FAILURE in the result backend.
        raise self.retry(exc=exc)


# ── check_and_publish_pending_posts (Celery Beat) ────────────────────────────

@celery_app.task(
    name="app.tasks.post_tasks.check_and_publish_pending_posts",
    queue="posts",
)
def check_and_publish_pending_posts() -> dict:
    """
    Beat safety-net: scan for posts whose scheduled_at has passed but are still
    in status='scheduled'.  This catches posts that were missed because:
      • The server was down at fire time
      • The ETA task was lost from Redis (flush, migration, etc.)
      • The post was scheduled before Celery was available

    Each discovered post gets its own publish_post task dispatched, and its
    status is immediately updated to 'queued' so subsequent Beat runs don't
    double-dispatch.
    """
    from datetime import datetime, timezone

    try:
        from app.db.appwrite_client import get_appwrite_client
        db = get_appwrite_client()
        now_iso = datetime.now(timezone.utc).isoformat()

        # Fetch posts that are overdue and still unprocessed
        result = (
            db.table("scheduled_posts")
            .select("id, scheduled_at, status")
            .eq("status", "scheduled")
            .lte("scheduled_at", now_iso)   # scheduled_at <= now
            .limit(100)
            .execute()
        )

        dispatched = 0
        for post in (result.data or []):
            post_id = post.get("id")
            if not post_id:
                continue
            try:
                # Mark as queued FIRST to prevent double-dispatch on next Beat
                # tick
                db.table("scheduled_posts").update(
                    {"status": "queued"}
                ).eq("id", post_id).execute()
                # Dispatch the actual publish task
                publish_post.apply_async(args=[post_id], queue="posts")
                dispatched += 1
            except Exception as inner:
                logger.warning(
                    f"[Beat] Could not dispatch post {post_id}: {inner}")

        if dispatched:
            logger.info(f"⏰ [Beat] Dispatched {dispatched} overdue post(s)")
        return {"dispatched": dispatched}

    except Exception as exc:
        logger.error(f"❌ [Beat] check_and_publish_pending_posts failed: {exc}")
        return {"error": str(exc)}
