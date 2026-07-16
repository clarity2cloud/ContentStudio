# app/api/v1/dashboard.py
"""
Dashboard API — home-screen stats, activity feed, and quick insights.
Powers the main dashboard that users see right after login.
"""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone, timedelta
from app.core.database import get_db
from app.db.appwrite_client import AppwriteClient
from app.utils.logger import logger

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/overview", summary="Home-screen overview stats",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def get_dashboard_overview(

    db: AppwriteClient = Depends(get_db),
):
    """
    Returns everything the home dashboard needs in a single request:

    - **Content stats** — total, by status (draft / published / scheduled), by type
    - **Campaign stats** — total, active, completed
    - **Brand profiles** — count
    - **Scheduled posts** — upcoming posts in the next 7 days
    - **Recent activity** — last 5 content items created/updated
    - **Quick wins** — suggested next actions (empty state guidance)
    """
    user_id = "demo-user"
    try:
        now = datetime.now(timezone.utc)

        # ── Parallel data fetches ────────────────────────────────
        content_res   = db.table("content").select("id, title, content_type, status, created_at")\
                          .eq("user_id", user_id).order("created_at", desc=True).limit(5000).execute()
        campaign_res  = db.table("campaigns").select("id, name, status, created_at")\
                          .eq("user_id", user_id).execute()
        brand_res     = db.table("brand_profiles").select("id, name, is_default")\
                          .eq("user_id", user_id).execute()
        scheduled_res = db.table("scheduled_posts").select("id, platform, scheduled_time, status")\
                          .eq("user_id", user_id).eq("status", "pending")\
                          .order("scheduled_time", desc=False).limit(10).execute()

        content_items  = content_res.data  or []
        campaigns      = campaign_res.data or []
        brands         = brand_res.data    or []
        scheduled      = scheduled_res.data or []

        # ── Content stats ────────────────────────────────────────
        by_status: dict = {}
        by_type:   dict = {}
        for item in content_items:
            st = item.get("status", "unknown")
            ct = item.get("content_type", "unknown")
            by_status[st] = by_status.get(st, 0) + 1
            by_type[ct]   = by_type.get(ct, 0) + 1

        # ── Campaign stats ───────────────────────────────────────
        camp_by_status: dict = {}
        for camp in campaigns:
            st = camp.get("status", "unknown")
            camp_by_status[st] = camp_by_status.get(st, 0) + 1

        # ── Recent activity (last 5 content items) ───────────────
        recent_activity = [
            {
                "id":           item["id"],
                "title":        item.get("title", "(untitled)"),
                "content_type": item.get("content_type"),
                "status":       item.get("status"),
                "created_at":   item.get("created_at"),
            }
            for item in content_items[:5]
        ]

        # ── Upcoming scheduled posts ─────────────────────────────
        upcoming_posts = [
            {
                "id":             s["id"],
                "platform":       s.get("platform"),
                "scheduled_time": s.get("scheduled_time"),
            }
            for s in scheduled
        ]

        # ── Quick wins / next actions ────────────────────────────
        quick_wins = []
        if not brands:
            quick_wins.append({
                "action": "create_brand",
                "title":  "Set up your Brand Profile",
                "desc":   "Define your brand's tone, voice, and vocabulary so every piece of AI-generated content is on-brand.",
                "cta":    "Create Brand",
                "url":    "/brands/new",
            })
        if not campaigns:
            quick_wins.append({
                "action": "create_campaign",
                "title":  "Launch your first Campaign",
                "desc":   "Group your content by campaign and generate a full content suite in one click.",
                "cta":    "New Campaign",
                "url":    "/campaigns/new",
            })
        if len(content_items) < 3:
            quick_wins.append({
                "action": "generate_content",
                "title":  "Generate your first content",
                "desc":   "Try the AI to generate a blog post, tweet, email, or social caption.",
                "cta":    "Generate Content",
                "url":    "/ai/generate",
            })

        return {
            "user_id": user_id,
            "generated_at": now.isoformat(),
            "content": {
                "total":     content_res.count,   # true Appwrite total, not capped by limit
                "by_status": by_status,
                "by_type":   by_type,
            },
            "campaigns": {
                "total":     len(campaigns),
                "by_status": camp_by_status,
            },
            "brands": {
                "total":   len(brands),
                "default": next((b["name"] for b in brands if b.get("is_default")), None),
            },
            "scheduled_posts": {
                "upcoming_count": len(upcoming_posts),
                "upcoming":       upcoming_posts,
            },
            "recent_activity": recent_activity,
            "quick_wins":      quick_wins,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ dashboard overview: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/activity", summary="Recent activity feed",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def get_activity_feed(
    limit: int = 20,

    db: AppwriteClient = Depends(get_db),
):
    """
    Returns the last {limit} content items created or updated — ordered newest first.
    Use this to power the activity feed on the dashboard sidebar.
    """
    user_id = "demo-user"
    try:
        res = db.table("content").select("id, title, content_type, status, campaign_id, created_at")\
                .eq("user_id", user_id)\
                .order("created_at", desc=True)\
                .limit(min(limit, 50)).execute()
        return {"items": res.data or [], "total": len(res.data or [])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/content-breakdown", summary="Content production breakdown by type and channel",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def get_content_breakdown(
    days: int = 30,

    db: AppwriteClient = Depends(get_db),
):
    """
    Returns content produced per channel/type over the last N days.
    Use this to power bar charts / pie charts on the analytics dashboard.
    """
    user_id = "demo-user"
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        # Appwrite REST doesn't accept range queries on $createdAt via our builder,
        # so we fetch all user content and filter by created_at on the client side.
        res = db.table("content").select("content_type, status, created_at")\
                .eq("user_id", user_id)\
                .order("created_at", desc=False)\
                .limit(5000).execute()
        all_items = res.data or []
        items = [i for i in all_items if (i.get("created_at") or "") >= since]

        breakdown: dict = {}
        for item in items:
            ct = item.get("content_type", "unknown")
            breakdown[ct] = breakdown.get(ct, 0) + 1

        return {
            "period_days": days,
            "total":       len(items),   # accurate count after client-side date filter
            "breakdown":   breakdown,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
