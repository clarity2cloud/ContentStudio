# app/api/v1/brand.py
"""
Brand Intelligence — the Umbrella

Endpoints to create, list, get, update, delete brand profiles.
  • Persist knowledge fields (brand_story, goals) in Appwrite
  • Return brand completeness score + next-best-action hint in responses
  • Cache brand profiles for hot-path performance
  • Enforce tenant_id on every query (defense in depth)
"""

from fastapi import APIRouter, HTTPException, Depends
from typing import List, Dict, Any, Annotated

from app.models.brand import (
    BrandProfileCreate,
    BrandProfileUpdate,
    BrandProfileResponse,
)
from app.core.database import get_db
from app.core.dependencies import get_current_user_id, get_current_tenant_id
from app.db.appwrite_client import AppwriteClient
from app.utils.logger import logger
from app.services import brand_validator
from app.services.cache_service import (
    cache,
    brand_key,
    default_brand_key,
    invalidate_brand,
)
from app.middleware.tenant_isolation import apply_tenant_filter, assert_owner

router = APIRouter(prefix="/brands", tags=["Brand Intelligence"])


# ── Field normalization helpers ─────────────────────────────────────────────

def _to_storage(data: Dict[str, Any]) -> Dict[str, Any]:
    """Pass-through — all fields are stored natively in Appwrite."""
    return dict(data or {})


def _from_storage(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce missing/None list fields to safe empty defaults."""
    out = dict(doc or {})
    for f in ("vocabulary", "avoid_words", "cta_examples", "goals"):
        if out.get(f) is None:
            out[f] = []
    return out


def _attach_score(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Add completeness_score, tier, and next-best action to a brand doc."""
    rep = brand_validator.score_completeness(doc)
    doc["completeness_score"] = rep["score"]
    doc["completeness_tier"]  = rep["tier"]
    doc["next_best_action"]   = rep.get("next_best")
    return doc


def _model_dump_with_subschemas(model) -> Dict[str, Any]:
    """Pydantic model to dict — flattens nested Pydantic models."""
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_none=True)
    return model.dict(exclude_none=True)  # pydantic v1 fallback


# ──────────────────────────────────────────────────────────────
# OPTIONS for frontend dropdowns
# ──────────────────────────────────────────────────────────────
@router.get("/options", summary="Get all available dropdown options for brand creation")
async def get_brand_options() -> Dict[str, Any]:
    """Predefined option lists for brand-profile dropdowns. Fields still accept free-text."""
    return {
        "industries": [
            "Technology", "Healthcare", "Finance", "Retail", "Education", "Real Estate",
            "Hospitality", "Manufacturing", "Media & Entertainment", "Nonprofit",
            "Consulting", "E-commerce", "SaaS", "Agency", "Other",
        ],
        "tones": [
            "professional", "casual", "friendly", "authoritative", "inspirational",
            "humorous", "empathetic", "formal", "conversational", "bold",
            "minimalist", "luxurious", "data_driven", "storytelling", "urgency",
            "educational",
        ],
        "voices": [
            "thought leader", "educator", "storyteller", "advisor", "innovator",
            "rebel", "supporter", "expert", "enthusiast", "visionary",
        ],
        "positionings": [
            "premium quality", "affordable", "innovative", "customer-centric",
            "eco-friendly", "luxury", "cutting-edge", "reliable", "trusted",
            "disruptive",
        ],
        "target_audiences": [
            "small business owners", "marketing professionals", "tech enthusiasts",
            "general consumers", "enterprise decision makers", "millennials",
            "gen z", "parents", "students", "freelancers", "startup founders",
            "corporate executives",
        ],
        "vocabulary_suggestions": [
            "innovative", "scalable", "seamless", "empower", "transform",
            "cutting-edge", "user-friendly", "next-gen", "proven", "award-winning",
        ],
        "avoid_words_suggestions": [
            "cheap", "complicated", "old", "outdated", "difficult", "broken",
            "hard", "maybe", "try", "hope",
        ],
        "cta_examples_suggestions": [
            "Sign up for free", "Get started today", "Book a demo", "Download now",
            "Learn more", "Join the waitlist", "Claim your spot",
            "Start your journey", "See it in action", "Get early access",
        ],
        "content_pillar_suggestions": [
            "Education", "Behind the scenes", "Customer stories", "Product updates",
            "Industry insights", "Founder voice", "Team culture", "Tutorials",
            "Data & research", "Frameworks",
        ],
        "persona_template": {
            "name": "VP of Marketing",
            "pain_points": ["fragmented tooling", "no single source of truth"],
            "goals": ["10x content velocity", "consistent brand voice"],
            "language_style": "tactical, data-driven",
        },
        "competitor_template": {
            "name": "Acme Co.",
            "what_they_do": "category leader for X",
            "what_we_do_differently": "focus on Y use case",
        },
        "example_post_template": {
            "platform": "linkedin",
            "content": "...",
            "performance": "1.2K reactions",
            "why_it_worked": "specific personal story + framework",
        },
    }


# ──────────────────────────────────────────────────────────────
# CREATE
# ──────────────────────────────────────────────────────────────
@router.post("", response_model=BrandProfileResponse, status_code=201, summary="Create brand profile",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def create_brand(
    brand: BrandProfileCreate,
    user_id: str = Depends(get_current_user_id),
    tenant_id: str = Depends(get_current_tenant_id),
    db: AppwriteClient = Depends(get_db),
):
    """
    Create a brand profile — the umbrella under which all content generation flows.
    """
    try:
        if brand.is_default:
            q = db.table("brand_profiles").update({"is_default": False}).eq("user_id", user_id)
            q = apply_tenant_filter(q, tenant_id)
            q.execute()

        payload = _model_dump_with_subschemas(brand)
        payload["user_id"]   = user_id
        payload["tenant_id"] = tenant_id

        score_info = brand_validator.score_completeness(payload)
        storage_payload = _to_storage(payload)
        storage_payload["completeness_score"] = score_info["score"]
        result = db.table("brand_profiles").insert(storage_payload).execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create brand profile")

        doc = _from_storage(result.data[0])
        doc = _attach_score(doc)

        # Invalidate caches
        if user_id:
            cache.delete(default_brand_key(user_id))

        logger.info(f"✅ Brand '{brand.name}' created for user {user_id} (score={doc['completeness_score']})")
        return BrandProfileResponse(**doc)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ create_brand: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────
# LIST
# ──────────────────────────────────────────────────────────────
@router.get("", response_model=List[BrandProfileResponse], summary="List all brand profiles",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def list_brands(
    user_id: str = Depends(get_current_user_id),
    tenant_id: str = Depends(get_current_tenant_id),
    db: AppwriteClient = Depends(get_db),
):
    """Return all brand profiles for the current user. Default brand listed first."""
    try:
        q = db.table("brand_profiles").select("*").eq("user_id", user_id)
        q = apply_tenant_filter(q, tenant_id)
        result = q.order("created_at", desc=True).limit(200).execute()

        brands = result.data or []
        brands = sorted(brands, key=lambda b: (not b.get("is_default", False)))
        responses = []
        for b in brands:
            b = _from_storage(b)
            b = _attach_score(b)
            responses.append(BrandProfileResponse(**b))
        return responses
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────
# GET
# ──────────────────────────────────────────────────────────────
@router.get("/{brand_id}", response_model=BrandProfileResponse, summary="Get a brand profile",
    responses={
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def get_brand(
    brand_id: str,
    user_id: str = Depends(get_current_user_id),
    tenant_id: str = Depends(get_current_tenant_id),
    db: AppwriteClient = Depends(get_db),
):
    try:
        # Try cache first (full doc with score)
        cached = cache.get(brand_key(brand_id))
        if cached and isinstance(cached, dict):
            assert_owner(cached, user_id, tenant_id)
            return BrandProfileResponse(**_attach_score(_from_storage(cached)))

        q = db.table("brand_profiles").select("*").eq("id", brand_id).eq("user_id", user_id)
        q = apply_tenant_filter(q, tenant_id)
        result = q.execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Brand profile not found")

        doc = result.data[0]
        assert_owner(doc, user_id, tenant_id)
        cache.set(brand_key(brand_id), doc, ttl=3600)  # 1h cache
        doc = _from_storage(doc)
        doc = _attach_score(doc)
        return BrandProfileResponse(**doc)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────
# GET — completeness score (lightweight)
# ──────────────────────────────────────────────────────────────
@router.get("/{brand_id}/score", summary="Brand profile completeness score + next best action",
    responses={
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def get_brand_score(
    brand_id: str,
    user_id: str = Depends(get_current_user_id),
    tenant_id: str = Depends(get_current_tenant_id),
    db: AppwriteClient = Depends(get_db),
):
    try:
        q = db.table("brand_profiles").select("*").eq("id", brand_id).eq("user_id", user_id)
        q = apply_tenant_filter(q, tenant_id)
        result = q.execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Brand profile not found")
        doc = _from_storage(result.data[0])
        assert_owner(doc, user_id, tenant_id)
        return brand_validator.score_completeness(doc)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────
# UPDATE
# ──────────────────────────────────────────────────────────────
@router.put("/{brand_id}", response_model=BrandProfileResponse, summary="Update brand profile",
    responses={
                400: {"description": "Bad request"},
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def update_brand(
    brand_id: str,
    brand: BrandProfileUpdate,
    user_id: str = Depends(get_current_user_id),
    tenant_id: str = Depends(get_current_tenant_id),
    db: AppwriteClient = Depends(get_db),
):
    """Update any field on a brand profile. All campaigns linked to this brand pick up changes immediately."""
    try:
        q = db.table("brand_profiles").select("*").eq("id", brand_id).eq("user_id", user_id)
        q = apply_tenant_filter(q, tenant_id)
        existing = q.execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Brand profile not found")
        assert_owner(existing.data[0], user_id, tenant_id)

        if brand.is_default:
            qq = db.table("brand_profiles").update({"is_default": False}).eq("user_id", user_id)
            qq = apply_tenant_filter(qq, tenant_id)
            qq.execute()

        upd = _model_dump_with_subschemas(brand)
        if not upd:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Recompute completeness score from merged data and persist it
        merged = {**existing.data[0], **upd}
        score_info = brand_validator.score_completeness(merged)
        upd["completeness_score"] = score_info["score"]

        storage_upd = _to_storage(upd)
        result = db.table("brand_profiles").update(storage_upd).eq("id", brand_id).execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="Update failed")

        invalidate_brand(brand_id, user_id)

        doc = _from_storage(result.data[0])
        doc = _attach_score(doc)
        return BrandProfileResponse(**doc)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────
# CASCADE DELETE HELPERS
# ──────────────────────────────────────────────────────────────

def _batch_delete_by_brand(db: AppwriteClient, collection: str, brand_id: str) -> int:
    """
    Delete ALL documents in `collection` where brand_id matches.
    Appwrite's delete helper is capped at 100 docs per call, so we loop
    until nothing remains.  Returns the total number of documents deleted.
    """
    from app.db.appwrite_client import AppwriteDB
    adb = AppwriteDB()
    total = 0
    while True:
        res = db.table(collection).select("*").eq("brand_id", brand_id).limit(100).execute()
        docs = res.data or []
        if not docs:
            break
        for doc in docs:
            try:
                adb.delete_document(collection, doc["id"])
                total += 1
            except Exception as exc:
                logger.warning(f"[cascade] could not delete {collection}/{doc.get('id')}: {exc}")
    return total


def _extract_appwrite_file_id(image_url: str) -> str | None:
    """
    Parse the Appwrite file ID out of a storage URL.
    URL format: .../storage/buckets/media/files/{FILE_ID}/view?project=...
    Returns None if the URL is not an Appwrite storage URL.
    """
    try:
        if "/files/" not in image_url:
            return None
        part = image_url.split("/files/")[1]
        file_id = part.split("/")[0].split("?")[0]
        return file_id or None
    except Exception:
        return None


def _cascade_delete_brand(db: AppwriteClient, brand_id: str) -> dict:
    """
    Permanently delete ALL data associated with a brand:
      • Content (text, hooks, reel scripts, viral intel, carousels, images)
      • Appwrite file-storage objects referenced by image_url
      • Campaign content
      • Campaigns
      • Scheduled posts
      • Posting queues
      • Templates
      • Generation history

    Non-fatal: every collection is attempted independently so a failure in
    one collection never blocks the others.  Errors are collected and returned.
    """
    summary: dict = {
        "content_deleted":            0,
        "files_deleted":              0,
        "campaign_content_deleted":   0,
        "campaigns_deleted":          0,
        "scheduled_posts_deleted":    0,
        "posting_queues_deleted":     0,
        "templates_deleted":          0,
        "generation_history_deleted": 0,
        "errors":                     [],
    }

    storage = db.storage("media")

    # ── 1. Content (text, images, carousels) ──────────────────────────────
    try:
        # Collect ALL content records to extract file IDs before deleting
        file_ids: list[str] = []
        offset = 0
        while True:
            res = (
                db.table("content")
                  .select("*")
                  .eq("brand_id", brand_id)
                  .limit(100)
            )
            res._offset_val = offset
            batch = res.execute().data or []
            if not batch:
                break
            for item in batch:
                url = item.get("image_url") or ""
                fid = _extract_appwrite_file_id(url)
                if fid:
                    file_ids.append(fid)
            offset += len(batch)
            if len(batch) < 100:
                break

        # Delete Appwrite storage files first (so URLs are still valid)
        for fid in file_ids:
            try:
                storage.delete_file(fid)
                summary["files_deleted"] += 1
            except Exception as exc:
                summary["errors"].append(f"storage file {fid}: {exc}")

        # Delete content documents
        summary["content_deleted"] = _batch_delete_by_brand(db, "content", brand_id)

    except Exception as exc:
        summary["errors"].append(f"content: {exc}")

    # ── 2. Campaign content ────────────────────────────────────────────────
    try:
        summary["campaign_content_deleted"] = _batch_delete_by_brand(db, "campaign_content", brand_id)
    except Exception as exc:
        summary["errors"].append(f"campaign_content: {exc}")

    # ── 3. Campaigns ───────────────────────────────────────────────────────
    try:
        summary["campaigns_deleted"] = _batch_delete_by_brand(db, "campaigns", brand_id)
    except Exception as exc:
        summary["errors"].append(f"campaigns: {exc}")

    # ── 4. Scheduled posts ─────────────────────────────────────────────────
    try:
        summary["scheduled_posts_deleted"] = _batch_delete_by_brand(db, "scheduled_posts", brand_id)
    except Exception as exc:
        summary["errors"].append(f"scheduled_posts: {exc}")

    # ── 5. Posting queues ──────────────────────────────────────────────────
    try:
        summary["posting_queues_deleted"] = _batch_delete_by_brand(db, "posting_queues", brand_id)
    except Exception as exc:
        summary["errors"].append(f"posting_queues: {exc}")

    # ── 6. Templates ───────────────────────────────────────────────────────
    try:
        summary["templates_deleted"] = _batch_delete_by_brand(db, "templates", brand_id)
    except Exception as exc:
        summary["errors"].append(f"templates: {exc}")

    # ── 7. Generation history ──────────────────────────────────────────────
    try:
        summary["generation_history_deleted"] = _batch_delete_by_brand(db, "generation_history", brand_id)
    except Exception as exc:
        summary["errors"].append(f"generation_history: {exc}")

    return summary


# ──────────────────────────────────────────────────────────────
# DELETE
# ──────────────────────────────────────────────────────────────
@router.delete("/{brand_id}", summary="Delete brand profile and ALL associated data",
    responses={
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def delete_brand(
    brand_id:  str,
    user_id: str = Depends(get_current_user_id),
    tenant_id: str = Depends(get_current_tenant_id),
    db: AppwriteClient = Depends(get_db),
):
    """
    Hard delete a brand and everything it owns:
    content, images, carousels, campaigns, campaign content,
    scheduled posts, posting queues, templates, generation history.
    """
    try:
        q = db.table("brand_profiles").select("*").eq("id", brand_id).eq("user_id", user_id)
        q = apply_tenant_filter(q, tenant_id)
        existing = q.execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Brand profile not found")
        assert_owner(existing.data[0], user_id, tenant_id)

        # 1. Cascade — delete all child data first
        cascade = _cascade_delete_brand(db, brand_id)
        logger.info(
            f"[brand.delete] cascade complete for {brand_id}: "
            f"content={cascade['content_deleted']} files={cascade['files_deleted']} "
            f"campaigns={cascade['campaigns_deleted']} errors={len(cascade['errors'])}"
        )
        if cascade["errors"]:
            logger.warning(f"[brand.delete] non-fatal cascade errors: {cascade['errors']}")

        # 2. Delete the brand profile itself
        db.table("brand_profiles").delete().eq("id", brand_id).execute()
        invalidate_brand(brand_id, user_id)

        return {
            "message":  "Brand and all associated data permanently deleted",
            "brand_id": brand_id,
            "deleted":  cascade,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────
# SET DEFAULT
# ──────────────────────────────────────────────────────────────
@router.post("/{brand_id}/set-default", response_model=BrandProfileResponse, summary="Set as default brand",
    responses={
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def set_default_brand(
    brand_id:  str,


    db: AppwriteClient = Depends(get_db),
):
    """
    Make this brand the default — used whenever no brand_id is passed
    to AI generation or campaign endpoints.
    """
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    try:
        q = db.table("brand_profiles").select("*").eq("id", brand_id).eq("user_id", user_id)
        q = apply_tenant_filter(q, tenant_id)
        existing = q.execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Brand profile not found")
        assert_owner(existing.data[0], user_id, tenant_id)

        qq = db.table("brand_profiles").update({"is_default": False}).eq("user_id", user_id)
        qq = apply_tenant_filter(qq, tenant_id)
        qq.execute()

        result = db.table("brand_profiles").update({"is_default": True}).eq("id", brand_id).execute()

        invalidate_brand(brand_id, user_id)

        doc = _from_storage(result.data[0])
        doc = _attach_score(doc)
        return BrandProfileResponse(**doc)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
