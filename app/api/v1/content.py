# app/api/v1/content.py
from fastapi import APIRouter, HTTPException, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from typing import List, Optional
from datetime import datetime, timezone

from app.models.content import (
    CreateContentRequest, UpdateContentRequest,
    ContentResponse, ContentListResponse, ContentType, ContentStatus
)
from app.core.database import get_db
from app.db.appwrite_client import AppwriteClient
from app.utils.logger import logger
from app.utils.audit import audit_log

# Reuse the alias map from ai_generation so the content-type filter also
# matches bare platform names that older saves used (e.g. "instagram" → filter "instagram_caption").
from app.api.v1.ai_generation import CONTENT_TYPE_ALIASES
from app.utils.sanitize import sanitize_html

router = APIRouter(prefix="/content", tags=["Content Management"])


def _to_content_response(item: dict) -> ContentResponse:
    fallback = datetime.now(timezone.utc)

    def _dt(val):
        if not val:
            return fallback
        if isinstance(val, datetime):
            return val
        try:
            return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        except Exception:
            return fallback

    # Sanitize textual fields to prevent XSS when rendered by the frontend
    title = sanitize_html(item.get("title")) if item.get("title") is not None else None
    content_text = sanitize_html(item.get("content", ""))

    raw_metadata = item.get("metadata") or {}
    safe_metadata = {}
    if isinstance(raw_metadata, dict):
        for k, v in raw_metadata.items():
            safe_metadata[k] = sanitize_html(v) if isinstance(v, str) else v
    else:
        safe_metadata = raw_metadata

    # Extract validation report if stored in metadata
    validation_report = None
    if isinstance(safe_metadata, dict) and "validation" in safe_metadata:
        validation_report = safe_metadata.pop("validation")
    # Or get from direct validation field
    if item.get("validation"):
        validation_report = item.get("validation")

    return ContentResponse(
        id=item.get("id", ""),
        user_id=item.get("user_id", ""),
        title=title,
        content=content_text,
        content_type=item.get("content_type", "blog"),
        status=item.get("status", "draft"),
        metadata=safe_metadata,
        image_url=item.get("image_url"),
        image_base64=safe_metadata.get("image_base64") if isinstance(safe_metadata, dict) else None,
        campaign_id=item.get("campaign_id"),
        brand_id=item.get("brand_id"),
        validation=validation_report,
        created_at=_dt(item.get("created_at")),
        updated_at=_dt(item.get("updated_at")),
    )


@router.post("", response_model=ContentResponse, summary="Create content manually or from generation",
    responses={
                400: {"description": "Bad request"},
                500: {"description": "Internal server error"}
    }
)
async def create_content(
    content: CreateContentRequest,
    db: AppwriteClient = Depends(get_db),
):
    """Create content manually or save generated content with validation report."""
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    try:
        # Validate required fields
        if not content.content or not content.content.strip():
            raise HTTPException(status_code=400, detail="Content cannot be empty")

        if not content.content_type:
            raise HTTPException(status_code=400, detail="Content type is required")

        # Prepare metadata with validation report
        metadata = content.metadata or {}
        if content.validation:
            validation_dict = content.validation.model_dump() if hasattr(content.validation, 'model_dump') else content.validation.dict()
            metadata["validation"] = validation_dict
            metadata["quality_score"] = content.validation.overall_quality_score

        from datetime import datetime, timezone
        ctype = content.content_type.value
        content_data: dict = {
            "user_id":      user_id,
            "tenant_id":    tenant_id,
            "title":        content.title or f"Untitled {ctype}",
            "content":      content.content,
            "content_type": ctype,
            "status":       content.status.value,
            "metadata":     metadata,
            "platform":     ctype,
            "updated_at":   datetime.now(timezone.utc).isoformat(),
        }
        if content.image_url:
            content_data["image_url"] = content.image_url
        if content.campaign_id:
            content_data["campaign_id"] = content.campaign_id
        # Fallback: if no brand_id passed, use the user's default brand
        effective_brand_id = content.brand_id
        if not effective_brand_id:
            try:
                from app.api.v1.ai_generation import _resolve_default_brand_id
                effective_brand_id = _resolve_default_brand_id(db, user_id)
            except Exception:
                effective_brand_id = None
        if effective_brand_id:
            content_data["brand_id"] = effective_brand_id

        # Also store quality_score at top level for filtering (extracted from validation in metadata)
        if content.validation:
            content_data["quality_score"] = content.validation.overall_quality_score

        result = db.table("content").insert(content_data).execute()
        if not result.data:
            logger.error(f"Database insert failed for user {user_id}: no data returned")
            raise HTTPException(status_code=500, detail="Failed to create content in database")

        logger.info(f"Content created by user {user_id}, type: {content.content_type.value}")
        return _to_content_response(result.data[0])
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create content error for user {user_id}: {type(e).__name__}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to save content: {str(e)[:100]}")


@router.get("", response_model=ContentListResponse, summary="List content (paginated with filters)",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def list_content(
    content_type: Optional[ContentType] = Query(None, description="Filter by content type (all 25 types)"),
    status: Optional[ContentStatus] = Query(None, description="Filter by status (draft/published/scheduled)"),
    quality_score: Optional[str] = Query(None, description="Filter by quality score (excellent/good/fair/needs_review)"),
    brand_id: Optional[str] = Query(None, description="Filter by brand"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    sort: str = Query("created_at", description="Sort field (created_at, updated_at, quality_score)"),
    order: str = Query("desc", description="Sort order (asc, desc)"),
    db: AppwriteClient = Depends(get_db),
):
    """List all content for the current user with pagination and advanced filtering."""
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    try:
        logger.info(f"Listing content for user_id={user_id}, tenant_id={tenant_id}, content_type={content_type}, brand_id={brand_id}")
        offset = (page - 1) * page_size

        # Count all matching items for total
        count_q = db.table("content").select("*").eq("user_id", user_id)
        if tenant_id:
            count_q = count_q.eq("tenant_id", tenant_id)
        if content_type:
            ct_values = [content_type.value]
            alt = next((k for k, v in CONTENT_TYPE_ALIASES.items() if v == content_type.value), None)
            if alt and alt != content_type.value:
                ct_values.append(alt)
            count_q._queries.append({"method": "equal", "attribute": "content_type", "values": ct_values})
        if status:
            count_q = count_q.eq("status", status.value)
        if quality_score:
            count_q = count_q.eq("quality_score", quality_score)
        if brand_id:
            count_q = count_q.eq("brand_id", brand_id)
        count_q = count_q.limit(1000)
        count_result = count_q.execute()
        total = len(count_result.data or [])

        # Paginated query
        page_q = db.table("content").select("*").eq("user_id", user_id)
        if tenant_id:
            page_q = page_q.eq("tenant_id", tenant_id)
        if content_type:
            ct_values = [content_type.value]
            alt = next((k for k, v in CONTENT_TYPE_ALIASES.items() if v == content_type.value), None)
            if alt and alt != content_type.value:
                ct_values.append(alt)
            page_q._queries.append({"method": "equal", "attribute": "content_type", "values": ct_values})
        if status:
            page_q = page_q.eq("status", status.value)
        if quality_score:
            page_q = page_q.eq("quality_score", quality_score)
        if brand_id:
            page_q = page_q.eq("brand_id", brand_id)

        # Validate sort field and order
        if sort not in ("created_at", "updated_at", "quality_score"):
            sort = "created_at"
        is_desc = order.lower() == "desc"
        page_q = page_q.order(sort, desc=is_desc).limit(page_size)
        if offset:
            page_q = page_q.range(offset, offset + page_size - 1)
        result = page_q.execute()

        items = [_to_content_response(i) for i in (result.data or [])]
        logger.info(f"Listed {len(items)} content items for user {user_id} (quality: {quality_score}, brand: {brand_id})")
        return ContentListResponse(items=items, total=total, page=page, page_size=page_size)
    except Exception as e:
        logger.error(f"List content error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/search", summary="Search content and campaigns",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def search_content(
    q: str = Query(..., min_length=1, description="Search query"),
    db: AppwriteClient = Depends(get_db),
):
    """Full-text search across content titles and campaign names.

    Appwrite's Query.search() requires a full-text index on the attribute, which
    may not be configured. We instead fetch all user content and filter in Python —
    reliable with no special Appwrite collection setup needed.
    """
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    try:
        q_lower = q.strip().lower()

        # ── Content search ────────────────────────────────────────────────────
        cq = (
            db.table("content")
            .select("id,title,content_type,status,created_at")
            .eq("user_id", user_id)
        )
        if tenant_id:
            cq = cq.eq("tenant_id", tenant_id)
        all_content = cq.order("created_at", desc=True).limit(500).execute()

        matched_content = [
            item for item in (all_content.data or [])
            if q_lower in (item.get("title") or "").lower()
        ][:10]

        # ── Campaign search ───────────────────────────────────────────────────
        cam_q = (
            db.table("campaigns")
            .select("id,name,objective,status,created_at")
            .eq("user_id", user_id)
        )
        if tenant_id:
            cam_q = cam_q.eq("tenant_id", tenant_id)
        all_campaigns = cam_q.order("created_at", desc=True).limit(200).execute()

        matched_campaigns = [
            item for item in (all_campaigns.data or [])
            if q_lower in (item.get("name") or "").lower()
        ][:5]

        return {"query": q, "content": matched_content, "campaigns": matched_campaigns}
    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail="Search failed")


@router.get("/{content_id}", response_model=ContentResponse, summary="Get content by ID",
    responses={
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def get_content(
    content_id: str,
    db: AppwriteClient = Depends(get_db),
):
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    try:
        q = db.table("content").select("*").eq("id", content_id).eq("user_id", user_id)
        if tenant_id:
            q = q.eq("tenant_id", tenant_id)
        result = q.execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Content not found")
        return _to_content_response(result.data[0])
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get content error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.put("/{content_id}", response_model=ContentResponse, summary="Update content",
    responses={
                400: {"description": "Bad request"},
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def update_content(
    content_id: str,
    content: UpdateContentRequest,
    db: AppwriteClient = Depends(get_db),
):
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    try:
        existing_q = db.table("content").select("id").eq("id", content_id).eq("user_id", user_id)
        if tenant_id:
            existing_q = existing_q.eq("tenant_id", tenant_id)
        existing = existing_q.execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Content not found")

        update_data: dict = {}
        if content.title    is not None: update_data["title"]    = content.title
        if content.content  is not None: update_data["content"]  = content.content
        if content.status   is not None: update_data["status"]   = content.status.value
        if content.metadata is not None: update_data["metadata"] = content.metadata
        # image_url is optional on the model — guard so we don't AttributeError
        if hasattr(content, "image_url") and getattr(content, "image_url", None) is not None:
            update_data["image_url"] = content.image_url
        if not update_data:
            raise HTTPException(status_code=400, detail="No update data provided")

        # Always stamp updated_at on writes so the DB column stays in sync
        from datetime import datetime, timezone
        update_data["updated_at"] = datetime.now(timezone.utc).isoformat()

        update_q = db.table("content").update(update_data).eq("id", content_id).eq("user_id", user_id)
        if tenant_id:
            update_q = update_q.eq("tenant_id", tenant_id)
        result = update_q.execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to update content")

        logger.info(f"Content {content_id} updated by user {user_id}")
        return _to_content_response(result.data[0])
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update content error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/{content_id}", summary="Delete content",
    responses={
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def delete_content(
    content_id: str,
    http_request: Request,


    db: AppwriteClient = Depends(get_db),
):
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    try:
        existing_q = db.table("content").select("id").eq("id", content_id).eq("user_id", user_id)
        if tenant_id:
            existing_q = existing_q.eq("tenant_id", tenant_id)
        existing = existing_q.execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Content not found")
        del_q = db.table("content").delete().eq("id", content_id).eq("user_id", user_id)
        if tenant_id:
            del_q = del_q.eq("tenant_id", tenant_id)
        del_q.execute()
        logger.info(f"Content {content_id} deleted by user {user_id}")
        # Immutable audit trail — deletion is irreversible.
        await audit_log(
            db, user_id, "content.delete",
            resource_id=content_id, tenant_id=tenant_id or None,
            request=http_request,
        )
        return {"message": "Content deleted successfully", "content_id": content_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete content error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/{content_id}/duplicate", response_model=ContentResponse, summary="Duplicate a content item",
    responses={
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def duplicate_content(
    content_id: str,
    db: AppwriteClient = Depends(get_db),
):
    """Clone a content item as a new draft."""
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    try:
        res_q = db.table("content").select("*").eq("id", content_id).eq("user_id", user_id)
        if tenant_id:
            res_q = res_q.eq("tenant_id", tenant_id)
        res = res_q.execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Content not found")
        original = res.data[0]
        new_data = {
            "user_id":      user_id,
            "tenant_id":    tenant_id or original.get("tenant_id"),
            "campaign_id":  original.get("campaign_id"),
            "brand_id":     original.get("brand_id"),
            "title":        f"[Copy] {original.get('title', '')}",
            "content":      original.get("content", ""),
            "content_type": original.get("content_type", "blog"),
            "status":       "draft",
            "metadata":     original.get("metadata") or {},
            "platform":     original.get("platform") or original.get("content_type") or "blog",
            "updated_at":   __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        }
        saved = db.table("content").insert(new_data).execute()
        if not saved.data:
            raise HTTPException(status_code=500, detail="Failed to duplicate content")
        logger.info(f"Content {content_id} duplicated for user {user_id}")
        return _to_content_response(saved.data[0])
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Duplicate content error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{content_id}/export", summary="Export content as markdown, text, or JSON",
    responses={
                400: {"description": "Bad request"},
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def export_content(
    content_id: str,
    format: str = Query("markdown", description="Export format: markdown | text | json"),
    db: AppwriteClient = Depends(get_db),
):
    """Export a content item. Browsers will trigger file download via Content-Disposition."""
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    try:
        res_q = db.table("content").select("*").eq("id", content_id).eq("user_id", user_id)
        if tenant_id:
            res_q = res_q.eq("tenant_id", tenant_id)
        res = res_q.execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Content not found")
        item = res.data[0]
        title = item.get("title", "untitled")
        body = item.get("content", "")
        content_type = item.get("content_type", "content")
        created_at = str(item.get("created_at", ""))[:10]
        safe_title = title.replace(" ", "-").replace("/", "-").lower()[:60]

        # Restrict export formats to the allowed list
        if format not in ("markdown", "text", "json"):
            raise HTTPException(status_code=400, detail="Invalid export format requested")

        if format == "markdown":
            md = (
                f"---\ntitle: \"{title}\"\ntype: {content_type}\n"
                f"status: {item.get('status', 'draft')}\ncreated: {created_at}\n---\n\n"
                f"# {title}\n\n{body}"
            )
            return PlainTextResponse(
                content=md, media_type="text/markdown",
                headers={"Content-Disposition": f'attachment; filename=\"{safe_title}.md\"'},
            )
        elif format == "text":
            txt = f"{title}\n{'=' * len(title)}\n\n{body}"
            return PlainTextResponse(
                content=txt, media_type="text/plain",
                headers={"Content-Disposition": f'attachment; filename=\"{safe_title}.txt\"'},
            )
        else:
            return _to_content_response(item)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Export content error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/bulk/status", summary="Bulk update content status")
async def bulk_update_status(
    content_ids: List[str],
    status: ContentStatus,
    db: AppwriteClient = Depends(get_db),
):
    """Update the status of multiple content items in one request."""
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    updated, failed = [], []
    for cid in content_ids:
        try:
            existing_q = db.table("content").select("id").eq("id", cid).eq("user_id", user_id)
            if tenant_id:
                existing_q = existing_q.eq("tenant_id", tenant_id)
            existing = existing_q.execute()
            if not existing.data:
                failed.append({"id": cid, "reason": "not found"})
                continue
            update_q = db.table("content").update({"status": status.value}).eq("id", cid).eq("user_id", user_id)
            if tenant_id:
                update_q = update_q.eq("tenant_id", tenant_id)
            update_q.execute()
            updated.append(cid)
        except Exception as item_err:
            failed.append({"id": cid, "reason": str(item_err)})
    return {
        "updated_count": len(updated),
        "failed_count":  len(failed),
        "updated_ids":   updated,
        "failed":        failed,
    }
