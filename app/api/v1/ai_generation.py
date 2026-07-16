# app/api/v1/ai_generation.py
#
# Brand-intelligent text generation — powered by NVIDIA meta/llama-3.3-70b-instruct
#
# Credit flow (per THQ Developer Guide):
#   1. Pre-check balance  →  fail fast before AI call
#   2. Generate content   →  time it
#   3. Save to library    →  get result_id
#   4. Deduct credits     →  AFTER success, with result_id + duration_ms in metadata
#
# Routes:
#   POST /ai/generate/blog
#   POST /ai/generate/tweet
#   POST /ai/generate/email
#   POST /ai/generate/caption
#   POST /ai/generate/multi-platform
#   POST /ai/generate/headlines
#   POST /ai/generate/value-props
#   POST /ai/generate/newsletter
#   POST /ai/suggestions
#   GET  /ai/models

import time
from fastapi import APIRouter, HTTPException, Depends, Query, Request
from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum

from app.services.ai_service import ai_service
from app.services import brand_validator as _bv
from app.services.cache_service import cache, brand_key, brand_context_key
from app.services.platform_personas import list_all_platforms as _list_all_platforms
from app.core.database import get_db
from app.db.appwrite_client import AppwriteClient
from app.utils.logger import logger
from app.tasks.text_generation_tasks import generate_multi_platform_background

router = APIRouter(prefix="/ai", tags=["AI Content Generation"])

# Map platform names to ContentType enum values
CONTENT_TYPE_ALIASES = {
    "instagram":      "instagram_caption",
    "facebook":       "facebook_post",
    "twitter":        "tweet",
    "linkedin":       "linkedin_post",
    # youtube_shorts is a first-class content type — maps to itself so it is
    # preserved in the content library instead of being silently aliased to tiktok.
    "youtube_shorts": "youtube_shorts",
    "yt_shorts":      "youtube_shorts",
    # hook is a first-class content type replacing image_caption
    "hook":           "hook",
    # reel_script — dedicated reel script generation
    "reel_script":    "reel_script",
    # viral_intel — RAG-powered trend scout angles
    "viral_intel":    "viral_intel",
}

# ── Brand context helper ──────────────────────────────────────────────────────

def _resolve_brand(db: AppwriteClient, brand_id: Optional[str]) -> str:
    """
    Resolve brand_id to a rich brand context block (the umbrella).

    Uses brand_validator.build_brand_block to build the full brand context block.
    Caches result for 1h to cut DB load.
    """
    if not brand_id:
        return ""

    # Hot path: cached brand-context block
    cached = cache.get(brand_context_key(brand_id))
    if isinstance(cached, str) and cached:
        return cached

    try:
        res = db.table("brand_profiles").select("*").eq("id", brand_id).execute()
        if not res.data:
            return ""
        b = res.data[0]
    except Exception:
        return ""

    # Use the new rich builder
    block = _bv.build_brand_block(b)

    if block:
        cache.set(brand_context_key(brand_id), block, ttl=3600)
    return block


def _resolve_brand_raw(db: AppwriteClient, brand_id: Optional[str]) -> dict:
    """Return the raw brand dict (used to pull default tone/audience/cta)."""
    if not brand_id:
        return {}
    try:
        cached = cache.get(brand_key(brand_id))
        if isinstance(cached, dict):
            return cached
        res = db.table("brand_profiles").select("*").eq("id", brand_id).execute()
        if not res.data:
            return {}
        b = res.data[0]
        cache.set(brand_key(brand_id), b, ttl=3600)
        return b
    except Exception:
        return {}



def _resolve_brand_meta(db: AppwriteClient, brand_id: Optional[str]) -> dict:
    """Return brand metadata (id, tenant_id, completeness score) for memory/cost hooks."""
    if not brand_id:
        return {}
    try:
        res = db.table("brand_profiles").select("*").eq("id", brand_id).execute()
        if not res.data:
            return {}
        b = res.data[0]
        score = _bv.score_completeness(b)
        return {"brand_id": brand_id, "tenant_id": b.get("tenant_id", ""), "completeness": score}
    except Exception:
        return {}


def _resolve_default_brand_id(db: AppwriteClient, user_id: str) -> Optional[str]:
    """Return the user's default brand_id, if any. Cheap, cached helper."""
    if not user_id:
        return None
    try:
        from app.services.cache_service import cache, default_brand_key
        cached = cache.get(default_brand_key(user_id))
        if cached:
            return cached if isinstance(cached, str) else None
        res = (
            db.table("brand_profiles")
              .select("id")
              .eq("user_id", user_id)
              .eq("is_default", True)
              .limit(1)
              .execute()
        )
        if res.data:
            bid = res.data[0].get("id")
            if bid:
                cache.set(default_brand_key(user_id), bid, ttl=3600)
                return bid
    except Exception:
        pass
    return None


def _save_content(
    db: AppwriteClient,
    user_id: str,
    tenant_id: str,
    brand_id: Optional[str],
    title: str,
    content: str,
    content_type: str,
    metadata: dict,
    platform: Optional[str] = None,
) -> Optional[str]:
    """
    Save generated content to the content library.

    Always writes:
      • updated_at  (ISO timestamp — keeps the DB column in sync, not just $updatedAt)
      • platform    (the channel name, parallel to content_type)
      • brand_id    (falls back to the user's default brand if not supplied)
      • tenant_id, user_id, title, content, content_type, status='draft', metadata
      • quality_score (extracted from metadata.validation if present)

    Returns the new content_id or None.
    """
    from datetime import datetime, timezone

    try:
        # Fallback to the user's default brand if caller didn't pass one
        effective_brand_id = brand_id or _resolve_default_brand_id(db, user_id)

        # Resolve a sensible platform value — caller-provided wins, else content_type
        plat = (platform or content_type or "").strip().lower()
        if plat in ("", "content", "post"):
            plat = (content_type or "").strip().lower()

        now_iso = datetime.now(timezone.utc).isoformat()

        payload = {
            "user_id":      user_id,
            "tenant_id":    tenant_id,
            "title":        title,
            "content":      content,
            "content_type": content_type,
            "status":       "draft",
            "metadata":     metadata,
            "updated_at":   now_iso,
            "platform":     plat,
        }
        if effective_brand_id:
            payload["brand_id"] = effective_brand_id

        # Extract quality_score from metadata.validation when present
        if isinstance(metadata, dict) and "validation" in metadata:
            validation = metadata.get("validation", {})
            if isinstance(validation, dict):
                payload["quality_score"] = validation.get("overall_quality_score", "fair")

        saved = db.table("content").insert(payload).execute()
        return saved.data[0]["id"] if saved.data else None
    except Exception as e:
        import traceback as _tb
        logger.error(
            f"[_save_content] FAILED for user={user_id} type={content_type} "
            f"title={title!r}\n  error={type(e).__name__}: {e}\n"
            + _tb.format_exc()
        )
        return None


# ── Enums ─────────────────────────────────────────────────────────────────────


class HeadlineStyleEnum(str, Enum):
    mixed      = "mixed"
    question   = "question"
    number     = "number"
    how_to     = "how-to"
    power_word = "power-word"
    curiosity  = "curiosity"


# ── Request models ────────────────────────────────────────────────────────────

class MultiPlatformRequest(BaseModel):
    platforms:           List[str]
    topic:               Optional[str] = None
    include_hashtags:    bool = True
    include_emojis:      bool = True
    brand_id:            Optional[str] = None
    custom_instructions: Optional[str] = None
    user_context:        Optional[str] = None
    # Explicit overrides — take priority over brand defaults when provided
    tone:     Optional[str] = None
    audience: Optional[str] = None
    cta:      Optional[str] = None
    keywords: Optional[List[str]] = None


class ContentSuggestionsRequest(BaseModel):
    count:        int = Field(5, ge=1, le=20)
    brand_id:     Optional[str] = None
    user_context: Optional[str] = None


class GenerateHeadlinesRequest(BaseModel):
    topic:               str
    count:               int = Field(10, ge=1, le=30)
    style:               HeadlineStyleEnum = HeadlineStyleEnum.mixed
    brand_id:            Optional[str] = None
    custom_instructions: Optional[str] = None
    user_context:        Optional[str] = None


class GenerateValuePropsRequest(BaseModel):
    product_name:        str
    product_description: str
    count:               int = Field(5, ge=1, le=10)
    differentiators:     Optional[List[str]] = None
    brand_id:            Optional[str] = None
    custom_instructions: Optional[str] = None
    user_context:        Optional[str] = None


class GenerateNewsletterRequest(BaseModel):
    subject:             str
    sections:            List[str] = Field(
                             default=["Main story", "Key insight", "CTA"],
                             description="Ordered list of section titles to include"
                         )
    word_count:          int = Field(600, ge=200, le=2000)
    brand_id:            Optional[str] = None
    custom_instructions: Optional[str] = None
    user_context:        Optional[str] = None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/generate/multi-platform", summary="Generate content for multiple platforms — brand-aware",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def generate_multi_platform(
    req: MultiPlatformRequest,
    request: Request,
    save: bool = Query(True, description="Save each platform variant to content library"),
    background: bool = Query(False, description="Generate asynchronously in background (non-blocking)"),
    db: AppwriteClient = Depends(get_db),
):
    user_id = "demo-user"
    tenant_id = "demo-tenant"

    from app.config import settings

    # ── BACKGROUND MODE: Dispatch to Celery (non-blocking) ──────────────────
    # (only if Redis is configured; otherwise fall through to synchronous mode)
    if background and settings.REDIS_URL:
        logger.info(
            f"📦 Multi-platform generation dispatched to background: "
            f"platforms={len(req.platforms)}, topic={req.topic[:40] if req.topic else 'N/A'}"
        )

        brand_ctx = _resolve_brand(db, req.brand_id)
        _b = _resolve_brand_raw(db, req.brand_id)
        effective_tone = _b.get("tone") or "casual"

        # Dispatch to Celery background task (non-blocking)
        task = generate_multi_platform_background.apply_async(
            args=(
                req.platforms,
                req.topic,
                req.include_hashtags,
                req.include_emojis,
                brand_ctx,
                req.user_context,
                req.custom_instructions,
                tenant_id,
                user_id,
            ),
            kwargs={
                "brand_id": req.brand_id,
                "effective_tone": effective_tone,
                "save": save,
            },
            queue="ai",
        )

        logger.info(
            f"✅ Multi-platform generation dispatched with task_id: {task.id}"
        )

        return {
            "task_id": task.id,
            "status": "queued",
            "message": f"Generating content for {len(req.platforms)} platform(s) in background",
            "platforms": req.platforms,
            "platform_count": len(req.platforms),
            "poll_url": f"/api/v1/tasks/{task.id}",
        }

    # ── SYNCHRONOUS MODE: Generate immediately (backward compatible) ────────
    t0 = time.monotonic()
    brand_ctx = _resolve_brand(db, req.brand_id)
    _b = _resolve_brand_raw(db, req.brand_id)
    effective_tone = _b.get("tone") or "casual"
    try:
        result = await ai_service.generate_multi_platform(
            platforms=req.platforms, topic=req.topic, tone=effective_tone,
            include_hashtags=req.include_hashtags, include_emojis=req.include_emojis,
            custom_instructions=req.custom_instructions,
            brand_context=brand_ctx, user_context=req.user_context,
            brand_id=req.brand_id, tenant_id=tenant_id, user_id=user_id,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    duration_ms = int((time.monotonic() - t0) * 1000)

    # Human-readable title prefix — shared with generate-native path
    _MULTI_TITLE_LABELS: dict = {
        "youtube_shorts":    "YT Shorts",
        "yt_shorts":         "YT Shorts",
        "tiktok":            "Instagram Reel",
        "instagram":         "Instagram Post",
        "instagram_caption": "Instagram Post",
        "facebook":          "Facebook Post",
        "facebook_post":     "Facebook Post",
        "linkedin":          "LinkedIn Post",
        "linkedin_post":     "LinkedIn Post",
        "tweet":             "Tweet",
        "twitter":           "Tweet",
        "blog":              "Blog Post",
        "email":             "Email",
        "youtube":           "YouTube",
        "podcast":           "Podcast",
        "newsletter":        "Newsletter",
        "press_release":     "Press Release",
        "linkedin_article":  "LinkedIn Article",
        "landing_page":      "Landing Page",
        "webinar":           "Webinar",
        "whatsapp":          "WhatsApp",
        "threads":           "Threads",
        "snapchat":          "Snapchat",
        "sms":               "SMS",
        "pinterest":         "Pinterest",
        "google_ads":        "Google Ads",
        "meta_ads":          "Meta Ads",
        "linkedin_ads":      "LinkedIn Ads",
    }

    content_ids: dict = {}
    if save:
        for item in (result.get("results") or []):
            if isinstance(item, dict) and not item.get("error"):
                platform = item.get("platform", "unknown")
                text     = item.get("content", "")
                content_type = CONTENT_TYPE_ALIASES.get(platform, platform)
                title_label  = _MULTI_TITLE_LABELS.get(platform, platform.replace("_", " ").title())
                cid      = _save_content(
                    db, user_id, tenant_id, req.brand_id,
                    title=f"{title_label}: {(req.topic or 'Multi-platform')[:50]}",
                    content=text,
                    content_type=content_type,
                    metadata=item.get("metadata", {"platform": platform}),
                    platform=platform,
                )
                if cid:
                    content_ids[platform] = cid

    return {**result, "saved": save, "content_ids": content_ids}


@router.post("/generate/headlines", summary="Generate high-converting headlines — brand-aware",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def generate_headlines(
    req: GenerateHeadlinesRequest,
    request: Request,
    save: bool = Query(True, description="Save to content library"),
    db: AppwriteClient = Depends(get_db),
):
    """
    Generate a list of conversion-optimised headlines for a topic.
    Brand voice, vocabulary, and audience are applied automatically when brand_id is set.

    Styles:
    - **mixed**: variety of question, number, how-to, power-word, and curiosity headlines
    - **question**: compelling questions the reader must answer
    - **number**: listicle style (7 Ways…, 12 Mistakes…)
    - **how-to**: outcome-focused how-to statements
    - **power-word**: opens with Proven / Secret / Exact / Surprising etc.
    - **curiosity**: curiosity-gap — tease without revealing
    """
    user_id = "demo-user"
    tenant_id = "demo-tenant"

    t0 = time.monotonic()
    brand_ctx = _resolve_brand(db, req.brand_id)
    _b = _resolve_brand_raw(db, req.brand_id)
    effective_tone = _b.get("tone") or "professional"
    try:
        result = await ai_service.generate_headlines(
            topic=req.topic, count=req.count, style=req.style.value,
            tone=effective_tone, brand_context=brand_ctx,
            user_context=req.user_context, custom_instructions=req.custom_instructions,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    duration_ms = int((time.monotonic() - t0) * 1000)

    content_id = None
    if save:
        content_id = _save_content(
            db, user_id, tenant_id, req.brand_id,
            title=f"Headlines: {req.topic[:60]}",
            content="\n".join(result.get("headlines", [])),
            content_type="headlines",
            metadata=result.get("metadata", {}),
        )

    return {**result, "saved": save, "content_id": content_id}


@router.post("/generate/value-props", summary="Generate value propositions — brand-aware",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def generate_value_props(
    req: GenerateValuePropsRequest,
    request: Request,
    save: bool = Query(True, description="Save to content library"),
    db: AppwriteClient = Depends(get_db),
):
    """
    Generate structured value propositions, each with:
    - **Headline**: core promise (under 12 words)
    - **Subheadline**: expands on the promise (15-25 words)
    - **Proof**: specific stat, outcome, or differentiator

    Brand intelligence ensures every prop speaks to the brand's actual audience
    using the right tone and vocabulary.
    """
    user_id = "demo-user"
    tenant_id = "demo-tenant"

    t0 = time.monotonic()
    brand_ctx = _resolve_brand(db, req.brand_id)
    _b = _resolve_brand_raw(db, req.brand_id)
    effective_tone     = _b.get("tone") or "professional"
    effective_audience = _b.get("target_audience") or "general audience"
    try:
        result = await ai_service.generate_value_props(
            product_name=req.product_name,
            product_description=req.product_description,
            target_audience=effective_audience,
            count=req.count, tone=effective_tone,
            differentiators=req.differentiators,
            brand_context=brand_ctx, user_context=req.user_context,
            custom_instructions=req.custom_instructions,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    duration_ms = int((time.monotonic() - t0) * 1000)

    content_id = None
    if save:
        vp_text = "\n\n".join(
            f"Headline: {vp.get('headline', '')}\nSubheadline: {vp.get('subheadline', '')}\nProof: {vp.get('proof', '')}"
            for vp in result.get("value_props", [])
        )
        content_id = _save_content(
            db, user_id, tenant_id, req.brand_id,
            title=f"Value Props: {req.product_name}",
            content=vp_text,
            content_type="value_props",
            metadata=result.get("metadata", {}),
        )

    return {**result, "saved": save, "content_id": content_id}


@router.post("/generate/newsletter", summary="Generate email newsletter — brand-aware",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def generate_newsletter(
    req: GenerateNewsletterRequest,
    request: Request,
    save: bool = Query(True, description="Save to content library"),
    db: AppwriteClient = Depends(get_db),
):
    """
    Generate a complete newsletter issue:
    - Subject line and 50-char preview text (optimised for open rates)
    - Structured body following the sections you specify
    - On-brand sign-off and CTA

    Reads the brand's voice, audience, vocabulary, and approved CTAs automatically
    when brand_id is provided.
    """
    user_id = "demo-user"
    tenant_id = "demo-tenant"

    t0 = time.monotonic()
    brand_ctx = _resolve_brand(db, req.brand_id)
    _b = _resolve_brand_raw(db, req.brand_id)
    effective_tone     = _b.get("tone") or "professional"
    effective_audience = _b.get("target_audience")
    effective_cta      = (_b.get("cta_examples") or [None])[0]
    try:
        result = await ai_service.generate_newsletter(
            subject=req.subject, sections=req.sections, tone=effective_tone,
            word_count=req.word_count, audience=effective_audience, cta=effective_cta,
            brand_context=brand_ctx, user_context=req.user_context,
            custom_instructions=req.custom_instructions,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    duration_ms = int((time.monotonic() - t0) * 1000)

    content_id = None
    if save:
        full_text = (
            f"Subject: {result.get('subject', '')}\n"
            f"Preview: {result.get('preview', '')}\n\n"
            f"{result.get('body', '')}"
        )
        content_id = _save_content(
            db, user_id, tenant_id, req.brand_id,
            title=f"Newsletter: {req.subject}",
            content=full_text,
            content_type="newsletter",
            metadata=result.get("metadata", {}),
        )

    return {**result, "saved": save, "content_id": content_id}


@router.post("/suggestions", summary="Get AI content ideas based on brand context",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def content_suggestions(
    req: ContentSuggestionsRequest,
    request: Request,
    db: AppwriteClient = Depends(get_db),
):
    user_id = "demo-user"
    tenant_id = "demo-tenant"

    t0 = time.monotonic()
    brand_ctx = _resolve_brand(db, req.brand_id)
    try:
        result = await ai_service.generate_content_suggestions(
            count=req.count,
            brand_context=brand_ctx, user_context=req.user_context,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    duration_ms = int((time.monotonic() - t0) * 1000)

    return result




@router.get("/models", summary="List available AI models")
async def list_models():
    return {
        "llm": {
            "model":       "nvidia/llama-3.3-nemotron-super-49b-v1",
            "provider":    "NVIDIA NIM",
            "endpoint":    "https://integrate.api.nvidia.com/v1",
            "used_for":    "blog, tweet, email, caption, multi-platform, headlines, value-props, newsletter, suggestions",
            "brand_aware": True,
        },
        "image": {
            "model":       "black-forest-labs/FLUX.2-klein-4B",
            "provider":    "NVIDIA NIM",
            "endpoint":    "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.2-klein-4b",
            "used_for":    "POST /media/generate/image",
            "constraints": "Width/height must be multiples of 16 (512-1408). Max area 1,062,400 px².",
        },
        "carousel": {
            "provider":    "Gamma AI",
            "used_for":    "POST /media/generate/social",
            "description": "High-quality social media carousels with professional design themes",
        },
        "campaign": {
            "provider":    "NVIDIA LLM (brand-aware)",
            "used_for":    "POST /campaigns/{id}/generate  |  POST /campaigns/{id}/content-map",
        },
    }


# ════════════════════════════════════════════════════════════════════════════
# ──── PLATFORM-NATIVE GENERATION (the world-class endpoint) ──────────────────
# ════════════════════════════════════════════════════════════════════════════
class PlatformNativeRequest(BaseModel):
    platform: str = Field(..., description="Target platform — twitter, linkedin, tiktok, sms, email, blog, hook, reel_script, etc.")
    topic:    str = Field(..., min_length=2, description="What to write about")
    brand_id: Optional[str] = Field(None, description="Brand to apply (umbrella). If omitted, default brand is used.")
    custom_instructions: Optional[str] = None
    user_context:        Optional[str] = None
    max_regens:          int = Field(1, ge=0, le=3, description="Max regenerations on constraint or repetition violation")
    word_count:          Optional[int] = Field(None, ge=50, le=5000, description="Target word count for long-form content (blog, landing_page, newsletter, etc.)")
    # Explicit overrides — take priority over brand defaults when provided
    tone:      Optional[str] = Field(None, description="Override brand tone for this generation (e.g. 'casual', 'bold', 'authoritative')")
    audience:  Optional[str] = Field(None, description="Override target audience for this generation")
    cta:       Optional[str] = Field(None, description="Override call-to-action for this generation")
    keywords:  Optional[List[str]] = Field(None, description="Keywords to weave into the content")
    # Hook Generator + Reel Script Studio optional psychology fields
    trigger:       Optional[str] = Field(None, description="Psychological trigger: fear / curiosity / controversy / ego / desire")
    delivery_tone: Optional[str] = Field(None, description="Delivery tone: provocative / authoritative / insider_leak / confessional")
    # X Thread Builder format selector
    x_format:      Optional[str] = Field(None, description="X/Twitter format: power_hook / thread / ratio_bait / stat_drop / power_list / quote_tweet_bait")


@router.get("/platforms", summary="List every platform we natively generate for")
async def list_platforms():
    """
    Returns the 24+ platforms we natively support, with specialist personas,
    voice directives, structure rules, hard constraints, and angle pools.

    Use this to populate the platform selector in the frontend with the full,
    accurate list — not the legacy 6-platform set.
    """
    return {"platforms": _list_all_platforms(), "total": len(_list_all_platforms())}


@router.post("/generate-native", summary="World-class platform-native generation",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def generate_native(
    req: PlatformNativeRequest,
    request: Request,
    db: AppwriteClient = Depends(get_db),
):
    """
    Generate content with full world-class treatment:
      • Platform-specific specialist persona (24+ platforms supported)
      • Brand-context injection (the umbrella)
      • Anti-repetition memory (fresh angle every time, never repeats)
      • Hard constraint validation (SMS ≤160, Tweet ≤280, etc.) + auto-regen
      • Per-tenant cost metering + daily quota enforcement
      • Credit deduction integrated with existing billing system

    Credit cost: same as post_generation (6 credits) by default.
    """
    user_id = "demo-user"
    tenant_id = "demo-tenant"

    # ── 2. Resolve brand context ──────────────────────────────────────────
    effective_brand_id = req.brand_id
    if not effective_brand_id:
        # Try default brand for user
        try:
            res = (
                db.table("brand_profiles")
                  .select("id")
                  .eq("user_id", user_id)
                  .eq("is_default", True)
                  .limit(1)
                  .execute()
            )
            if res.data:
                effective_brand_id = res.data[0].get("id")
        except Exception:
            pass

    brand_ctx = _resolve_brand(db, effective_brand_id) if effective_brand_id else ""
    _b        = _resolve_brand_raw(db, effective_brand_id) if effective_brand_id else {}

    # Tone: request override > brand delivery_tone > brand tone > professional
    eff_tone = (
        req.tone
        or _b.get("delivery_tone") or ""
        or _b.get("tone") or ""
        or "professional"
    )

    # Build enriched custom_instructions — merge explicit request fields + user's ci
    _ci_parts: list[str] = []
    if req.custom_instructions:
        _ci_parts.append(req.custom_instructions)
    if req.audience:
        _ci_parts.append(f"Target audience: {req.audience}")
    if req.cta:
        _ci_parts.append(f"Call to action: {req.cta}")
    if req.keywords:
        _ci_parts.append(f"Weave in these keywords naturally: {', '.join(req.keywords)}")
    _merged_ci = " | ".join(_ci_parts) or None

    # ── 3. Generate (platform-native or hook) ────────────────────────────
    t0 = time.monotonic()
    try:
        if req.platform.lower() in ("tweet", "twitter") and req.x_format:
            # X Thread Builder — format-specific generation
            result = await ai_service.generate_x_thread(
                topic=req.topic,
                x_format=req.x_format,
                brand_context=brand_ctx,
                audience=req.audience,
                cta=req.cta,
                brand_id=effective_brand_id,
                tenant_id=tenant_id,
            )
        elif req.platform.lower() == "hook":
            # Hook Generator — dedicated multi-hook generation
            result = await ai_service.generate_hooks(
                topic=req.topic,
                brand_context=brand_ctx,
                trigger=req.trigger,
                audience=req.audience,
                cta=req.cta,
                brand_id=effective_brand_id,
                tenant_id=tenant_id,
            )
        elif req.platform.lower() == "reel_script":
            # Reel Script Studio — 60-second structured reel script
            result = await ai_service.generate_reel_script(
                topic=req.topic,
                brand_context=brand_ctx,
                trigger=req.trigger,
                delivery_tone=req.delivery_tone,
                audience=req.audience,
                cta=req.cta,
                brand_id=effective_brand_id,
                tenant_id=tenant_id,
            )
        else:
            result = await ai_service.generate_platform_native(
                platform=req.platform,
                topic=req.topic,
                tone=eff_tone,
                brand_context=brand_ctx,
                user_context=req.user_context,
                custom_instructions=_merged_ci,
                brand_id=effective_brand_id,
                tenant_id=tenant_id,
                user_id=user_id,
                max_regens=req.max_regens,
                word_count=req.word_count,
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GEN-NATIVE] failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    duration_ms = int((time.monotonic() - t0) * 1000)
    content_text = result.get("content", "")

    # Use the *requested* platform for content_type — never the AI metadata.
    # The AI internally uses an alias (e.g. "tiktok" persona for "youtube_shorts")
    # and may echo that alias back in metadata, which would silently mis-classify
    # the saved content.
    req_platform = req.platform.lower()
    content_type = CONTENT_TYPE_ALIASES.get(req_platform, req_platform)

    # Human-readable title prefix for each platform
    _TITLE_LABELS: dict = {
        "youtube_shorts":    "YT Shorts",
        "yt_shorts":         "YT Shorts",
        "tiktok":            "Instagram Reel",
        "instagram":         "Instagram Post",
        "instagram_caption": "Instagram Post",
        "facebook":          "Facebook Post",
        "facebook_post":     "Facebook Post",
        "linkedin":          "LinkedIn Post",
        "linkedin_post":     "LinkedIn Post",
        "tweet":             "Tweet",
        "twitter":           "Tweet",
        "blog":              "Blog Post",
        "email":             "Email",
        "youtube":           "YouTube",
        "podcast":           "Podcast",
        "newsletter":        "Newsletter",
        "press_release":     "Press Release",
        "linkedin_article":  "LinkedIn Article",
        "landing_page":      "Landing Page",
        "webinar":           "Webinar",
        "whatsapp":          "WhatsApp",
        "threads":           "Threads",
        "snapchat":          "Snapchat",
        "sms":               "SMS",
        "pinterest":         "Pinterest",
        "google_ads":        "Google Ads",
        "meta_ads":          "Meta Ads",
        "linkedin_ads":      "LinkedIn Ads",
        "hook":              "Hook",
        "reel_script":       "Reel Script",
        "viral_intel":       "Viral Intel",
    }
    title_prefix = _TITLE_LABELS.get(req_platform, req_platform.replace("_", " ").title())

    # ── 4. Save to content library ────────────────────────────────────────
    content_id = _save_content(
        db=db,
        user_id=user_id,
        tenant_id=tenant_id,
        brand_id=effective_brand_id,
        title=f"{title_prefix}: {req.topic[:80]}",
        content=content_text,
        content_type=content_type,
        metadata={
            "validation":  result.get("metadata", {}).get("quality_report", {}),
            "angle":       result.get("metadata", {}).get("angle_used"),
            "duration_ms": duration_ms,
            "model_tier":  result.get("metadata", {}).get("model_tier"),
        },
        platform=req_platform,
    )


    return {
        "content":   content_text,
        "metadata":  result.get("metadata", {}),
        "platform":  req_platform,
        "content_id": content_id,
        "duration_ms": duration_ms,
    }


