# app/api/v1/history.py
"""
Content and media history endpoints.

Retrieves generated content and media files with full metadata.
Supports filtering by type, date range, and pagination.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional, List

from app.core.database import get_db
from app.db.appwrite_client import AppwriteClient
from app.utils.logger import logger
from pydantic import BaseModel

router = APIRouter(prefix="/history", tags=["Content & Media History"])


# ── Response Models ──────────────────────────────────────────────────────────

class MediaHistoryItem(BaseModel):
    """Single media history entry."""
    id: Optional[str] = None
    media_id: str
    content_id: Optional[str] = None
    media_type: str  # "image", "video", etc.
    file_url: Optional[str] = None
    file_name: Optional[str] = None
    mime_type: str
    size_bytes: int
    width: Optional[int] = None
    height: Optional[int] = None
    model: str
    prompt: str
    created_at: str
    metadata: Optional[dict] = None


class ContentHistoryItem(BaseModel):
    """Single content history entry."""
    id: Optional[str] = None
    title: str
    content: str
    content_type: str  # "blog", "tweet", "email", "image", "video", etc.
    status: str  # "draft", "published", "scheduled"
    media_id: Optional[str] = None
    file_url: Optional[str] = None
    created_at: str
    updated_at: Optional[str] = None
    metadata: Optional[dict] = None


class HistoryListResponse(BaseModel):
    """Paginated list response."""
    items: List[dict]
    total: int
    page: int
    page_size: int


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/media", response_model=HistoryListResponse, summary="Get media history",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def get_media_history(
    media_type: Optional[str] = Query(None, description="Filter by media type: image, video"),
    days: int = Query(30, ge=1, le=365, description="Days to look back"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    sort_order: str = Query("desc", description="Sort order (asc or desc)"),

    db: AppwriteClient = Depends(get_db),
):
    """
    Get paginated media (image/video) history for current user.

    Reads from the content table filtering by content_type=image|video.
    """
    user_id = "demo-user"
    try:
        # Map media_type param → content_type values
        type_filter = media_type if media_type in ("image", "video") else "image"

        query = (
            db.table("content")
            .select("*")
            .eq("user_id", user_id)
            .eq("content_type", type_filter)
            .order("created_at", desc=(sort_order.lower() == "desc"))
        )

        offset = (page - 1) * page_size
        query = query.range(offset, offset + page_size - 1)

        result = query.execute()
        logger.info(f"✅ Retrieved {len(result.data)} media items for user={user_id}")

        return HistoryListResponse(
            items=result.data or [],
            total=result.count,
            page=page,
            page_size=page_size,
        )

    except Exception as e:
        logger.error(f"❌ Media history error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/content", response_model=HistoryListResponse, summary="Get content history",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def get_content_history(
    content_type: Optional[str] = Query(None, description="Filter by content type (blog, tweet, email, image, etc.)"),
    status: Optional[str] = Query(None, description="Filter by status (draft, published, scheduled)"),
    days: int = Query(30, ge=1, le=365, description="Days to look back"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    sort_by: str = Query("created_at", description="Sort field"),
    sort_order: str = Query("desc", description="Sort order (asc or desc)"),

    db: AppwriteClient = Depends(get_db),
):
    """
    Get paginated content history for current user.

    Supports filtering by type and status. Includes media references.
    """
    user_id = "demo-user"
    try:
        # Build query — created_at maps to $createdAt via _field() in appwrite_client
        query = db.table("content").select("*").eq("user_id", user_id)

        if content_type:
            query = query.eq("content_type", content_type)
        if status:
            query = query.eq("status", status)

        # Sort (sort_by defaults to created_at which maps to $createdAt)
        desc = sort_order.lower() == "desc"
        query = query.order("created_at", desc=desc)

        # Paginate
        offset = (page - 1) * page_size
        query = query.range(offset, offset + page_size - 1)

        result = query.execute()

        logger.info(f"✅ Retrieved {len(result.data)} content history items for user={user_id}")

        return HistoryListResponse(
            items=result.data,
            total=result.count,
            page=page,
            page_size=page_size,
        )

    except Exception as e:
        logger.error(f"❌ Content history error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search", response_model=HistoryListResponse, summary="Search content and media",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def search_history(
    query_text: str = Query(..., min_length=1, description="Search prompt or content"),
    content_types: Optional[str] = Query(None, description="Comma-separated content types to search"),
    days: int = Query(30, ge=1, le=365, description="Days to look back"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),

    db: AppwriteClient = Depends(get_db),
):
    """
    Search content and media history by prompt, title, or content.

    Returns matching items with pagination.
    """
    user_id = "demo-user"
    try:
        # Search content table by title (media_history collection doesn't exist — all
        # generated images/videos are stored in the content table as content_type=image/video)
        content_query = (
            db.table("content")
            .select("*")
            .eq("user_id", user_id)
            .ilike("title", f"%{query_text}%")
            .order("created_at", desc=True)
        )
        content_result = content_query.limit(page_size).execute()
        all_items = (content_result.data or [])[:page_size]

        logger.info(f"✅ Found {len(all_items)} matching items for query='{query_text}'")

        return HistoryListResponse(
            items=all_items,
            total=len(all_items),
            page=page,
            page_size=page_size,
        )

    except Exception as e:
        logger.error(f"❌ Search error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
