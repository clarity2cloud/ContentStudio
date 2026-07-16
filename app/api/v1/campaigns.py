# app/api/v1/campaigns.py
from fastapi import APIRouter, HTTPException, Depends, Query, Body, Request
from typing import List, Optional
from datetime import datetime, timedelta, timezone
import asyncio
import uuid
import os
import requests
from app.models.campaign import (
    CampaignCreate, CampaignUpdate, CampaignResponse, CampaignStatus,
    CampaignObjective, CampaignTargetAudience, CampaignCTA, Channel
)
from app.models.content import Tone
from app.core.database import get_db
from app.core.dependencies import (
    get_current_user_id, get_current_tenant_id,
    get_current_user_id_optional, get_current_tenant_id_optional
)
from app.db.appwrite_client import AppwriteClient, AppwriteDB
from app.services.ai_service import ai_service
from app.services.campaign_pipeline import campaign_pipeline
import time
from app.utils.logger import logger
from app.tasks.campaign_tasks import generate_campaign_background
from app.config import settings

# Helper for querying campaign_content collection
_appwrite_db = AppwriteDB()


def _mirror_to_content_library(data: dict, user_id: str, tenant_id: str):
    """Mirror campaign content to the content collection so it appears in Content Library."""
    try:
        lib_data = {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "title": data.get("title", ""),
            "content": data.get("body", ""),
            "content_type": data.get("content_type", data.get("channel", "other")),
            "status": data.get("status", "draft"),
            "campaign_id": data.get("campaign_id"),
        }
        if data.get("brand_id"):
            lib_data["brand_id"] = data["brand_id"]
        _appwrite_db.create_document("content", lib_data)
    except Exception as e:
        logger.warning(f"[CONTENT MIRROR] Failed to mirror to content library: {e}")


router = APIRouter(prefix="/campaigns", tags=["Campaign Management"])


# ── Brand helpers ─────────────────────────────────────────────
def _fetch_brand_profile(db: AppwriteClient, brand_id: Optional[str]) -> dict:
    """Return the raw brand_profiles row (or empty dict)."""
    if not brand_id:
        return {}
    res = db.table("brand_profiles").select("*").eq("id", brand_id).execute()
    return res.data[0] if res.data else {}


def _fetch_brand_context(db: AppwriteClient, brand_id: Optional[str]) -> str:
    """
    Turn a brand_profiles row into a rich text block for AI prompts.

    Uses brand_validator.build_brand_block() to build the full brand context block
    — making the brand the true umbrella for content generation.
    """
    b = _fetch_brand_profile(db, brand_id)
    if not b:
        return ""
    try:
        from app.services import brand_validator as _bv
        from app.services.cache_service import cache, brand_context_key
        cached = cache.get(brand_context_key(brand_id or ""))
        if isinstance(cached, str) and cached:
            return cached
        block = _bv.build_brand_block(b)
        if block and brand_id:
            cache.set(brand_context_key(brand_id), block, ttl=3600)
        if block:
            return block
    except Exception:
        pass

    # Legacy fallback (shouldn't be reached now)
    parts = []
    if b.get("name"):            parts.append(f"Brand Name: {b['name']}")
    if b.get("website_url"):     parts.append(f"Website: {b['website_url']}")
    if b.get("industry"):        parts.append(f"Industry: {b['industry']}")
    if b.get("positioning"):     parts.append(f"Brand Positioning: {b['positioning']}")
    if b.get("tone"):            parts.append(f"Brand Tone: {b['tone']}")
    if b.get("voice"):           parts.append(f"Brand Voice: {b['voice']}")
    if b.get("target_audience"): parts.append(f"Target Audience: {b['target_audience']}")
    if b.get("vocabulary"):      parts.append(f"Always use these words/phrases: {', '.join(b['vocabulary'])}")
    if b.get("avoid_words"):     parts.append(f"Never use these words/phrases: {', '.join(b['avoid_words'])}")
    if b.get("cta_examples"):    parts.append(f"Approved CTAs (use one of these): {', '.join(b['cta_examples'])}")
    return "\n".join(parts)


def _resolve_campaign_context(db: AppwriteClient, camp: dict) -> dict:
    """
    Merge campaign fields with brand profile intelligently.
    Campaign-specific fields (audience, CTA, tone, notes) take priority over
    brand defaults — the campaign is a deliberate override. Brand provides
    voice, psychology, vocabulary, and identity that are always applied.
    Returns enriched: objective, audience, cta, tone, brand_ctx, notes.
    """
    brand = _fetch_brand_profile(db, camp.get("brand_id"))

    # Audience: campaign-specific audience takes priority; brand is the fallback
    audience = (
        camp.get("target_audience") or ""
        or brand.get("target_audience") or ""
    )

    # CTA: campaign-specific CTA takes priority; brand approved CTAs are the fallback
    brand_ctas = brand.get("cta_examples") or []
    cta = (
        camp.get("cta") or ""
        or (brand_ctas[0] if brand_ctas else "")
    )

    # Tone: campaign tone > brand delivery_tone > brand tone > professional
    tone = (
        camp.get("tone") or ""
        or brand.get("delivery_tone") or ""
        or brand.get("tone") or ""
        or "professional"
    )

    # Campaign notes — passed verbatim to LLM as strategic context
    notes = (camp.get("notes") or "").strip()

    # Build a rich brand context block
    brand_ctx = _fetch_brand_context(db, camp.get("brand_id"))

    # Budget context — passed to LLM so ad copy / scale is calibrated correctly
    budget = camp.get("budget")
    budget_context = None
    if budget:
        try:
            b = float(budget)
            if b >= 10000:
                budget_context = f"Campaign budget: ${b:,.0f} (large-scale launch — premium positioning, broad reach)."
            elif b >= 1000:
                budget_context = f"Campaign budget: ${b:,.0f} (mid-size campaign — focus on targeted value messaging)."
            else:
                budget_context = f"Campaign budget: ${b:,.0f} (lean campaign — high-efficiency, punchy copy, strong single CTA)."
        except (ValueError, TypeError):
            pass

    return {
        "objective":      camp.get("objective", ""),
        "audience":       audience,
        "cta":            cta,
        "tone":           tone,
        "brand_ctx":      brand_ctx,
        "brand_name":     brand.get("name", camp.get("name", "")),
        "channels":       camp.get("channels", []),
        "budget_context": budget_context,
        "notes":          notes,
    }


def _get_default_brand_id(db: AppwriteClient, user_id: str) -> Optional[str]:
    res = db.table("brand_profiles").select("id")\
            .eq("user_id", user_id).eq("is_default", True).execute()
    return res.data[0]["id"] if res.data else None


# ══════════════════════════════════════════════════════════════
# NEW: Get all dropdown options
# ══════════════════════════════════════════════════════════════
@router.get("/options", summary="Get all available dropdown options for campaign creation")
async def get_campaign_options():
    """
    Returns lists of predefined options for objective, target_audience, cta, and channels.
    Frontend can use this to populate dropdowns/radio buttons.
    """
    return {
        "objectives":       [item.value for item in CampaignObjective],
        "target_audiences": [item.value for item in CampaignTargetAudience],
        "ctas":             [item.value for item in CampaignCTA],
        "channels":         [item.value for item in Channel],
        "tones":            [item.value for item in Tone],
        "statuses":         [item.value for item in CampaignStatus],
        "budget": {
            "currency": "USD",
            "min":      0,
            "max":      1_000_000,
            "note":     "Optional. Leave blank for no budget cap.",
        },
    }


# ══════════════════════════════════════════════════════════════
# CRUD
# ══════════════════════════════════════════════════════════════

@router.post("", response_model=CampaignResponse, status_code=201,
             summary="Create campaign",
                 responses={
                500: {"description": "Internal server error"}
                 }
             )
async def create_campaign(
    campaign: CampaignCreate,
    user_id: str = Depends(get_current_user_id),
    tenant_id: str = Depends(get_current_tenant_id),
    db: AppwriteClient   = Depends(get_db),
):
    """
    **PRD §5.2 — Campaign-First Workflow**

    The campaign is the primary unit of work. Every content asset
    (blog, tweet, email, caption) lives under a campaign and shares
    the same objective, audience, and brand voice.

    - **channels**: `blog`, `twitter`, `linkedin`, `instagram`, `facebook`, `email`
    - **brand_id**: Leave blank to use your default brand profile
    """
    try:
        brand_id = campaign.brand_id or _get_default_brand_id(db, user_id)
        # Resolve target_audience: prefer submitted value, fall back to brand profile
        brand = _fetch_brand_profile(db, brand_id)
        target_audience = (
            campaign.target_audience.value
            if campaign.target_audience
            else brand.get("target_audience") or "general_consumers"
        )
        cta = (
            campaign.cta.value
            if campaign.cta
            else (brand.get("cta_examples") or ["learn_more"])[0]
        )
        data = {
            "user_id":         user_id,
            "tenant_id":       tenant_id,
            "name":            campaign.name,
            "objective":       campaign.objective.value if hasattr(campaign.objective, "value") else str(campaign.objective),
            "target_audience": target_audience,
            "channels":        [c.value for c in campaign.channels],
            "cta":             cta,
            "tone":            campaign.tone.value if campaign.tone else "professional",
            "brand_id":        brand_id,
            "status":          "draft",
            "start_date":      campaign.start_date.isoformat() if campaign.start_date else None,
            "end_date":        campaign.end_date.isoformat()   if campaign.end_date   else None,
            "budget":          str(campaign.budget) if campaign.budget is not None else None,
            "notes":           campaign.notes,
            "updated_at":      datetime.now(timezone.utc).isoformat(),
        }
        result = db.table("campaigns").insert(data).execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create campaign")
        logger.info(f"✅ Campaign '{campaign.name}' created")
        return CampaignResponse(**result.data[0])
    except HTTPException:
        raise
    except Exception as e:
        logger.error("❌ create_campaign: {}", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=List[CampaignResponse], summary="List campaigns",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def list_campaigns(
    status: Optional[CampaignStatus] = Query(None, description="Filter by status"),
    db: AppwriteClient   = Depends(get_db),
):
    """List all campaigns for the current user, newest first."""
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    try:
        q = db.table("campaigns").select("*").eq("user_id", user_id)
        if tenant_id:
            q = q.eq("tenant_id", tenant_id)
        if status:
            q = q.eq("status", status.value)
        result = q.order("created_at", desc=True).execute()
        return [CampaignResponse(**c) for c in (result.data or [])]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Campaign Pipeline Job Tracking ────────────────────────────────────────
# IMPORTANT: These must come BEFORE /{campaign_id} to be matched correctly by FastAPI
@router.get(
    "/{job_id}/progress",
    summary="Get pipeline job progress",
    description="""
    Track progress of a large campaign generation job.

    **Returns:**
    - `job_id`: Unique job identifier
    - `status`: pending | in_progress | completed | failed
    - `progress_percent`: 0-100
    - `completed_items`: Number of items generated
    - `total_items`: Total items to generate
    - `is_complete`: Boolean
    """
,
    responses={
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def get_campaign_job_progress(
    job_id: str,
):
    """Get current progress of a campaign generation job.

    Authentication is optional — job_id acts as the access key (UUIDs are not guessable).
    This allows tracking long-running campaigns even if the session expires.
    """
    try:
        status = campaign_pipeline.get_job_status(job_id)

        if status.get("status") == "not_found":
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        return status

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"❌ Failed to get job progress for {job_id}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/{job_id}/results",
    summary="Get pipeline job results",
    description="""
    Retrieve all generated content from a completed campaign job.

    **Returns:**
    - `job_id`: Unique job identifier
    - `status`: completed | in_progress | failed
    - `results`: Array of generated content items
    - `errors`: Array of error messages (if any)
    - `completed_items`: Count of successful generations
    """
,
    responses={
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def get_campaign_job_results(
    job_id: str,
):
    """Get all results from a completed campaign job.

    Authentication is optional — job_id acts as the access key (UUIDs are not guessable).
    This allows retrieving results even if the session expires.
    """
    try:
        results = campaign_pipeline.get_job_results(job_id)

        if results.get("error"):
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        return results

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"❌ Failed to get job results for {job_id}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{campaign_id}", response_model=CampaignResponse, summary="Get campaign",
    responses={
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def get_campaign(
    campaign_id: str,
    db: AppwriteClient   = Depends(get_db),
):
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    try:
        q = db.table("campaigns").select("*").eq("id", campaign_id).eq("user_id", user_id)
        if tenant_id:
            q = q.eq("tenant_id", tenant_id)
        result = q.execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        return CampaignResponse(**result.data[0])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{campaign_id}", response_model=CampaignResponse, summary="Update campaign",
    responses={
                400: {"description": "Bad request"},
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def update_campaign(
    campaign_id: str,
    campaign: CampaignUpdate,
    db: AppwriteClient   = Depends(get_db),
):
    """
    Update campaign details or advance its status:
    `draft → active → review → completed → archived`
    """
    user_id = "demo-user"
    try:
        existing = db.table("campaigns").select("id")\
                     .eq("id", campaign_id).eq("user_id", user_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        upd = {}
        if campaign.name            is not None: upd["name"]            = campaign.name
        if campaign.objective       is not None: upd["objective"]       = campaign.objective.value if hasattr(campaign.objective, "value") else str(campaign.objective)
        if campaign.target_audience is not None: upd["target_audience"] = campaign.target_audience.value
        if campaign.channels        is not None: upd["channels"]        = [c.value for c in campaign.channels]
        if campaign.cta             is not None: upd["cta"]             = campaign.cta.value
        if campaign.tone            is not None: upd["tone"]            = campaign.tone.value
        if campaign.brand_id        is not None: upd["brand_id"]        = campaign.brand_id
        if campaign.status          is not None: upd["status"]          = campaign.status.value
        if campaign.start_date      is not None: upd["start_date"]      = campaign.start_date.isoformat()
        if campaign.end_date        is not None: upd["end_date"]        = campaign.end_date.isoformat()
        if campaign.budget          is not None: upd["budget"]          = str(campaign.budget)
        if campaign.notes           is not None: upd["notes"]           = campaign.notes
        if not upd:
            raise HTTPException(status_code=400, detail="No fields to update")
        # Always stamp updated_at so the DB column stays in sync (not just $updatedAt)
        upd["updated_at"] = datetime.now(timezone.utc).isoformat()
        result = db.table("campaigns").update(upd).eq("id", campaign_id).execute()
        return CampaignResponse(**result.data[0])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{campaign_id}", summary="Delete campaign",
    responses={
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def delete_campaign(
    campaign_id: str,
    db: AppwriteClient   = Depends(get_db),
):
    user_id = "demo-user"
    try:
        existing = db.table("campaigns").select("id")\
                     .eq("id", campaign_id).eq("user_id", user_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        db.table("campaigns").delete().eq("id", campaign_id).execute()
        return {"message": "Campaign deleted", "campaign_id": campaign_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════
# CONTENT ASSETS
# ══════════════════════════════════════════════════════════════

@router.get("/{campaign_id}/content", summary="Get all content assets for a campaign",
    responses={
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def get_campaign_content(
    campaign_id: str,
    db: AppwriteClient   = Depends(get_db),
):
    """
    Return all content assets (drafts, review, published) grouped under this campaign.
    Queries unified 'campaign_content' collection (single source of truth).
    Results are grouped by content_type for easy consumption by frontends.
    """
    user_id = "demo-user"
    try:
        camp = db.table("campaigns").select("*")\
                 .eq("id", campaign_id).eq("user_id", user_id).execute()
        if not camp.data:
            raise HTTPException(status_code=404, detail="Campaign not found")

        all_items = []

        # Query unified campaign_content collection (falls back to the
        # in-memory store automatically if Appwrite isn't configured)
        try:
            offset = 0
            page_size = 100
            total = None
            while total is None or offset < total:
                result = _appwrite_db.list_documents("campaign_content", queries=[
                    {"method": "equal", "attribute": "campaign_id", "values": [campaign_id]},
                    {"method": "limit", "values": [page_size]},
                    {"method": "offset", "values": [offset]},
                ])
                documents = result.get("documents", [])
                for doc in documents:
                    all_items.append({
                        "id": doc.get("id", ""),
                        "campaign_id": doc.get("campaign_id"),
                        "content_type": doc.get("content_type") or doc.get("channel", "other"),
                        "title": doc.get("title", ""),
                        "content": doc.get("body", ""),
                        "channel": doc.get("channel", ""),
                        "phase": doc.get("phase", ""),
                        "status": doc.get("status", "draft"),
                        "scheduled_for": doc.get("scheduled_for"),
                        "created_at": doc.get("created_at", ""),
                        "updated_at": doc.get("updated_at", ""),
                    })
                total = result.get("total", len(documents))
                if not documents:
                    break
                offset += page_size

            logger.debug(f"[ContentQuery] Found {len(all_items)} total items in campaign_content for {campaign_id}")

        except Exception as e:
            logger.warning(f"[ContentQuery] Could not query campaign_content collection: {e}")

        # Group by content_type
        grouped: dict = {}
        for item in all_items:
            ct = item.get("content_type", "other")
            grouped.setdefault(ct, []).append(item)

        return {
            "campaign":        CampaignResponse(**camp.data[0]),
            "total_assets":    len(all_items),
            "content_by_type": grouped,
            "all_content":     all_items,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{campaign_id}/content/{content_id}", summary="Update a campaign content asset",
    responses={
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def update_campaign_content(
    campaign_id: str,
    content_id: str,
    title: Optional[str] = Body(None),
    content: Optional[str] = Body(None),
    status: Optional[str] = Body(None),
    db: AppwriteClient = Depends(get_db),
):
    """Update title, body, or status of a campaign content asset."""
    user_id = "demo-user"
    try:
        camp = db.table("campaigns").select("id").eq("id", campaign_id).eq("user_id", user_id).execute()
        if not camp.data:
            raise HTTPException(status_code=404, detail="Campaign not found")

        update_data = {}
        if title is not None:
            update_data["title"] = title
        if content is not None:
            update_data["body"] = content
        if status is not None:
            update_data["status"] = status

        _appwrite_db.update_document("campaign_content", content_id, update_data)
        return {"message": "Content updated", "content_id": content_id}
    except HTTPException:
        raise
    except Exception as e:
        err_str = str(e)
        if "404" in err_str or "not found" in err_str.lower():
            raise HTTPException(status_code=404, detail="Content not found")
        logger.error(f"Update campaign content error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{campaign_id}/content/{content_id}/generate-image",
             summary="Generate, persist, and link an AI image for a campaign content item",
                 responses={
                404: {"description": "Not found"},
                500: {"description": "Internal server error"},
                502: {"description": "Bad gateway"}
                 }
             )
async def campaign_generate_image(
    campaign_id: str,
    content_id: str,
    request: Request,
    prompt: str = Body(...),
    platform: str = Body("instagram"),
    db: AppwriteClient = Depends(get_db),
):
    """
    Full image generation pipeline for campaign AI Images channel:

    1. Verify campaign ownership
    2. Call NVIDIA FLUX to generate the image
    3. Upload to Appwrite Storage 'media' bucket → get permanent public URL
    4. Insert into `content` table WITH campaign_id (→ appears in Content Library under Campaigns)
    5. Update `campaign_content.body` = URL (→ detected on page refresh, no regeneration)
    6. Return { image_url } for the frontend to display immediately
    """
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    import base64 as _b64

    try:
        # ── 1. Ownership check + fetch brand_id ──────────────────────────────
        camp = db.table("campaigns").select("id,brand_id").eq("id", campaign_id).eq("user_id", user_id).execute()
        if not camp.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        brand_id = camp.data[0].get("brand_id") if camp.data else None

        # ── 2. Credit check ───────────────────────────────────────────────────
        bearer = request.headers.get("Authorization", "")

        # ── 3. Generate image via NVIDIA FLUX ─────────────────────────────────
        from app.services.modal_service import ModalService
        from app.services.ai_service import ai_service as _ai

        modal = ModalService()

        # Enhance the user prompt; append hard "no text" suffix
        try:
            enhanced = await _ai.enhance_image_prompt_fast(prompt=prompt, platform=platform)
        except Exception:
            enhanced = prompt
        full_prompt = (
            enhanced.strip()
            + ", no text, no words, no letters, no signs, no captions, no watermarks, high quality"
        )[:800]

        # All sizes are exact NVIDIA FLUX valid resolutions
        _PLATFORM_SIZES = {
            "instagram":         (1024, 1024),  # square  1:1
            "instagram_caption": (1024, 1024),  # square  1:1
            "images":            (1024, 1024),  # square  1:1
            "tiktok":            (800,  1328),  # portrait 9:16 (closest FLUX)
            "facebook":          (1392, 752),   # landscape 16:9 (closest FLUX)
            "twitter":           (1392, 752),   # landscape 16:9 (closest FLUX)
            "linkedin":          (1248, 832),   # landscape 3:2  (closest FLUX)
        }
        img_w, img_h = _PLATFORM_SIZES.get(platform.lower(), (1024, 1024))

        result = await modal.generate_image(prompt=full_prompt, size=f"{img_w}x{img_h}")
        b64 = result.get("image_base64", "")
        if not b64:
            raise HTTPException(status_code=502, detail="FLUX returned no image data")

        image_bytes = _b64.b64decode(b64)

        # ── 4. Upload to Appwrite Storage (with disk fallback) ───────────────────
        image_url = ""
        file_id = ""
        try:
            storage = db.storage("media")
            file_name = f"campaign-{campaign_id[:8]}-{content_id[:8]}.png"
            file_meta = storage.upload_file(image_bytes, file_name, "image/png")
            file_id = file_meta.get("$id", "")
            image_url = storage.get_file_url(file_id)
            logger.info(f"[CAMPAIGN_IMG] Uploaded → {image_url}")
        except Exception as exc:
            logger.warning(f"Appwrite upload failed, falling back to disk: {exc}")
            try:
                from pathlib import Path as _Path
                media_dir = _Path(os.getcwd()) / "storage" / "media"
                media_dir.mkdir(parents=True, exist_ok=True)
                local_name = f"{uuid.uuid4().hex}.png"
                (_Path(media_dir) / local_name).write_bytes(image_bytes)
                image_url = f"https://api.contentstudio.thq.digital/media/{local_name}"
                logger.info(f"[CAMPAIGN_IMG] Image saved to disk (fallback): {local_name}")
            except Exception as disk_exc:
                logger.error(f"[CAMPAIGN_IMG] Disk fallback also failed: {disk_exc}")
                raise HTTPException(status_code=500, detail="Failed to save image to storage or disk")

        # ── 5. Save to content table with campaign_id (Content Library) ───────
        try:
            content_record = {
                "user_id":      user_id,
                "tenant_id":    tenant_id,
                "campaign_id":  campaign_id,
                "title":        "AI Image",
                "content":      image_url,
                "content_type": "image",
                "image_url":    image_url,
                "status":       "draft",
                "metadata": {
                    "source":      "campaign_ai_images",
                    "campaign_id": campaign_id,
                    "platform":    platform,
                    "prompt":      full_prompt[:300],
                },
            }
            if brand_id:
                content_record["brand_id"] = brand_id
            db.table("content").insert(content_record).execute()
        except Exception as exc:
            logger.warning(f"[CAMPAIGN_IMG] content table insert failed (non-fatal): {exc}")

        # ── 6. Update campaign_content.body = URL (no-regen on refresh) ───────
        try:
            _appwrite_db.update_document("campaign_content", content_id, {"body": image_url})
        except Exception as exc:
            logger.warning(f"[CAMPAIGN_IMG] campaign_content update failed (non-fatal): {exc}")

        return {"image_url": image_url, "file_id": file_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CAMPAIGN_IMG] Unhandled error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{campaign_id}/content/{content_id}", summary="Delete a campaign content asset",
    responses={
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def delete_campaign_content(
    campaign_id: str,
    content_id: str,
    db: AppwriteClient = Depends(get_db),
):
    """Delete a single campaign content asset."""
    user_id = "demo-user"
    try:
        camp = db.table("campaigns").select("id").eq("id", campaign_id).eq("user_id", user_id).execute()
        if not camp.data:
            raise HTTPException(status_code=404, detail="Campaign not found")

        _appwrite_db.delete_document("campaign_content", content_id)
        return {"message": "Content deleted", "content_id": content_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete campaign content error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════
# ONE-CLICK CONTENT SUITE  (PRD §5.2 + §5.4)
# ══════════════════════════════════════════════════════════════

@router.post(
    "/{campaign_id}/generate",
    summary="One-click: generate content suite for campaign (supports daily mode)"
,
    responses={
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def generate_campaign_content(
    campaign_id: str,
    request: Request,
    mode: str       = Query("auto", description="'auto' = daily if dates are set, else full (default) | 'daily' = per-day | 'full' = one set"),
    db: AppwriteClient = Depends(get_db),
):
    """
    **PRD §5.2 + §5.4 — One-click content suite**

    ### mode=auto  *(default)*
    Auto-detects based on campaign dates:
    - If `start_date` and `end_date` are set → **daily** mode (per-day content)
    - If no dates → **full** mode (one set per channel)

    ### mode=daily
    Loops through **every day** of the campaign (`start_date` → `end_date`) and generates
    a complete set of assets for each day.

    ### mode=full
    Generates **one set** of assets per channel — no per-day breakdown.

    **Phase progression (daily mode):**
    | Phase | Days | Focus |
    |-------|------|-------|
    | Awareness | First ⅓ | Introduce, tease, spark curiosity |
    | Value | Middle ⅓ | Benefits, insights, social proof |
    | CTA | Final ⅓ | Urgency, offer, convert |

    Every asset is saved as a **draft** with `scheduled_for` set to the correct campaign day.
    """
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    bearer = request.headers.get("Authorization", "") or None
    t0 = time.monotonic()
    try:
        camp_res = db.table("campaigns").select("*") \
                     .eq("id", campaign_id).eq("user_id", user_id).execute()
        if not camp_res.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        camp = camp_res.data[0]

        channels = camp.get("channels", [])
        if not channels:
            raise HTTPException(
                status_code=400,
                detail="Campaign has no channels. Update the campaign with channels first "
                       "(e.g. blog, twitter, linkedin, instagram, facebook, email)."
            )

        ctx            = _resolve_campaign_context(db, camp)
        brand_ctx      = ctx["brand_ctx"]
        objective      = ctx["objective"]
        audience       = ctx["audience"]
        cta            = ctx["cta"]
        tone           = ctx["tone"]
        budget_context = ctx.get("budget_context")   # e.g. "Campaign budget: $500 (lean...)"

        CHANNEL_MAP = {
            "blog":           ("blog",              "Blog Post"),
            "twitter":        ("tweet",             "Tweet"),
            "linkedin":       ("linkedin_post",     "LinkedIn Post"),
            "instagram":      ("instagram_caption", "Instagram Caption"),
            "facebook":       ("facebook_post",     "Facebook Post"),
            "email":          ("email",             "Email"),
            "tiktok":         ("tiktok",            "Short Reels"),
            "youtube_shorts": ("youtube_shorts",    "YT Shorts"),
            "youtube":        ("youtube",           "YouTube"),
            "pinterest":      ("pinterest",         "Pinterest"),
            "threads":        ("threads",           "Threads"),
            "snapchat":       ("snapchat",          "Snapchat"),
            "newsletter":     ("newsletter",        "Newsletter"),
            "podcast":        ("podcast",           "Podcast"),
            "webinar":        ("webinar",           "Webinar"),
            "sms":            ("sms",               "SMS"),
            "whatsapp":       ("whatsapp",          "WhatsApp"),
            "linkedin_article": ("linkedin_article", "LinkedIn Article"),
            "press_release":  ("press_release",     "Press Release"),
            "landing_page":   ("landing_page",      "Landing Page"),
            "google_ads":     ("google_ads",        "Google Ads"),
            "meta_ads":       ("meta_ads",          "Meta Ads"),
            "linkedin_ads":   ("linkedin_ads",      "LinkedIn Ads"),
        }

        # ── Determine mode: auto = daily if dates exist, else full ──
        has_dates = bool(camp.get("start_date") and camp.get("end_date"))
        effective_mode = mode
        if mode == "auto":
            effective_mode = "daily" if has_dates else "full"

        # ── DAILY MODE ─────────────────────────────────────────
        if effective_mode == "daily":

            # Resolve campaign dates
            def _parse_date(val):
                if not val:
                    return None
                try:
                    d = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
                    return d.replace(tzinfo=timezone.utc)
                except Exception:
                    return None

            start_dt = _parse_date(camp.get("start_date")) or datetime.now(timezone.utc)
            end_dt   = _parse_date(camp.get("end_date"))

            if not end_dt or end_dt <= start_dt:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Daily mode requires both `start_date` and `end_date` on the campaign. "
                        f"Got start={camp.get('start_date')}, end={camp.get('end_date')}."
                    )
                )

            # Build list of campaign days (date objects, start inclusive, end inclusive)
            campaign_days = []
            cursor = start_dt
            while cursor <= end_dt:
                campaign_days.append(cursor)
                cursor += timedelta(days=1)

            total_days = len(campaign_days)
            total_items = total_days * len(channels)
            logger.info(
                f"🗓️ Daily mode: {total_days} day(s) × {len(channels)} channels = "
                f"{total_items} assets for campaign '{camp['name']}'"
            )

            # ── LARGE CAMPAIGNS: Use intelligent pipeline (background task) ──
            # Threshold: 100+ items (dev mode: generate synchronously without Redis/Celery)
            if total_items >= 100:
                logger.info(f"📦 Large campaign detected ({total_items} items) — dispatching to background worker")

                # ── Redis/Celery available → dispatch to Celery (production) ──
                if settings.REDIS_URL:
                    task = generate_campaign_background.apply_async(
                        args=(
                            campaign_id,
                            channels,
                            total_days,
                            objective,
                            audience,
                            cta,
                            brand_ctx,
                            budget_context,
                            tenant_id,
                            user_id,
                        ),
                        kwargs={
                            "brand_id": camp.get("brand_id") or None,
                            "tone": tone,
                        },
                        queue="ai",
                    )
                    logger.info(f"✅ Campaign {campaign_id} dispatched to background worker with task_id: {task.id}")
                    return {
                        "campaign_id": campaign_id,
                        "campaign_name": camp["name"],
                        "mode": "daily_pipeline",
                        "message": "Large campaign queued for intelligent generation (background)",
                        "task_id": task.id,
                        "total_items": total_items,
                        "status": "queued",
                        "poll_url": f"/api/v1/tasks/{task.id}",
                    }

                # ── No Redis → run pipeline directly as asyncio background task ──
                logger.info(
                    f"ℹ️ Redis not configured — running campaign pipeline directly (job_id mode)"
                )
                pipeline_result = await campaign_pipeline.generate_campaign(
                    platforms=channels,
                    duration_days=total_days,
                    objective=objective,
                    audience=audience,
                    cta=cta,
                    brand_context=brand_ctx,
                    user_context=budget_context,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    campaign_id=campaign_id,
                    brand_id=camp.get("brand_id") or "",
                    tone=tone or "professional",
                )
                job_id = pipeline_result.get("job_id", "")
                logger.info(f"✅ Campaign {campaign_id} pipeline started with job_id: {job_id}")
                return {
                    "campaign_id": campaign_id,
                    "campaign_name": camp["name"],
                    "mode": "daily_pipeline",
                    "message": "Large campaign generation started",
                    "job_id": job_id,
                    "total_items": total_items,
                    "status": "in_progress",
                    "poll_url": f"/api/v1/campaigns/{job_id}/progress",
                }

            # ── SMALL CAMPAIGNS: Direct concurrent generation ─────────────────
            generated_by_day = {}
            total_ok = 0

            async def _gen_day_channel(day_idx: int, day_dt, channel: str):
                """Generate + save one asset for one day/channel combo, with retries."""
                user_id = "demo-user"
                tenant_id = "demo-tenant"
                day_label = day_dt.strftime("%Y-%m-%d")
                last_err = None
                for attempt in range(3):
                    try:
                        r = await asyncio.wait_for(
                            ai_service.generate_content_for_day(
                                channel=channel, objective=objective, audience=audience,
                                cta=cta, day_index=day_idx, total_days=total_days,
                                brand_context=brand_ctx, tone=tone,
                                user_context=budget_context,   # budget now reaches LLM
                                brand_id=camp.get("brand_id") or None,
                                tenant_id=tenant_id,
                                user_id=user_id,
                            ),
                            timeout=90.0,
                        )
                        content_type = CHANNEL_MAP.get(channel, (channel, ""))[0]
                        scheduled_for = day_dt.replace(hour=9, minute=0, second=0, microsecond=0)

                        # Save to unified 'campaign_content' collection (single source of truth)
                        saved = _appwrite_db.create_document("campaign_content", {
                            "campaign_id": campaign_id,
                            "tenant_id":   tenant_id,
                            "channel":     channel,
                            "content_type": content_type,
                            "title":       r["title"],
                            "body":        r["content"],
                            "phase":       r.get("phase", "Awareness"),
                            "scheduled_for": scheduled_for.isoformat(),
                            "status":      "draft",
                            "created_at":  datetime.utcnow().isoformat(),
                            **({"brand_id": camp.get("brand_id")} if camp.get("brand_id") else {}),                        })
                        _mirror_to_content_library({
                            "campaign_id": campaign_id,
                            "title":       r["title"],
                            "body":        r["content"],
                            "content_type": content_type,
                            "channel":     channel,
                            "brand_id":    camp.get("brand_id"),
                        }, user_id, tenant_id)

                        return (day_idx, day_label, {
                            "channel":       channel,
                            "content_id":    saved.get("id", ""),
                            "phase":         r["phase"],
                            "title":         r["title"],
                            "status":        "completed",
                            "scheduled_for": scheduled_for.date().isoformat(),
                            "content":       r["content"],
                        })
                    except asyncio.TimeoutError:
                        last_err = f"Timeout (attempt {attempt+1}/3)"
                        logger.warning(f"⏱️ Day {day_idx+1}/{channel} timed out — attempt {attempt+1}/3")
                    except Exception as ch_err:
                        last_err = f"{type(ch_err).__name__}: {ch_err}" if str(ch_err) else type(ch_err).__name__
                        logger.warning(f"⚠️ Day {day_idx+1}/{channel} failed (attempt {attempt+1}/3): {ch_err}")
                    await asyncio.sleep(1.5)
                return (day_idx, day_dt.strftime("%Y-%m-%d"), {"channel": channel, "error": last_err or "Generation failed after 3 attempts", "status": "failed"})

            # Build all tasks and run everything concurrently
            all_tasks = [
                _gen_day_channel(day_idx, day_dt, channel)
                for day_idx, day_dt in enumerate(campaign_days)
                for channel in channels
            ]
            logger.info(f"⚡ Daily mode: firing {len(all_tasks)} tasks concurrently")
            all_results = await asyncio.gather(*all_tasks)

            # Build per-platform progress stats
            platform_stats: Dict[str, Dict] = {}
            for ch in channels:
                platform_stats[ch] = {"total": total_days, "completed": 0, "failed": 0}

            for day_idx, day_label, asset in all_results:
                key = f"day_{day_idx + 1}_{day_label}"
                generated_by_day.setdefault(key, []).append(asset)
                ch = asset.get("channel", "")
                if asset.get("status") == "completed":
                    total_ok += 1
                    platform_stats[ch]["completed"] = platform_stats[ch].get("completed", 0) + 1
                else:
                    platform_stats[ch]["failed"] = platform_stats[ch].get("failed", 0) + 1

            logger.info(f"✅ Daily campaign suite: {total_ok}/{total_days * len(channels)} assets saved")
            duration_ms = int((time.monotonic() - t0) * 1000)
            return {
                "campaign_id":    campaign_id,
                "campaign_name":  camp["name"],
                "mode":           "daily",
                "start_date":     start_dt.date().isoformat(),
                "end_date":       end_dt.date().isoformat(),
                "total_days":     total_days,
                "channels":       channels,
                "total_requested": total_days * len(channels),
                "total_generated": total_ok,
                "total_failed":    total_days * len(channels) - total_ok,
                "platform_stats":  platform_stats,
                "days":           generated_by_day,
            }

        # ── FULL MODE — all channels generated CONCURRENTLY ─────────────────────
        # Uses generate_platform_native() for every channel — all 23 platforms
        # are supported, each gets its specialist persona, brand context, and
        # anti-repetition memory.  No more "Unsupported channel" failures.
        brand_id_for_gen = camp.get("brand_id") or None

        async def _gen_channel(channel: str):
            """Generate content for one channel with retry + timeout."""
            user_id = "demo-user"
            tenant_id = "demo-tenant"
            for attempt in range(3):
                try:
                    r = await asyncio.wait_for(
                        ai_service.generate_platform_native(
                            platform=channel,
                            topic=objective,
                            tone=tone,
                            brand_context=brand_ctx,
                            user_context=budget_context,   # budget now reaches LLM
                            brand_id=brand_id_for_gen,
                            tenant_id=tenant_id,
                            user_id=user_id,
                            custom_instructions=(
                                f"Campaign: {camp['name']}. "
                                f"Audience: {audience}. CTA: {cta}."
                            ),
                        ),
                        timeout=90.0,
                    )
                    content_text = r.get("content", "")

                    # Extract title/headline prefix if platform returns one
                    ch_label = CHANNEL_MAP.get(channel, (channel, channel.title()))[1]
                    title = f"{camp['name']} — {ch_label}"
                    lines = content_text.split("\n", 2)
                    for _pfx in ("title:", "headline:", "subject:"):
                        if lines and lines[0].lower().startswith(_pfx):
                            extracted = lines[0].split(":", 1)[1].strip()
                            if extracted:
                                title = extracted[:200]
                            content_text = "\n".join(lines[1:]).strip() or content_text
                            break

                    content_type = CHANNEL_MAP.get(channel, (channel, ""))[0]
                    saved = _appwrite_db.create_document("campaign_content", {
                        "campaign_id":  campaign_id,
                        "tenant_id":    tenant_id,
                        "channel":      channel,
                        "content_type": content_type,
                        "title":        title,
                        "body":         content_text,
                        "phase":        "Awareness",
                        "scheduled_for": None,
                        "status":       "draft",
                        "created_at":   datetime.utcnow().isoformat(),
                        **({"brand_id": brand_id_for_gen} if brand_id_for_gen else {}),                    })
                    _mirror_to_content_library({
                        "campaign_id":  campaign_id,
                        "title":        title,
                        "body":         content_text,
                        "content_type": content_type,
                        "channel":      channel,
                        "brand_id":     brand_id_for_gen,
                    }, user_id, tenant_id)

                    return {
                        "channel":    channel,
                        "content_id": saved.get("id", ""),
                        "title":      title,
                        "status":     "completed",
                        "content":    content_text,
                    }

                except asyncio.TimeoutError:
                    logger.warning(f"⏱️ {channel} timed out — attempt {attempt+1}/3")
                except Exception as ch_err:
                    logger.warning(f"⚠️ {channel} failed (attempt {attempt+1}/3): {ch_err}")
                await asyncio.sleep(1.5)
            return {"channel": channel, "status": "failed", "error": "Generation failed after 3 attempts"}

        # Fire all channels at the same time — total time = slowest channel, not sum of all
        logger.info(f"⚡ Generating {len(channels)} channels concurrently for campaign '{camp['name']}'")
        generated = await asyncio.gather(*[_gen_channel(ch) for ch in channels])
        generated = list(generated)

        ok_count = len([g for g in generated if "error" not in g])
        logger.info(f"✅ Campaign suite (full mode): {ok_count}/{len(channels)} assets generated")
        duration_ms = int((time.monotonic() - t0) * 1000)
        return {
            "campaign_id":      campaign_id,
            "campaign_name":    camp["name"],
            "mode":             "full",
            "total_requested":  len(channels),
            "total_generated":  ok_count,
            "assets":           generated,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("❌ generate_campaign_content")
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════
# EDITORIAL CALENDAR  (PRD §5.2)
# ══════════════════════════════════════════════════════════════
@router.get("/{campaign_id}/calendar", summary="Get editorial calendar for a campaign (PRD §5.2)",
    responses={
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def get_editorial_calendar(
    campaign_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AppwriteClient   = Depends(get_db),
):
    """
    **PRD §5.2 — Campaign-First Editorial Calendar**

    Returns all content assets grouped by scheduled date, giving you a
    visual calendar view of every piece of content planned for this campaign.

    Assets without a scheduled date appear in the `unscheduled` bucket.
    """
    try:
        camp_res = db.table("campaigns").select("*")\
                     .eq("id", campaign_id).eq("user_id", user_id).execute()
        if not camp_res.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        camp = camp_res.data[0]

        # campaign_content has no user_id — security already checked via campaign lookup above
        content_res = db.table("campaign_content").select("*")\
                        .eq("campaign_id", campaign_id)\
                        .order("created_at", desc=False).execute()
        items = content_res.data or []

        # Group by scheduled_for date (or fall back to created_at date)
        # campaign_content uses: body (not content), channel (not platform), scheduled_for top-level
        calendar: dict = {}
        unscheduled: list = []
        for item in items:
            scheduled = item.get("scheduled_for")   # top-level field in campaign_content
            if scheduled:
                date_key = str(scheduled)[:10]
                calendar.setdefault(date_key, []).append({
                    "id":           item["id"],
                    "title":        item.get("title"),
                    "channel":      item.get("channel"),
                    "content_type": item.get("content_type"),
                    "phase":        item.get("phase"),
                    "status":       item.get("status"),
                    "preview":      (item.get("body") or "")[:120],
                })
            else:
                unscheduled.append({
                    "id":           item["id"],
                    "title":        item.get("title"),
                    "channel":      item.get("channel"),
                    "content_type": item.get("content_type"),
                    "phase":        item.get("phase"),
                    "status":       item.get("status"),
                    "preview":      (item.get("body") or "")[:120],
                    "created_at":   item.get("created_at"),
                })

        # Build sorted calendar list
        sorted_calendar = [
            {"date": date_key, "assets": assets}
            for date_key, assets in sorted(calendar.items())
        ]

        return {
            "campaign_id":   campaign_id,
            "campaign_name": camp.get("name"),
            "status":        camp.get("status"),
            "start_date":    camp.get("start_date"),
            "end_date":      camp.get("end_date"),
            "total_assets":  len(items),
            "calendar":      sorted_calendar,
            "unscheduled":   unscheduled,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ editorial_calendar: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── CAMPAIGN STATS ────────────────────────────────────────────
@router.get("/{campaign_id}/stats", summary="Get content stats for a campaign",
    responses={
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def get_campaign_stats(
    campaign_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AppwriteClient   = Depends(get_db),
):
    """
    Returns content count by type and status for a campaign.
    Great for a quick campaign health check.
    """
    try:
        camp_res = db.table("campaigns").select("id, name, status")\
                     .eq("id", campaign_id).eq("user_id", user_id).execute()
        if not camp_res.data:
            raise HTTPException(status_code=404, detail="Campaign not found")

        # campaign content lives in campaign_content, not content
        # campaign_content has no user_id — security already checked via campaign lookup above
        content_res = db.table("campaign_content").select("channel, content_type, status, phase")\
                        .eq("campaign_id", campaign_id).limit(5000).execute()
        items = content_res.data or []

        by_channel: dict = {}
        by_status:  dict = {}
        by_phase:   dict = {}
        for item in items:
            ch = item.get("channel") or item.get("content_type", "unknown")
            st = item.get("status", "unknown")
            ph = item.get("phase", "unknown")
            by_channel[ch] = by_channel.get(ch, 0) + 1
            by_status[st]  = by_status.get(st, 0) + 1
            by_phase[ph]   = by_phase.get(ph, 0) + 1

        return {
            "campaign_id":     campaign_id,
            "campaign_name":   camp_res.data[0]["name"],
            "campaign_status": camp_res.data[0]["status"],
            "total_assets":    content_res.count,
            "by_channel":      by_channel,
            "by_status":       by_status,
            "by_phase":        by_phase,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════
# CONTENT MAP  (PRD §5.2 — editorial calendar view)
# ══════════════════════════════════════════════════════════════

@router.post("/{campaign_id}/content-map",
             summary="Generate strategic content map for campaign",
                 responses={
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
                 }
             )
async def generate_content_map(
    campaign_id: str,
    user_id: str    = Depends(get_current_user_id),
    tenant_id: str  = Depends(get_current_tenant_id),
    db: AppwriteClient = Depends(get_db),
):
    """
    **PRD §5.2 — Content Map / Editorial Calendar**

    Produces a strategic content map for the campaign:
    - Core narrative / key message
    - Key talking points
    - Channel-specific angle for each channel
    - Recommended publish sequence

    Use this **before** running `/generate` to plan the campaign strategy.
    Credit cost: 10 credits per map.
    """
    # ── Credit pre-check ────────────────────────────────────────────────────

    try:
        camp_res = db.table("campaigns").select("*")\
                     .eq("id", campaign_id).eq("user_id", user_id).execute()
        if not camp_res.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        camp = camp_res.data[0]
        ctx  = _resolve_campaign_context(db, camp)
        # Build user_context from budget + notes
        _map_user_ctx_parts = []
        if ctx.get("budget_context"):
            _map_user_ctx_parts.append(ctx["budget_context"])
        if ctx.get("notes"):
            _map_user_ctx_parts.append(f"Campaign notes: {ctx['notes']}")
        _map_user_ctx = "\n".join(_map_user_ctx_parts) or None

        result = await ai_service.generate_content_map(
            objective     = ctx["objective"],
            audience      = ctx["audience"],
            channels      = ctx["channels"],
            cta           = ctx["cta"],
            brand_context = ctx["brand_ctx"],
            tone          = ctx["tone"],
            user_context  = _map_user_ctx,
        )

        # ── Deduct credits after successful generation ───────────────────────
        try:
            pass
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"[CAMPAIGNS] content-map credit deduction failed (non-fatal): {e}")

        return {
            "campaign_id":   campaign_id,
            "campaign_name": camp["name"],
            "brand_name":    ctx.get("brand_name", ""),
            "tone":          ctx.get("tone", "professional"),
            **result,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════
# REPURPOSE ENGINE  (PRD §5.4)
# ══════════════════════════════════════════════════════════════

@router.post("/{campaign_id}/repurpose",
             summary="Repurpose one content asset into multiple channel formats",
                 responses={
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
                 }
             )
async def repurpose_content(
    campaign_id: str,
    source_content_id: str = Body(..., embed=True,
                                  description="content.id to repurpose"),
    target_channels:   List[str] = Body(..., embed=True,
                                        description="Channels: twitter | linkedin | instagram | facebook | email | blog"),
    user_id: str = Depends(get_current_user_id),
    tenant_id: str = Depends(get_current_tenant_id),
    db: AppwriteClient   = Depends(get_db),
):
    """
    **PRD §5.4 — Repurposing & Remix Engine**

    Take one content asset and generate channel-native versions of it automatically.

    Examples:
    - Blog post → LinkedIn post + Tweet + Email
    - LinkedIn post → Instagram caption + Facebook post
    - Any format → Any other format

    All repurposed assets are brand-aware and saved as drafts under this campaign.
    Credit cost: 6 credits per repurpose call.
    """
    # ── Credit pre-check ────────────────────────────────────────────────────

    try:
        # Validate campaign belongs to user
        camp_res = db.table("campaigns").select("brand_id")\
                     .eq("id", campaign_id).eq("user_id", user_id).execute()
        if not camp_res.data:
            raise HTTPException(status_code=404, detail="Campaign not found")

        # Fetch source content — try both with user_id filter and without (for mock fallback)
        src = db.table("content").select("*")\
                .eq("id", source_content_id).eq("user_id", user_id).execute()
        if not src.data:
            # Fallback: try querying by id only (in case user_id filter fails in mock mode)
            src = db.table("content").select("*")\
                    .eq("id", source_content_id).execute()
        if not src.data:
            raise HTTPException(status_code=404, detail="Source content not found")
        source = src.data[0]

        brand_ctx = _fetch_brand_context(db, camp_res.data[0].get("brand_id"))
        repurposed = []

        for channel in target_channels:
            try:
                r = await ai_service.repurpose_content(
                    original_content=source["content"],
                    original_title=source.get("title", "Content"),
                    target_channel=channel,
                    brand_context=brand_ctx,
                )
                # Save to unified campaign_content collection
                _camp_brand_id = camp_res.data[0].get("brand_id")
                saved = _appwrite_db.create_document("campaign_content", {
                    "campaign_id": campaign_id,
                    "tenant_id": tenant_id,
                    "channel": channel,
                    "content_type": channel,
                    "title": f"{source.get('title', 'Content')} [{channel.title()}]",
                    "body": r["content"],
                    "phase": "Repurposed",
                    "status": "draft",
                    "created_at": datetime.utcnow().isoformat(),
                    **( {"brand_id": _camp_brand_id} if _camp_brand_id else {} ),
                })
                _mirror_to_content_library({
                    "campaign_id":  campaign_id,
                    "title":        f"{source.get('title', 'Content')} [{channel.title()}]",
                    "body":         r["content"],
                    "content_type": channel,
                    "channel":      channel,
                    **( {"brand_id": _camp_brand_id} if _camp_brand_id else {} ),
                }, user_id, tenant_id)

                repurposed.append({
                    "channel":    channel,
                    "content_id": saved.get("id", ""),
                    "content":    r["content"],
                })
            except Exception as ch_err:
                logger.warning(f"⚠️ repurpose {channel}: {ch_err}")
                repurposed.append({"channel": channel, "error": str(ch_err)})

        # ── Deduct credits after successful repurpose ───────────────────────
        success_count = len([r for r in repurposed if "error" not in r])
        if success_count > 0:
            try:
                pass
            except HTTPException:
                raise
            except Exception as e:
                logger.warning(f"[CAMPAIGNS] repurpose credit deduction failed (non-fatal): {e}")

        return {
            "source_content_id": source_content_id,
            "source_title":      source.get("title"),
            "repurposed_count":  success_count,
            "repurposed_assets": repurposed,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════
# MULTI-WEEK SCHEDULE GENERATOR
# ══════════════════════════════════════════════════════════════

@router.post(
    "/{campaign_id}/generate-schedule",
    summary="Generate a full multi-week content schedule for a campaign"
,
    responses={
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def generate_campaign_schedule(
    campaign_id:              str,
    weeks:                    int       = Body(3,   description="Number of weeks for the campaign (e.g. 3)", ge=1, le=12, embed=True),
    posts_per_week_per_channel: int     = Body(3,   description="Posts per channel per week", ge=1, le=7, embed=True),
    tone:                     str       = Body("professional", description="Content tone", embed=True),
    user_id: str = Depends(get_current_user_id),
    tenant_id: str = Depends(get_current_tenant_id),
    db: AppwriteClient   = Depends(get_db),
):
    """
    **AI Multi-Week Campaign Planner**

    Pass in how many weeks (e.g. 3) and how many posts per channel per week (e.g. 3).
    The AI will:
    1. Plan the full editorial schedule for every channel in the campaign
    2. Generate ready-to-post content for each slot
    3. Save everything as **drafts** under this campaign with estimated publish dates

    The `scheduled_for` date is calculated from `campaign.start_date` (or today if not set).

    **Channels supported**: `blog`, `twitter`, `linkedin`, `instagram`, `facebook`, `email`
    """
    try:
        # Fetch campaign
        camp_res = db.table("campaigns").select("*") \
                     .eq("id", campaign_id).eq("user_id", user_id).execute()
        if not camp_res.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        camp = camp_res.data[0]

        channels = camp.get("channels", [])
        if not channels:
            raise HTTPException(
                status_code=400,
                detail="Campaign has no channels. Update the campaign with channels first."
            )

        ctx       = _resolve_campaign_context(db, camp)
        brand_ctx = ctx["brand_ctx"]
        objective = ctx["objective"]
        audience  = ctx["audience"]
        cta       = ctx["cta"]
        _sched_tone = ctx.get("tone") or tone  # campaign/brand tone overrides the body param default

        # Build user_context: budget + notes
        _sched_ctx_parts = []
        if ctx.get("budget_context"):
            _sched_ctx_parts.append(ctx["budget_context"])
        if ctx.get("notes"):
            _sched_ctx_parts.append(f"Campaign notes: {ctx['notes']}")
        _sched_user_ctx = "\n".join(_sched_ctx_parts) or None
        _sched_brand_id = camp.get("brand_id")

        # Determine campaign start date
        start_date = None
        if camp.get("start_date"):
            try:
                start_date = datetime.fromisoformat(str(camp["start_date"]).replace("Z", "+00:00"))
            except Exception:
                start_date = None
        if not start_date:
            start_date = datetime.now(timezone.utc)

        logger.info(f"🗓️ Generating {weeks}-week schedule for campaign '{camp['name']}' "
                    f"({len(channels)} channels, {posts_per_week_per_channel} posts/week/channel)")

        # Ask AI for the schedule — pass all campaign context including budget/notes
        schedule_data = await ai_service.generate_campaign_schedule(
            objective       = objective,
            audience        = audience,
            channels        = channels,
            cta             = cta,
            weeks           = weeks,
            posts_per_week  = posts_per_week_per_channel,
            brand_context   = brand_ctx,
            tone            = _sched_tone,
            user_context    = _sched_user_ctx,
        )

        schedule = schedule_data.get("schedule", [])

        # Map day names to day offsets within a week
        DAY_OFFSET = {
            "monday": 0, "tuesday": 1, "wednesday": 2,
            "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
        }

        # Channel → content_type mapping
        CHANNEL_TYPE = {
            "blog":      "blog",
            "twitter":   "tweet",
            "linkedin":  "linkedin_post",
            "instagram": "instagram_caption",
            "facebook":  "facebook_post",
            "email":     "email",
        }

        saved_posts = []
        errors      = []

        for item in schedule:
            try:
                week       = int(item.get("week", 1))
                channel    = str(item.get("channel", "")).lower()
                day_name   = str(item.get("day_of_week", "Monday")).lower()
                topic      = item.get("topic", "")
                content    = item.get("content", "")

                if not content:
                    continue

                # Calculate publish date: start + (week-1)*7 days + day offset
                day_offset     = DAY_OFFSET.get(day_name, 0)
                scheduled_for  = start_date + timedelta(weeks=week - 1, days=day_offset)

                content_type   = CHANNEL_TYPE.get(channel, channel)
                title          = f"[W{week}] {topic[:100]}"

                # Save to unified campaign_content collection — always include brand_id
                saved = _appwrite_db.create_document("campaign_content", {
                    "campaign_id": campaign_id,
                    "tenant_id": tenant_id,
                    "channel": channel,
                    "content_type": content_type,
                    "title": title,
                    "body": content,
                    "phase": "Scheduled",
                    "scheduled_for": scheduled_for.isoformat(),
                    "status": "draft",
                    "created_at": datetime.utcnow().isoformat(),
                    **( {"brand_id": _sched_brand_id} if _sched_brand_id else {} ),
                })
                _mirror_to_content_library({
                    "campaign_id":  campaign_id,
                    "title":        title,
                    "body":         content,
                    "content_type": content_type,
                    "channel":      channel,
                    **( {"brand_id": _sched_brand_id} if _sched_brand_id else {} ),
                }, user_id, tenant_id)

                saved_posts.append({
                    "content_id":   saved.get("id") if saved else None,
                    "week":         week,
                    "channel":      channel,
                    "day_of_week":  item.get("day_of_week", ""),
                    "topic":        topic,
                    "scheduled_for": scheduled_for.isoformat(),
                    "content":      content,
                })

            except Exception as item_err:
                logger.warning("⚠️ Failed to save schedule item: {}", item_err)
                errors.append({"item": item, "error": str(item_err)})

        logger.info(f"✅ Schedule generated: {len(saved_posts)} posts saved, {len(errors)} errors")

        # Build week-by-week summary for easy reading
        by_week: dict = {}
        for post in saved_posts:
            w = f"week_{post['week']}"
            by_week.setdefault(w, []).append(post)

        return {
            "campaign_id":      campaign_id,
            "campaign_name":    camp["name"],
            "weeks":            weeks,
            "channels":         channels,
            "total_generated":  len(saved_posts),
            "total_errors":     len(errors),
            "campaign_start":   start_date.isoformat(),
            "schedule_by_week": by_week,
            "errors":           errors if errors else None,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("❌ generate_campaign_schedule")
        raise HTTPException(status_code=500, detail=str(e))