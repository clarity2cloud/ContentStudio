# app/api/v1/scheduling.py
#
# Hootsuite / Buffer-style post scheduler
#
# ── Core scheduling ──────────────────────────────────────────
#   POST   /scheduler/posts                  Create & schedule a post
#   GET    /scheduler/posts                  List scheduled posts (filters)
#   GET    /scheduler/posts/{id}             Get one scheduled post
#   PUT    /scheduler/posts/{id}             Update / reschedule
#   DELETE /scheduler/posts/{id}             Cancel & remove
#   POST   /scheduler/posts/{id}/publish-now Publish immediately
#
# ── Queue management (Buffer-style recurring slots) ──────────
#   GET    /scheduler/queue                  List queue slots
#   POST   /scheduler/queue                  Add a recurring slot
#   DELETE /scheduler/queue/{id}             Remove a slot
#   POST   /scheduler/queue/fill             AI fills empty slots
#
# ── Bulk operations ──────────────────────────────────────────
#   POST   /scheduler/bulk                   Bulk schedule posts
#
# ── AI features ──────────────────────────────────────────────
#   POST   /scheduler/ai/generate            Generate + schedule one post
#   POST   /scheduler/ai/fill-week           AI generates full week
#   POST   /scheduler/ai/optimize            Rewrite for target platform
#   POST   /scheduler/ai/hashtags            Suggest hashtags
#
# ── Best times (Hootsuite-style) ─────────────────────────────
#   GET    /scheduler/best-times/{platform}  AI best posting times
#   POST   /scheduler/best-times/analyze     Refresh recommendations
#
# ── Views & analytics ────────────────────────────────────────
#   GET    /scheduler/calendar               Posts grouped by date
#   GET    /scheduler/analytics              Stats by status & platform
#   GET    /scheduler/options                Platform / status enums

from fastapi import APIRouter, HTTPException, Depends, Query, Body, Request
from typing import List, Optional
from datetime import datetime, timezone, timedelta
import json as _json

from app.models.scheduled_post import (
    SchedulePostRequest, UpdateScheduleRequest,
    BulkScheduleRequest, QueueSlotCreate,
    AIGenerateScheduleRequest, AIFillWeekRequest,
    QueueFillRequest, OptimizeContentRequest, HashtagRequest,
    ScheduledPostResponse, QueueSlotResponse,
    BestTimesResponse, BestTimeSlot,
    ScheduleStatus, SchedulePlatform, MediaType, DayOfWeek,
)
from app.services.scheduler_service import scheduler_service
from app.services.ai_service import ai_service
import time as _time
from app.services.credits_service import get_credit_cost
from app.core.database import get_db
from app.db.appwrite_client import AppwriteClient
from app.utils.logger import logger
from app.utils.audit import audit_log
from app.tasks.text_generation_tasks import fill_week_background


router = APIRouter(prefix="/scheduler", tags=["Scheduling"])

_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ══════════════════════════════════════════════════════════════════════════════
# HELPER: resolve brand context from brand_id
# ══════════════════════════════════════════════════════════════════════════════

def _brand_ctx(db: AppwriteClient, brand_id: Optional[str], user_id: str) -> str:
    if not brand_id:
        # Try default brand
        res = db.table("brand_profiles").select("*").eq("user_id", user_id).eq("is_default", True).execute()
        if not res.data:
            return ""
        brand_id = res.data[0]["id"]
    try:
        res = db.table("brand_profiles").select("*").eq("id", brand_id).execute()
        if not res.data:
            return ""
        b = res.data[0]
        parts = []
        if b.get("name"):            parts.append(f"Brand: {b['name']}")
        if b.get("industry"):        parts.append(f"Industry: {b['industry']}")
        if b.get("tone"):            parts.append(f"Tone: {b['tone']}")
        if b.get("voice"):           parts.append(f"Voice: {b['voice']}")
        if b.get("target_audience"): parts.append(f"Audience: {b['target_audience']}")
        if b.get("vocabulary"):      parts.append(f"Approved words: {', '.join(b['vocabulary'])}")
        if b.get("avoid_words"):     parts.append(f"Avoid: {', '.join(b['avoid_words'])}")
        return "\n".join(parts)
    except Exception:
        return ""


def _platform_content_hint(platform: str) -> str:
    hints = {
        "twitter":   "Under 280 characters. Hook in the first 8 words. Punchy.",
        "instagram": "Visual caption. Short punchy first line. 5-10 hashtags at the end.",
        "linkedin":  "Professional. 150-250 words. Hook → insight → CTA. 3-5 hashtags.",
        "facebook":  "Conversational. 50-150 words. Storytelling tone. 1-2 hashtags.",
        "tiktok":    "Short, punchy, trending. Match Gen Z energy. 3-5 hashtags.",
        "youtube":   "Engaging description (100-200 words) + relevant keywords.",
        "pinterest": "Inspirational and keyword-rich. 50-100 words.",
        "threads":   "Casual and authentic. Under 500 characters. No hashtags needed.",
    }
    return hints.get(platform, "Write engaging platform-native content.")


# ══════════════════════════════════════════════════════════════════════════════
# CORE SCHEDULING CRUD
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/posts", summary="Schedule a post",
    responses={
                400: {"description": "Bad request"},
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def schedule_post(
    req: SchedulePostRequest,
    request: Request,
    db: AppwriteClient    = Depends(get_db),
):
    """
    Schedule a post for any connected platform.

    - Provide **content_id** to schedule existing content from the library, OR
    - Provide **content_text** to write inline.
    - If no social account is connected for the platform, post is saved as **draft**.
    """
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    bearer = request.headers.get("Authorization", "") or None
    t0 = _time.monotonic()
    # Resolve content — always persist to Appwrite content table
    content_text = req.content_text
    content_id   = req.content_id

    if content_id and not content_text:
        # Load text from existing content item
        cnt = db.table("content").select("content").eq("id", content_id).eq("user_id", user_id).execute()
        if not cnt.data:
            raise HTTPException(status_code=404, detail="Content item not found")
        content_text = cnt.data[0].get("content", "")
    elif content_text and not content_id:
        # Save inline content to library so it appears in history
        saved = db.table("content").insert({
            "user_id":      user_id,
            "tenant_id":    tenant_id,
            "title":        req.title or f"{req.platform.value.title()} post",
            "content":      content_text,
            "content_type": req.platform.value,
            "status":       "scheduled",
            "campaign_id":  req.campaign_id,
            "brand_id":     req.brand_id,
            "metadata":     {"source": "scheduler", "platform": req.platform.value},
        }).execute()
        content_id = saved.data[0]["id"] if saved.data else None

    if not content_text:
        raise HTTPException(status_code=400, detail="No content text found — provide content_id or content_text")

    # Check for connected social account
    acc_res = (
        db.table("social_accounts")
        .select("id")
        .eq("user_id", user_id)
        .eq("platform", req.platform.value)
        .eq("is_active", True)
        .execute()
    )
    has_account = bool(acc_res.data)
    status      = ScheduleStatus.SCHEDULED if has_account else ScheduleStatus.DRAFT

    data = {
        "user_id":              user_id,
        "platform":             req.platform.value,
        "content_text":         content_text,
        "title":                req.title or f"{req.platform.value.title()} post",
        "media_urls":           req.media_urls or [],
        "media_type":           req.media_type.value if req.media_type else None,
        "hashtags":             req.hashtags or [],
        "scheduled_at":         req.scheduled_at.isoformat(),
        "timezone":             req.timezone,
        "status":               status.value,
        "campaign_id":          req.campaign_id,
        "brand_id":             req.brand_id,
        "content_id":           content_id,
        "connected_account_id": req.connected_account_id or (acc_res.data[0]["id"] if acc_res.data else None),
        "retry_count":          0,
        "max_retries":          req.max_retries,
    }

    result = db.table("scheduled_posts").insert(data).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create scheduled post")

    post = result.data[0]

    # Queue in APScheduler
    if status == ScheduleStatus.SCHEDULED:
        await scheduler_service.schedule_post(post["id"], req.scheduled_at)
        if req.content_id:
            db.table("content").update({"status": "scheduled"}).eq("id", req.content_id).execute()

    logger.info(f"📅 Post scheduled [{status.value}] → {req.scheduled_at} on {req.platform.value}")
    return {**post, "queued": status == ScheduleStatus.SCHEDULED}


@router.get("/posts", summary="List scheduled posts")
async def list_scheduled_posts(
    status:    Optional[str] = Query(None, description="Filter by status"),
    platform:  Optional[str] = Query(None, description="Filter by platform"),
    from_date: Optional[str] = Query(None, description="ISO date — posts on/after this date"),
    to_date:   Optional[str] = Query(None, description="ISO date — posts on/before this date"),
    limit:     int           = Query(50, ge=1, le=200),
    offset:    int           = Query(0, ge=0),
    db:        AppwriteClient = Depends(get_db),
):
    """List all scheduled posts — supports filtering by status, platform, and date range."""
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    q = db.table("scheduled_posts").select("*").eq("user_id", user_id)
    if status:
        q = q.eq("status", status)
    if platform:
        q = q.eq("platform", platform)
    if from_date:
        q = q.gte("scheduled_at", from_date)
    if to_date:
        q = q.lte("scheduled_at", to_date)
    q = q.order("scheduled_at", desc=False).limit(limit)
    result = q.execute()
    return {"posts": result.data or [], "total": result.count, "offset": offset, "limit": limit}


@router.get("/posts/{post_id}", summary="Get a scheduled post",
    responses={
                404: {"description": "Not found"}
    }
)
async def get_scheduled_post(
    post_id: str,
    db: AppwriteClient    = Depends(get_db),
):
    user_id = "demo-user"
    res = db.table("scheduled_posts").select("*").eq("id", post_id).eq("user_id", user_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Scheduled post not found")
    return res.data[0]


@router.put("/posts/{post_id}", summary="Update / reschedule a post",
    responses={
                400: {"description": "Bad request"},
                404: {"description": "Not found"}
    }
)
async def update_scheduled_post(
    post_id: str,
    req: UpdateScheduleRequest,
    db: AppwriteClient    = Depends(get_db),
):
    user_id = "demo-user"
    existing = db.table("scheduled_posts").select("*").eq("id", post_id).eq("user_id", user_id).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Scheduled post not found")

    current = existing.data[0]
    upd: dict = {}

    if req.scheduled_at:
        if current["status"] not in ("scheduled", "draft", "failed"):
            raise HTTPException(status_code=400, detail=f"Cannot reschedule a {current['status']} post")
        upd["scheduled_at"] = req.scheduled_at.isoformat()
        upd["status"] = "scheduled"
        if current["status"] == "scheduled":
            await scheduler_service.reschedule_post(post_id, req.scheduled_at)
        else:
            await scheduler_service.schedule_post(post_id, req.scheduled_at)

    if req.content_text:
        upd["content_text"] = req.content_text
    if req.hashtags is not None:
        upd["hashtags"] = req.hashtags
    if req.media_urls is not None:
        upd["media_urls"] = req.media_urls
    if req.status:
        upd["status"] = req.status.value
        if req.status == ScheduleStatus.CANCELLED:
            scheduler_service.cancel_scheduled_post(post_id)

    if not upd:
        raise HTTPException(status_code=400, detail="Nothing to update")

    result = db.table("scheduled_posts").update(upd).eq("id", post_id).execute()
    return result.data[0] if result.data else {"id": post_id, **upd}


@router.delete("/posts/{post_id}", summary="Cancel a scheduled post",
    responses={
                400: {"description": "Bad request"},
                404: {"description": "Not found"}
    }
)
async def cancel_scheduled_post(
    post_id: str,
    db: AppwriteClient    = Depends(get_db),
):
    user_id = "demo-user"
    existing = db.table("scheduled_posts").select("*").eq("id", post_id).eq("user_id", user_id).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Scheduled post not found")

    current = existing.data[0]
    if current["status"] == "published":
        raise HTTPException(status_code=400, detail="Cannot cancel an already published post")

    scheduler_service.cancel_scheduled_post(post_id)
    db.table("scheduled_posts").update({"status": "cancelled"}).eq("id", post_id).execute()

    if current.get("content_id"):
        db.table("content").update({"status": "draft"}).eq("id", current["content_id"]).execute()

    return {"message": "Post cancelled", "post_id": post_id}


@router.post("/posts/{post_id}/publish-now", summary="Publish a post immediately",
    responses={
                400: {"description": "Bad request"},
                404: {"description": "Not found"}
    }
)
async def publish_now(
    post_id: str,
    http_request: Request,
    db: AppwriteClient    = Depends(get_db),
):
    """Skip the queue — publish the post right now."""
    user_id = "demo-user"
    existing = db.table("scheduled_posts").select("status").eq("id", post_id).eq("user_id", user_id).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Scheduled post not found")
    if existing.data[0]["status"] == "published":
        raise HTTPException(status_code=400, detail="Post is already published")

    await scheduler_service.publish_now(post_id)

    # Immutable audit trail — immediate publish to live channels is irreversible.
    await audit_log(
        db, user_id, "schedule.publish_now",
        resource_id=post_id, request=http_request,
    )

    updated = db.table("scheduled_posts").select("*").eq("id", post_id).execute()
    return updated.data[0] if updated.data else {"post_id": post_id, "status": "publishing"}


# ══════════════════════════════════════════════════════════════════════════════
# QUEUE MANAGEMENT  (Buffer-style recurring time slots)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/queue", summary="List queue slots")
async def list_queue_slots(
    platform: Optional[str] = Query(None),
    db: AppwriteClient      = Depends(get_db),
):
    """List all recurring time slots configured for this account."""
    user_id = "demo-user"
    q = db.table("posting_queues").select("*").eq("user_id", user_id)
    if platform:
        q = q.eq("platform", platform)
    result = q.order("day_of_week", desc=False).execute()
    slots = result.data or []
    # Annotate with day label
    for s in slots:
        s["day_label"] = _DAYS[int(s.get("day_of_week", 0))]
    return {"slots": slots, "total": len(slots)}


@router.post("/queue", summary="Add a recurring queue slot",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def add_queue_slot(
    req: QueueSlotCreate,
    db: AppwriteClient    = Depends(get_db),
):
    """
    Add a recurring posting slot (like Buffer's queue).

    Example: post to Instagram every **Monday and Wednesday at 9:00 AM**.
    When you "fill the queue", AI generates content for each empty slot.
    """
    user_id = "demo-user"
    data = {
        "user_id":    user_id,
        "platform":   req.platform.value,
        "day_of_week": int(req.day_of_week),
        "time_of_day": req.time_of_day,
        "timezone":   req.timezone,
        "label":      req.label or f"{_DAYS[int(req.day_of_week)]} {req.time_of_day}",
        "brand_id":   req.brand_id,
        "is_active":  req.is_active,
    }
    result = db.table("posting_queues").insert(data).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create queue slot")
    slot = result.data[0]
    slot["day_label"] = _DAYS[int(slot.get("day_of_week", 0))]
    return slot


@router.delete("/queue/{slot_id}", summary="Remove a queue slot",
    responses={
                404: {"description": "Not found"}
    }
)
async def remove_queue_slot(
    slot_id: str,
    db: AppwriteClient    = Depends(get_db),
):
    user_id = "demo-user"
    existing = db.table("posting_queues").select("id").eq("id", slot_id).eq("user_id", user_id).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Queue slot not found")
    db.table("posting_queues").delete().eq("id", slot_id).execute()
    return {"message": "Queue slot removed", "slot_id": slot_id}


@router.post("/queue/fill", summary="AI fills empty queue slots with generated content",
    responses={
                400: {"description": "Bad request"}
    }
)
async def fill_queue(
    req: QueueFillRequest,
    db: AppwriteClient    = Depends(get_db),
):
    """
    Automatically fill upcoming empty queue slots with AI-generated content.

    - Looks at your queue slots for the next `days_ahead` days.
    - Generates platform-native content for each empty slot.
    - Schedules the post and saves it to the content library.
    """
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    slots_res = db.table("posting_queues").select("*").eq("user_id", user_id).eq("is_active", True).execute()
    slots = slots_res.data or []
    if not slots:
        raise HTTPException(status_code=400, detail="No active queue slots configured. Add slots first via POST /scheduler/queue")

    brand_context = _brand_ctx(db, req.brand_id, user_id)
    now      = datetime.now(timezone.utc)
    end_date = now + timedelta(days=req.days_ahead)
    filled   = []
    errors   = []

    for slot in slots:
        platform    = slot["platform"]
        dow         = int(slot["day_of_week"])
        time_parts  = slot["time_of_day"].split(":")
        slot_hour   = int(time_parts[0])
        slot_minute = int(time_parts[1])

        # Find all occurrences of this slot in the window
        cursor = now
        while cursor <= end_date:
            if cursor.weekday() == dow:
                fire_at = cursor.replace(hour=slot_hour, minute=slot_minute, second=0, microsecond=0)
                if fire_at > now:
                    # Check if already scheduled for this slot
                    check = (
                        db.table("scheduled_posts")
                        .select("id")
                        .eq("user_id", user_id)
                        .eq("platform", platform)
                        .gte("scheduled_at", (fire_at - timedelta(minutes=30)).isoformat())
                        .lte("scheduled_at", (fire_at + timedelta(minutes=30)).isoformat())
                        .execute()
                    )
                    if check.data:
                        cursor += timedelta(days=1)
                        continue

                    # Generate content for this slot
                    try:
                        hint = _platform_content_hint(platform)
                        generated = await ai_service.generate_caption(
                            platform=platform,
                            context=f"{req.topic}. {hint}",
                            tone=req.tone,
                            include_hashtags=True,
                            include_emojis=True,
                            custom_instructions=req.custom_instructions,
                            brand_context=brand_context,
                        )
                        content_text = generated.get("content", "")

                        # Save to content library
                        saved = db.table("content").insert({
                            "user_id":      user_id,
                            "tenant_id":    tenant_id,
                            "title":        f"{platform.title()} — {cursor.strftime('%b %d')}",
                            "content":      content_text,
                            "content_type": platform,
                            "status":       "scheduled",
                            "brand_id":     req.brand_id,
                            "metadata":     {"source": "queue_fill", "slot_id": slot["id"]},
                        }).execute()
                        content_id = saved.data[0]["id"] if saved.data else None

                        # Schedule
                        post_res = db.table("scheduled_posts").insert({
                            "user_id":      user_id,
                            "platform":     platform,
                            "content_text": content_text,
                            "title":        f"Queue: {platform.title()} {cursor.strftime('%b %d')}",
                            "scheduled_at": fire_at.isoformat(),
                            "timezone":     slot.get("timezone", "UTC"),
                            "status":       "scheduled",
                            "content_id":   content_id,
                            "brand_id":     req.brand_id or slot.get("brand_id"),
                            "retry_count":  0,
                            "max_retries":  3,
                        }).execute()

                        if post_res.data:
                            post_id = post_res.data[0]["id"]
                            await scheduler_service.schedule_post(post_id, fire_at)
                            filled.append({
                                "post_id":      post_id,
                                "platform":     platform,
                                "scheduled_at": fire_at.isoformat(),
                                "preview":      content_text[:120] + "…" if len(content_text) > 120 else content_text,
                            })
                    except Exception as e:
                        errors.append({"slot_id": slot["id"], "date": fire_at.isoformat(), "error": str(e)})

            cursor += timedelta(days=1)

    return {
        "filled":        len(filled),
        "posts":         filled,
        "errors":        errors,
        "days_searched": req.days_ahead,
    }


# ══════════════════════════════════════════════════════════════════════════════
# BULK SCHEDULING
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/bulk", summary="Bulk schedule up to 50 posts at once")
async def bulk_schedule(
    req: BulkScheduleRequest,
    db: AppwriteClient    = Depends(get_db),
):
    """Schedule multiple posts in one request. Returns results for each post (success/error)."""
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    results = []
    for item in req.posts:
        try:
            data = {
                "user_id":      user_id,
                "platform":     item.platform.value,
                "content_text": item.content_text,
                "hashtags":     item.hashtags or [],
                "media_urls":   item.media_urls or [],
                "media_type":   item.media_type.value if item.media_type else None,
                "scheduled_at": item.scheduled_at.isoformat(),
                "timezone":     req.timezone,
                "status":       "scheduled",
                "campaign_id":  req.campaign_id,
                "brand_id":     req.brand_id,
                "retry_count":  0,
                "max_retries":  3,
            }
            saved = db.table("scheduled_posts").insert(data).execute()
            if saved.data:
                post_id = saved.data[0]["id"]
                await scheduler_service.schedule_post(post_id, item.scheduled_at)
                results.append({"status": "ok", "post_id": post_id, "scheduled_at": item.scheduled_at.isoformat(), "platform": item.platform.value})
            else:
                results.append({"status": "error", "platform": item.platform.value, "error": "DB insert returned no data"})
        except Exception as e:
            results.append({"status": "error", "platform": item.platform.value, "error": str(e)})

    ok    = [r for r in results if r["status"] == "ok"]
    fails = [r for r in results if r["status"] == "error"]
    return {"total": len(results), "scheduled": len(ok), "failed": len(fails), "results": results}


# ══════════════════════════════════════════════════════════════════════════════
# AI FEATURES
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/ai/generate", summary="AI writes content and schedules it in one step",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def ai_generate_and_schedule(
    req: AIGenerateScheduleRequest,
    request: Request,


    db: AppwriteClient    = Depends(get_db),
):
    """
    One-step: AI generates platform-optimised content and immediately schedules it.

    Great for quick scheduling without going through the content library first.
    """
    user_id = "demo-user"
    bearer = request.headers.get("Authorization", "") or None
    t0 = _time.monotonic()
    brand_context = _brand_ctx(db, req.brand_id, user_id)
    hint          = _platform_content_hint(req.platform.value)

    try:
        generated = await ai_service.generate_caption(
            platform=req.platform.value,
            context=f"{req.topic}. {hint}",
            tone=req.tone,
            include_hashtags=req.include_hashtags,
            include_emojis=req.include_emojis,
            custom_instructions=req.custom_instructions,
            brand_context=brand_context,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI generation failed: {e}")

    content_text = generated.get("content", "")

    # Save to content library
    saved_content = db.table("content").insert({
        "user_id":      user_id,
        "tenant_id":    tenant_id,
        "title":        f"{req.platform.value.title()} — {req.topic[:60]}",
        "content":      content_text,
        "content_type": req.platform.value,
        "status":       "scheduled",
        "campaign_id":  req.campaign_id,
        "brand_id":     req.brand_id,
        "metadata":     {"source": "ai_generate_schedule", "topic": req.topic},
    }).execute()
    content_id = saved_content.data[0]["id"] if saved_content.data else None

    # Schedule
    acc_res = db.table("social_accounts").select("id").eq("user_id", user_id).eq("platform", req.platform.value).eq("is_active", True).execute()
    status  = ScheduleStatus.SCHEDULED if acc_res.data else ScheduleStatus.DRAFT

    post_res = db.table("scheduled_posts").insert({
        "user_id":      user_id,
        "platform":     req.platform.value,
        "content_text": content_text,
        "title":        f"{req.platform.value.title()} — {req.topic[:60]}",
        "scheduled_at": req.scheduled_at.isoformat(),
        "timezone":     req.timezone,
        "status":       status.value,
        "content_id":   content_id,
        "campaign_id":  req.campaign_id,
        "brand_id":     req.brand_id,
        "retry_count":  0,
        "max_retries":  req.max_retries,
    }).execute()

    post_id = post_res.data[0]["id"] if post_res.data else None
    if status == ScheduleStatus.SCHEDULED and post_id:
        await scheduler_service.schedule_post(post_id, req.scheduled_at)

    return {
        "post_id":      post_id,
        "content_id":   content_id,
        "platform":     req.platform.value,
        "content":      content_text,
        "scheduled_at": req.scheduled_at.isoformat(),
        "status":       status.value,
        "queued":       status == ScheduleStatus.SCHEDULED,
    }


@router.post("/ai/fill-week", summary="AI generates and schedules a full week of posts")
async def ai_fill_week(
    req: AIFillWeekRequest,
    background: bool      = Query(False, description="Generate asynchronously in background (non-blocking)"),
    
    
    db: AppwriteClient    = Depends(get_db),
):
    """
    Generate and schedule `posts_per_week` posts spread across the coming week.

    Posts are distributed on **Mon / Wed / Fri** (for 3 posts), or evenly spaced
    for other counts. Best posting times are used automatically.
    """

    from app.config import settings

    # ── BACKGROUND MODE: Dispatch to Celery (non-blocking) ──────────────────
    # (only if Redis is configured; otherwise fall through to synchronous mode)
    if background and settings.REDIS_URL:
        logger.info(
            f"📦 Week generation dispatched to background: "
            f"platform={req.platform.value}, posts_per_week={req.posts_per_week}"
        )

        # Dispatch to Celery background task (non-blocking)
        task = fill_week_background.apply_async(
            args=(
                tenant_id,
                user_id,
                req.topic,
                [req.platform.value],
            ),
            kwargs={
                "brand_id": req.brand_id,
                "count_per_day": 1,
                "user_context": None,
            },
            queue="ai",
        )

        logger.info(
            f"✅ Week generation dispatched with task_id: {task.id}"
        )

        return {
            "task_id": task.id,
            "status": "queued",
            "message": f"Generating and scheduling {req.posts_per_week} posts for the week in background",
            "platform": req.platform.value,
            "posts_per_week": req.posts_per_week,
            "poll_url": f"/api/v1/tasks/{task.id}",
        }

    # ── SYNCHRONOUS MODE: Generate immediately (backward compatible) ────────
    brand_context = _brand_ctx(db, req.brand_id, user_id)
    hint          = _platform_content_hint(req.platform.value)

    # Spread post days across the week
    all_days = [0, 2, 4, 1, 3, 5, 6]  # Mon, Wed, Fri, Tue, Thu, Sat, Sun
    chosen_days = all_days[:req.posts_per_week]

    # Best posting hours per platform
    best_hours = {
        "instagram": 9, "linkedin": 8, "twitter": 12,
        "facebook": 13, "tiktok": 18, "youtube": 15,
        "pinterest": 20, "threads": 10,
    }
    post_hour = best_hours.get(req.platform.value, 9)

    start = req.start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    created = []
    errors  = []

    import asyncio
    async def _gen_and_schedule(day_offset: int, day_label: str):
        user_id = "demo-user"
        tenant_id = "demo-tenant"
        fire_at = start + timedelta(days=day_offset, hours=post_hour)
        if fire_at <= datetime.now(timezone.utc):
            fire_at += timedelta(weeks=1)
        try:
            resp = await ai_service.generate_caption(
                platform=req.platform.value,
                context=f"{req.topic}. {hint}. Day: {day_label}",
                tone=req.tone,
                include_hashtags=req.include_hashtags,
                include_emojis=True,
                custom_instructions=req.custom_instructions,
                brand_context=brand_context,
            )
            text = resp.get("content", "")

            saved = db.table("content").insert({
                "user_id":      user_id,
                "tenant_id":    tenant_id,
                "title":        f"{req.platform.value.title()} — {day_label} {fire_at.strftime('%b %d')}",
                "content":      text,
                "content_type": req.platform.value,
                "status":       "scheduled",
                "campaign_id":  req.campaign_id,
                "brand_id":     req.brand_id,
                "metadata":     {"source": "ai_fill_week", "topic": req.topic, "day": day_label},
            }).execute()
            content_id = saved.data[0]["id"] if saved.data else None

            post_res = db.table("scheduled_posts").insert({
                "user_id":      user_id,
                "platform":     req.platform.value,
                "content_text": text,
                "title":        f"{req.platform.value.title()} — {day_label}",
                "scheduled_at": fire_at.isoformat(),
                "timezone":     req.timezone,
                "status":       "scheduled",
                "content_id":   content_id,
                "campaign_id":  req.campaign_id,
                "brand_id":     req.brand_id,
                "retry_count":  0,
                "max_retries":  3,
            }).execute()

            if post_res.data:
                post_id = post_res.data[0]["id"]
                await scheduler_service.schedule_post(post_id, fire_at)
                return {
                    "day":          day_label,
                    "post_id":      post_id,
                    "scheduled_at": fire_at.isoformat(),
                    "preview":      text[:120] + "…" if len(text) > 120 else text,
                }
        except Exception as e:
            errors.append({"day": day_label, "error": str(e)})
            return None

    tasks = [_gen_and_schedule(d, _DAYS[d]) for d in chosen_days]
    results = await asyncio.gather(*tasks)
    created = [r for r in results if r]

    return {
        "platform":      req.platform.value,
        "posts_created": len(created),
        "posts":         created,
        "errors":        errors,
        "week_start":    start.date().isoformat(),
    }


@router.post("/ai/optimize", summary="AI rewrites content for a specific platform",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def optimize_for_platform(
    req: OptimizeContentRequest,
    request: Request,
    
    
):
    """
    Rewrite existing content to feel native on the target platform.

    - A blog excerpt → punchy LinkedIn post
    - LinkedIn post → tweet thread opener
    - Long-form → Instagram caption with hashtags
    """
    bearer = request.headers.get("Authorization", "") or None
    t0 = _time.monotonic()
    hint = _platform_content_hint(req.target_platform.value)
    try:
        result = await ai_service.generate_caption(
            platform=req.target_platform.value,
            context=f"Rewrite the following content for {req.target_platform.value}. {hint}\n\nOriginal:\n{req.content_text}",
            tone="professional",
            include_hashtags=req.include_hashtags,
            include_emojis=req.include_emojis,
            brand_context=req.brand_context,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "original":   req.content_text,
        "optimized":  result.get("content", ""),
        "platform":   req.target_platform.value,
        "hint_used":  hint,
    }


@router.post("/ai/hashtags", summary="AI suggests hashtags for your content",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def suggest_hashtags(
    req: HashtagRequest,
    
):
    """Generate `count` relevant hashtags for the given content and platform."""
    prompt = (
        f"Suggest exactly {req.count} hashtags for a {req.platform.value} post.\n"
        f"Content: {req.content_text}\n\n"
        f"Rules:\n"
        f"- Mix popular (broad reach) and niche (targeted) hashtags\n"
        f"- Format: each hashtag on its own line starting with #\n"
        f"- No explanations — just the hashtags\n"
        f"- Ordered by relevance (most relevant first)"
    )
    try:
        raw = await ai_service._call_nvidia(prompt, temperature=0.5, max_tokens=300)
        tags = [line.strip() for line in raw.strip().splitlines() if line.strip().startswith("#")]
        return {"hashtags": tags[:req.count], "platform": req.platform.value, "count": len(tags)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# BEST POSTING TIMES  (Hootsuite-style)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/best-times/{platform}", summary="Get AI best posting time recommendations")
async def get_best_times(
    platform: SchedulePlatform,
    audience: Optional[str] = Query(None, description="Describe your audience (e.g. 'B2B marketers in the US')"),

    db:       AppwriteClient = Depends(get_db),
):
    """
    Returns AI-powered best posting time recommendations for the platform.

    Recommendations are cached in the DB and refreshed on demand via
    `POST /scheduler/best-times/analyze`.
    """
    user_id = "demo-user"
    # Try cache first
    cache = (
        db.table("best_times_cache")
        .select("*")
        .eq("user_id", user_id)
        .eq("platform", platform.value)
        .execute()
    )
    if cache.data:
        rec = cache.data[0]
        meta = rec.get("metadata") or {}
        if isinstance(meta, str):
            try: meta = _json.loads(meta)
            except Exception: meta = {}
        return {
            "platform":        platform.value,
            "recommendations": meta.get("recommendations", []),
            "summary":         meta.get("summary", ""),
            "cached":          True,
            "generated_at":    rec.get("created_at", ""),
        }

    # Generate fresh
    return await _generate_best_times(platform.value, audience or "general audience", user_id, db)


@router.post("/best-times/analyze", summary="Refresh best time recommendations for a platform")
async def analyze_best_times(
    platform: SchedulePlatform = Body(..., embed=True),
    audience: Optional[str]   = Body(None),

    db:       AppwriteClient   = Depends(get_db),
):
    """Force-refresh the AI best time analysis for a platform."""
    user_id = "demo-user"
    return await _generate_best_times(platform.value, audience or "general audience", user_id, db, force=True)


async def _generate_best_times(platform: str, audience: str, user_id: str, db: AppwriteClient, force: bool = False) -> dict:
    prompt = (
        f"You are a social media data analyst. Recommend the 5 best times to post on {platform} "
        f"for this audience: {audience}.\n\n"
        "Return ONLY valid JSON — no markdown:\n"
        '{"summary": "one-line insight", "recommendations": [\n'
        '  {"day_of_week": 0, "day_label": "Monday", "hour": 9, "time_label": "9:00 AM", "score": 9.2, "reason": "why this works"}\n'
        "]}\n\n"
        "Use day_of_week 0=Monday…6=Sunday. Score 0-10. Give exactly 5 recommendations."
    )
    try:
        raw = await ai_service._call_nvidia(prompt, temperature=0.4, max_tokens=600)
        cleaned = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        data    = _json.loads(cleaned)
    except Exception:
        data = {
            "summary": f"Peak engagement on {platform} is typically mid-morning and early evening on weekdays.",
            "recommendations": [
                {"day_of_week": 1, "day_label": "Tuesday",   "hour": 9,  "time_label": "9:00 AM",  "score": 9.1, "reason": "High engagement before work hours"},
                {"day_of_week": 2, "day_label": "Wednesday",  "hour": 11, "time_label": "11:00 AM", "score": 8.8, "reason": "Mid-week peak scrolling time"},
                {"day_of_week": 3, "day_label": "Thursday",   "hour": 12, "time_label": "12:00 PM", "score": 8.5, "reason": "Lunch break browsing"},
                {"day_of_week": 0, "day_label": "Monday",     "hour": 8,  "time_label": "8:00 AM",  "score": 8.0, "reason": "Week start planning time"},
                {"day_of_week": 4, "day_label": "Friday",     "hour": 14, "time_label": "2:00 PM",  "score": 7.8, "reason": "Early afternoon Friday wind-down"},
            ],
        }

    # Cache in DB (upsert via delete + insert)
    try:
        db.table("best_times_cache").delete().eq("user_id", user_id).eq("platform", platform).execute()
        db.table("best_times_cache").insert({
            "user_id":  user_id,
            "platform": platform,
            "metadata": _json.dumps(data),
        }).execute()
    except Exception as cache_err:
        logger.warning(f"Could not cache best times: {cache_err}")

    return {
        "platform":        platform,
        "recommendations": data.get("recommendations", []),
        "summary":         data.get("summary", ""),
        "cached":          False,
        "generated_at":    datetime.now(timezone.utc).isoformat(),
    }


# ══════════════════════════════════════════════════════════════════════════════
# CALENDAR VIEW
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/calendar", summary="Calendar view — posts grouped by date")
async def scheduler_calendar(
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD — defaults to today"),
    to_date:   Optional[str] = Query(None, description="YYYY-MM-DD — defaults to 30 days from now"),
    platform:  Optional[str] = Query(None),

    db:        AppwriteClient = Depends(get_db),
):
    """
    Returns all scheduled posts grouped by date — ideal for rendering a calendar UI.

    Each day's entry contains all posts and a summary count per platform.
    """
    user_id = "demo-user"
    now    = datetime.now(timezone.utc)
    start  = from_date or now.strftime("%Y-%m-%d")
    end    = to_date   or (now + timedelta(days=30)).strftime("%Y-%m-%d")

    q = (
        db.table("scheduled_posts")
        .select("*")
        .eq("user_id", user_id)
        .gte("scheduled_at", start)
        .lte("scheduled_at", end + "T23:59:59")
        .order("scheduled_at", desc=False)
        .limit(500)
    )
    if platform:
        q = q.eq("platform", platform)

    result = q.execute()
    posts  = result.data or []

    # Group by date
    calendar: dict = {}
    for p in posts:
        day = (p.get("scheduled_at") or "")[:10]
        if not day:
            continue
        if day not in calendar:
            calendar[day] = {"date": day, "posts": [], "platform_counts": {}}
        calendar[day]["posts"].append(p)
        platform_val = p.get("platform", "other")
        calendar[day]["platform_counts"][platform_val] = calendar[day]["platform_counts"].get(platform_val, 0) + 1

    sorted_calendar = [calendar[d] for d in sorted(calendar.keys())]
    return {
        "from":      start,
        "to":        end,
        "total":     len(posts),
        "days":      sorted_calendar,
    }


# ══════════════════════════════════════════════════════════════════════════════
# ANALYTICS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/analytics", summary="Scheduler analytics — status & platform breakdown")
async def scheduler_analytics(
    days:    int           = Query(30, ge=1, le=365),

    db:      AppwriteClient = Depends(get_db),
):
    """Overview of all scheduling activity for the past `days` days."""
    user_id = "demo-user"
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    result = (
        db.table("scheduled_posts")
        .select("*")
        .eq("user_id", user_id)
        .limit(1000)
        .execute()
    )
    # Filter by date in Python — Appwrite $createdAt can't be queried via .gte()
    posts = [p for p in (result.data or []) if (p.get("created_at") or "") >= since]

    by_status:   dict = {}
    by_platform: dict = {}
    upcoming:    list = []
    now_str = datetime.now(timezone.utc).isoformat()

    for p in posts:
        st = p.get("status", "unknown")
        pl = p.get("platform", "unknown")
        by_status[st]   = by_status.get(st, 0) + 1
        by_platform[pl] = by_platform.get(pl, 0) + 1
        if st == "scheduled" and (p.get("scheduled_at") or "") > now_str:
            upcoming.append({
                "id":           p["id"],
                "platform":     pl,
                "scheduled_at": p.get("scheduled_at"),
                "preview":      (p.get("content_text") or "")[:80],
            })

    upcoming = sorted(upcoming, key=lambda x: x["scheduled_at"] or "")[:10]

    return {
        "period_days":   days,
        "total_posts":   len(posts),
        "by_status":     by_status,
        "by_platform":   by_platform,
        "upcoming_next": upcoming,
        "success_rate":  round(
            by_status.get("published", 0) / max(len(posts), 1) * 100, 1
        ),
    }


# ══════════════════════════════════════════════════════════════════════════════
# OPTIONS / ENUMS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/options", summary="List available platforms, statuses, media types")
async def scheduler_options():
    return {
        "platforms":   [p.value for p in SchedulePlatform],
        "statuses":    [s.value for s in ScheduleStatus],
        "media_types": [m.value for m in MediaType],
        "days_of_week": [{"value": d.value, "label": _DAYS[d.value]} for d in DayOfWeek],
        "best_times_note": "Call GET /scheduler/best-times/{platform} for AI-powered time recommendations",
    }
