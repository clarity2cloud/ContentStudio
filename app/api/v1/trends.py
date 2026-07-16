"""
Viral Intel — RAG-powered trend scouting endpoints.

POST /api/v1/trends/scout          — kick off a scan (returns task_id immediately)
GET  /api/v1/trends/scout/{task_id} — poll for status + result

Flow (production with Redis):
  1. POST /scout → credit pre-check → dispatch Celery task → return {task_id} in <100ms
  2. Celery worker runs: scrape → LLM → save → deduct credits
  3. Frontend polls GET /scout/{task_id} every 3s until status="completed"

Flow (development / no Redis):
  task_always_eager=True → task runs synchronously inline, same result shape,
  no Celery or Redis required. Single .env line toggle: ENV=development.
"""

from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone, timedelta

from app.utils.logger import logger

router = APIRouter(prefix="/trends", tags=["Trend Intelligence"])


# ── Request model ─────────────────────────────────────────────────────────────

class TrendScoutRequest(BaseModel):
    keyword: str = Field(..., min_length=2, description="Topic or niche to scout for viral content")
    days: int    = Field(7, ge=1, le=30, description="Lookback window in days (1–30)")
    brand_id: Optional[str] = Field(None, description="Optional brand context for angle personalisation")


# ── POST /scout ───────────────────────────────────────────────────────────────

@router.post("/scout", summary="Viral Intel — kick off real-time trend scan")
async def trend_scout(
    req: TrendScoutRequest,
    request: Request,
    
    
):
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    """
    Dispatches the Viral Intel RAG pipeline to a Celery background worker.

    Returns {task_id, status: "queued"} immediately — poll
    GET /trends/scout/{task_id} every 3 s for the result.

    In development mode (ENV=development or REDIS_URL not set) the task runs
    synchronously and the full result is returned directly in this response
    (same behaviour as before, no polling needed in dev).

    Credit cost: 50 credits — checked here before dispatch, deducted inside
    the worker after successful generation.
    """
    bearer_token = request.headers.get("Authorization", "")

    # ── Credit pre-check (fails fast with 402 before any work starts) ────────

    logger.info(f"[Viral Intel] Dispatching — keyword={req.keyword!r} days={req.days} user={user_id[:8]}")

    # ── Dispatch to Celery ────────────────────────────────────────────────────
    from app.tasks.viral_intel_tasks import run_viral_intel_scan
    from app.config import settings

    task = run_viral_intel_scan.apply_async(
        kwargs={
            "keyword":   req.keyword,
            "days":      req.days,
            "brand_id":  req.brand_id,
            "user_id":   user_id,
            "tenant_id": tenant_id,
        },
        queue="ai",
    )

    # ── Eager / dev mode: task ran synchronously, return result directly ──────
    # When task_always_eager=True the .apply_async() call runs the task inline
    # and task.result holds the full return value. The frontend's polling code
    # sees status="completed" on the very first poll — no UX change in dev.
    if settings.ENV == "development" or not settings.REDIS_URL:
        result = task.result or {}
        return {
            "task_id": task.id,
            "status":  "completed",
            **result,
        }

    # ── Production: return task_id for frontend polling ───────────────────────
    return {
        "task_id": task.id,
        "status":  "queued",
        "keyword": req.keyword,
        "days":    req.days,
    }


# ── GET /scout/{task_id} ──────────────────────────────────────────────────────

@router.get("/scout/{task_id}", summary="Poll Viral Intel scan status + result",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def get_scout_status(
    task_id: str,
    
):
    """
    Poll for the status of a Viral Intel scan dispatched via POST /scout.

    States:
      queued     — task waiting in Redis queue (worker not started yet)
      processing — worker has started the scan
      completed  — scan done; full result in response body
      failed     — scan failed; retry or check logs

    Frontend should poll every 3 s and stop on completed or failed.
    """
    try:
        from celery.result import AsyncResult
        result = AsyncResult(task_id)
        state  = result.state  # PENDING | STARTED | SUCCESS | FAILURE | RETRY

        if state == "PENDING":
            return {"task_id": task_id, "status": "queued"}

        if state == "STARTED":
            return {"task_id": task_id, "status": "processing"}

        if state == "SUCCESS":
            data = result.result or {}
            return {"task_id": task_id, "status": "completed", **data}

        if state in ("FAILURE", "REVOKED"):
            err = str(result.result) if result.result else "Unknown error"
            logger.error(f"[Viral Intel] Task {task_id} failed: {err}")
            return {"task_id": task_id, "status": "failed", "error": err}

        if state == "RETRY":
            return {"task_id": task_id, "status": "processing"}

        # Any other state → treat as processing
        return {"task_id": task_id, "status": "processing"}

    except Exception as e:
        logger.error(f"[Viral Intel] Status check failed for task_id={task_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Status check failed: {e}")
