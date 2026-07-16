# app/api/v1/media.py
import os
from fastapi import APIRouter, HTTPException, Depends, Body, Query, Request
from fastapi.responses import StreamingResponse, Response
from typing import Optional, List, Dict, Any
import io
import json
import re
import httpx
import asyncio
import zipfile
from enum import Enum
import base64
import uuid as _uuid
from pathlib import Path as _Path
from app.services.ai_service import ai_service
from app.services.modal_service import modal_service  # Module-level import
from app.core.database import get_db

import time as _time
from app.db.appwrite_client import AppwriteClient
from app.services.gamma_service import gamma_service
from app.utils.logger import logger
from app.utils.ssrf_guard import safe_get


router = APIRouter(prefix="/media", tags=["Media Generation"])


# ── Image proxy — no CORS, no auth needed ─────────────────────────────────────
@router.get("/image-proxy/{file_id}", summary="Proxy an Appwrite Storage image through the backend",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def proxy_image(file_id: str):
    """
    Fetches an image from the Appwrite Storage 'media' bucket using the server-side
    API key and re-serves it with Access-Control-Allow-Origin: * so the frontend
    can use it in <img> tags and fetch() calls without hitting CORS restrictions.
    Responses are cached for 7 days.
    """
    from app.db.appwrite_client import FileStorage
    storage = FileStorage("media")
    url = storage.get_file_url(file_id)
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url, headers=storage._headers())
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="Image not found in storage")
        content_type = resp.headers.get("content-type", "image/png")
        return Response(
            content=resp.content,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=604800, immutable",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET",
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Image proxy error: {exc}")


# ─────────────────────────────────────────────────────────────
# NVIDIA FLUX.2-klein-4B — exact supported resolutions (from model card)
# ─────────────────────────────────────────────────────────────
_FLUX_RESOLUTIONS: list[tuple[int, int]] = [
    (672, 1568), (688, 1504), (720, 1456), (752, 1392),
    (800, 1328), (832, 1248), (880, 1184), (944, 1104),
    (1024, 1024),
    (1104, 944), (1184, 880), (1248, 832), (1328, 800),
    (1392, 752), (1456, 720), (1504, 688), (1568, 672),
]

def _snap_to_nvidia(w: int, h: int) -> tuple[int, int]:
    """Pick the supported FLUX resolution closest to the requested w×h."""
    return min(_FLUX_RESOLUTIONS, key=lambda r: abs(r[0] - w) + abs(r[1] - h))



# ─────────────────────────────────────────────────────────────
# Platform presets — all values are NVIDIA-valid
# (multiples of 16, total ≤ 1,062,400)
# ─────────────────────────────────────────────────────────────
_PLATFORM_PRESETS: Dict[str, tuple[int, int]] = {
    "instagram":          (1024, 1024),  # 1:1 square
    "instagram_portrait": (800,  1328),  # 4:5 portrait (closest valid)
    "instagram_story":    (720,  1456),  # 9:16 story (closest valid)
    "facebook":           (1328,  800),  # 1.91:1 landscape (closest valid)
    "facebook_story":     (720,  1456),  # 9:16 story
    "linkedin":           (1328,  800),  # 16:9 post (closest valid)
    "linkedin_portrait":  (800,  1328),  # 4:5 portrait
    "twitter":           (1328,  800),   # 16:9 (closest valid)
    "twitter_square":    (1024, 1024),   # 1:1
    "youtube_thumbnail": (1328,  800),   # 16:9 (closest valid)
    "pinterest":         (800,  1328),   # 2:3 portrait (closest valid)
    "tiktok":            (720,  1456),   # 9:16 (closest valid)
    "blog_hero":         (1328,  800),   # 16:9 (closest valid)
    "square_small":      (1024, 1024),   # 1:1 (768x768 not in valid list)
}

# ─────────────────────────────────────────────────────────────
# Enums for frontend dropdowns
# ─────────────────────────────────────────────────────────────

class ImagePlatformPreset(str, Enum):
    INSTAGRAM           = "instagram"
    INSTAGRAM_PORTRAIT  = "instagram_portrait"
    INSTAGRAM_STORY     = "instagram_story"
    FACEBOOK            = "facebook"
    FACEBOOK_STORY      = "facebook_story"
    LINKEDIN            = "linkedin"
    LINKEDIN_PORTRAIT   = "linkedin_portrait"
    TWITTER             = "twitter"
    TWITTER_SQUARE      = "twitter_square"
    YOUTUBE_THUMBNAIL   = "youtube_thumbnail"
    PINTEREST           = "pinterest"
    TIKTOK              = "tiktok"
    BLOG_HERO           = "blog_hero"
    SQUARE_SMALL        = "square_small"
    CUSTOM              = "custom"   # use width/height params directly

class ImageStyleSuggestion(str, Enum):
    PHOTOREALISTIC      = "photorealistic, hyperrealistic, 8K, sharp focus"
    CINEMATIC           = "cinematic photography, dramatic lighting, film grain"
    DIGITAL_ART         = "digital art, concept art, highly detailed"
    ILLUSTRATION        = "professional illustration, vector style, clean lines"
    WATERCOLOR          = "watercolor painting, soft colours, artistic"
    OIL_PAINTING        = "oil painting, textured canvas, classical style"
    FLAT_DESIGN         = "flat design, minimalist, geometric shapes, clean"
    NEON_CYBERPUNK      = "neon cyberpunk, glowing lights, futuristic city"
    CORPORATE_CLEAN     = "corporate stock photo style, clean, professional"
    EDITORIAL_MAGAZINE  = "editorial magazine photo, high-fashion, studio lighting"
    ABSTRACT_GEOMETRIC  = "abstract geometric art, bold shapes, gradient"
    VINTAGE_RETRO       = "vintage retro photo, film aesthetic, warm tones"
    ANIME               = "anime style, vibrant colours, Studio Ghibli inspired"
    SKETCH              = "pencil sketch, hand-drawn, artistic"

class CarouselPlatform(str, Enum):
    INSTAGRAM           = "instagram"
    INSTAGRAM_STORY     = "instagram_story"
    LINKEDIN            = "linkedin"
    FACEBOOK            = "facebook"
    TIKTOK              = "tiktok"
    TWITTER             = "twitter"
    PINTEREST           = "pinterest"

class CarouselDesignStyle(str, Enum):
    # Modern / Bold
    BOLD_VIBRANT        = "bold, vibrant, high-contrast modern design"
    DARK_LUXURY         = "dark luxury, black background, gold accents, premium feel"
    NEON_ELECTRIC       = "neon electric, dark background, glowing neon accents"
    # Clean / Corporate
    MINIMAL_CLEAN       = "minimal, clean white space, elegant typography"
    CORPORATE_BLUE      = "corporate professional, navy and white, business-grade"
    TECH_SLEEK          = "tech sleek, gradient blues, dark mode, modern sans-serif"
    # Creative / Artistic
    COLORFUL_GRADIENT   = "vibrant gradient, colourful, Gen-Z aesthetic, trendy"
    PASTEL_SOFT         = "pastel soft tones, friendly, approachable, feminine"
    EARTHY_ORGANIC      = "earthy organic, natural tones, green and beige, lifestyle"
    RETRO_VINTAGE       = "retro vintage, 70s/80s colours, nostalgic typography"
    # Industry-specific
    STARTUP_MODERN      = "startup SaaS, product screenshot style, clean UI mockups"
    FASHION_EDITORIAL   = "fashion editorial, bold imagery, magazine-quality layouts"
    HEALTH_WELLNESS     = "health wellness, light and airy, calming, clean"
    FOOD_PHOTOGRAPHY    = "food photography style, warm tones, appetising close-ups"
    PLAYFUL_FUN         = "playful, fun, illustrated, cartoonish, engaging"

class CarouselTheme(str, Enum):
    # Gamma built-in themes
    HORIZON             = "Horizon"
    BONAN_HALE          = "Bonan Hale"
    KEEPSAKE            = "Keepsake"
    SPECTRUM            = "Spectrum"
    SERENE              = "Serene"
    DAWN                = "Dawn"
    PEACH               = "Peach"
    MIDNIGHT            = "Midnight"
    FOREST              = "Forest"
    OCEAN               = "Ocean"
    # Style-based
    BOLD_MONO           = "Bold Mono"
    SOFT_PASTEL         = "Soft Pastel"
    DARK_MODE           = "Dark Mode"
    EDITORIAL           = "Editorial"
    MINIMAL_WHITE       = "Minimal White"

class CarouselTextDensity(str, Enum):
    ONE_LINER   = "one_liner"    # ≤10 words per slide
    BRIEF       = "brief"        # 1-2 sentences per slide
    MEDIUM      = "medium"       # 2-4 sentences per slide
    DETAILED    = "detailed"     # paragraphs; educational content

class CarouselSlideCount(int, Enum):
    THREE    = 3
    FOUR     = 4
    FIVE     = 5
    SIX      = 6
    SEVEN    = 7
    EIGHT    = 8
    NINE     = 9
    TEN      = 10

class CarouselImageSource(str, Enum):
    AI_GENERATED  = "aiGenerated"   # NVIDIA/DALL-E generated images
    WEB_SEARCH    = "webSearch"     # Gamma web-search images
    STOCK_PHOTO   = "stockPhoto"    # Stock photography
    NONE          = "none"          # Text-only / illustration

class CarouselDimension(str, Enum):
    SQUARE_1X1          = "1x1"      # 1080x1080 Instagram square
    PORTRAIT_4X5        = "4x5"      # 1080x1350 Instagram portrait
    STORY_9X16          = "9x16"     # 1080x1920 Instagram/TikTok story
    LANDSCAPE_16X9      = "16x9"     # 1920x1080 YouTube / LinkedIn
    LANDSCAPE_1_91X1    = "1.91x1"   # 1200x628 Facebook OG / LinkedIn banner
    SQUARE_SMALL        = "social_portrait"  # Generic social

class CarouselExportFormat(str, Enum):
    PNG_ZIP     = "png"          # ZIP of PNG slides (best for Instagram)
    PDF         = "pdf"          # Single PDF (good for LinkedIn docs)
    PPTX        = "pptx"         # PowerPoint (editable)

class CarouselArtStyle(str, Enum):
    PHOTOGRAPHY         = "Photo"
    ILLUSTRATION        = "Illustration"
    THREE_D             = "3D"
    ABSTRACT            = "Abstract"
    LINE_ART            = "Line Art"
    WATERCOLOR          = "Watercolor"
    FLAT_ICON           = "Flat Icon"
    FUTURISTIC          = "Futuristic"

class CarouselTone(str, Enum):
    PROFESSIONAL        = "Professional"
    EDUCATIONAL         = "Educational"
    CONVERSATIONAL      = "Conversational"
    INSPIRATIONAL       = "Inspirational"
    AUTHORITATIVE       = "Authoritative"
    HUMOROUS            = "Humorous"
    URGENT              = "Urgent"
    STORYTELLING        = "Storytelling"

class CarouselAudience(str, Enum):
    BUSINESS_PROFESSIONALS  = "Business professionals"
    MARKETING_TEAMS         = "Marketing teams"
    FOUNDERS_STARTUPS       = "Founders & startups"
    CONTENT_CREATORS        = "Content creators"
    TECH_ENTHUSIASTS        = "Tech enthusiasts"
    GENERAL_PUBLIC          = "General public"
    STUDENTS                = "Students"
    HIGH_SCHOOLERS          = "High schoolers"
    HEALTH_WELLNESS         = "Health & wellness"
    FASHION_LIFESTYLE       = "Fashion & lifestyle"
    FINANCE_INVESTORS       = "Finance & investors"

# Legacy compatibility aliases
class PlatformSuggestion(str, Enum):
    INSTAGRAM = "instagram"
    LINKEDIN  = "linkedin"
    FACEBOOK  = "facebook"
    TWITTER   = "twitter"

class DesignStyleSuggestion(str, Enum):
    BOLD_VIBRANT    = "bold, vibrant, modern"
    MINIMAL_ELEGANT = "minimal, elegant"
    CORPORATE_CLEAN = "corporate clean"
    PLAYFUL_FUN     = "playful, fun"
    LUXURIOUS       = "luxurious, premium"

class TextAmountSuggestion(str, Enum):
    BRIEF    = "brief"
    MEDIUM   = "medium"
    DETAILED = "detailed"

class ExportFormatSuggestion(str, Enum):
    PDF          = "pdf"
    PPTX         = "pptx"
    GOOGLE_SLIDES = "google_slides"
    PNG          = "png"




# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
def _handle_gamma_error(e: Exception):
    error_str = str(e)
    logger.error(f"Gamma AI request failed: {error_str}")
    if "Gamma API error" in error_str:
        match = re.search(r'\{.*\}', error_str)
        if match:
            try:
                error_json = json.loads(match.group())
                status = error_json.get("statusCode", 400)
                raise HTTPException(
                    status_code=status,
                    detail="Gamma AI rejected the request. Check your input parameters and try again.",
                )
            except json.JSONDecodeError:
                pass
    raise HTTPException(
        status_code=500,
        detail="Gamma AI request failed. If this persists, try again later.",
    )



async def _poll_generation_completion(
    generation_id: str,
    max_attempts: int = 40,
    base_delay: float = 1.0,
    max_delay: float = 8.0,
) -> Dict[str, Any]:
    """
    Poll Gamma generation status with exponential backoff and jitter.
    """
    import random
    delay = base_delay
    for attempt in range(1, max_attempts + 1):
        data = await gamma_service.get_generation(generation_id)
        status = data.get("status")
        if status == "completed":
            return data
        elif status in ("failed", "error"):
            err = data.get("error", "Unknown error")
            logger.error(f"Gamma generation {generation_id} failed: {err}")
            raise HTTPException(
                status_code=500,
                detail=f"Gamma generation failed after {attempt} attempts: {err}",
            )
        # Exponential backoff with jitter
        jitter = random.uniform(0, delay * 0.3)
        wait = min(delay + jitter, max_delay)
        logger.debug(f"Gamma poll attempt {attempt}/{max_attempts}, waiting {wait:.1f}s")
        await asyncio.sleep(wait)
        delay = min(delay * 2, max_delay)
    raise HTTPException(
        status_code=408,
        detail=f"Gamma generation timed out after {max_attempts} polling attempts. "
               f"Check status later via GET /media/status/{generation_id}",
    )



# ─────────────────────────────────────────────────────────────
# OPTIONS  — dropdown values for frontend
# ─────────────────────────────────────────────────────────────
@router.get("/options", summary="Get all dropdown options for image & carousel generation")
async def get_media_options() -> Dict[str, Any]:
    """
    Returns all valid options for image and carousel generation forms.
    Use these to populate dropdowns in the frontend — every value here is
    guaranteed to work with the generation APIs.
    """
    return {
        # ── Image generation ─────────────────────────────────────────
        "image": {
            "platform_presets": {k: {"width": v[0], "height": v[1]} for k, v in _PLATFORM_PRESETS.items()},
            "styles":   [i.value for i in ImageStyleSuggestion],
            "note":     "Use platform_presets to get valid width/height pairs automatically.",
        },
        # ── Carousel generation ──────────────────────────────────────
        "carousel": {
            "platforms":     [i.value for i in CarouselPlatform],
            "design_styles": [i.value for i in CarouselDesignStyle],
            "themes":        [i.value for i in CarouselTheme],
            "text_density":  [i.value for i in CarouselTextDensity],
            "slide_counts":  [i.value for i in CarouselSlideCount],
            "image_sources": [i.value for i in CarouselImageSource],
            "dimensions":    [i.value for i in CarouselDimension],
            "export_formats":[i.value for i in CarouselExportFormat],
            "art_styles":    [i.value for i in CarouselArtStyle],
            "tones":         [i.value for i in CarouselTone],
            "audiences":     [i.value for i in CarouselAudience],
        },
    }




# ─────────────────────────────────────────────────────────────
# IMAGE GENERATION  — POST /media/generate/image
# Uses AI image generation service
# POST {"prompt": "...", "width": W, "height": H}
# ← {"image_base64": "<PNG base64>"}
# ─────────────────────────────────────────────────────────────
@router.post("/generate/image", summary="Generate an AI image and download it",
    responses={
                500: {"description": "Internal server error"},
                502: {"description": "Bad gateway"}
    }
)
async def generate_image(
    prompt: str = Body(..., description="Describe the image you want — be specific about subject, setting, mood, lighting"),
    style: Optional[str] = Body(
        None,
        description=(
            "Visual style. Use values from GET /media/options → image.styles. "
            "Examples: 'photorealistic, hyperrealistic, 8K, sharp focus' | 'cinematic photography, dramatic lighting' | "
            "'digital art, concept art, highly detailed'"
        ),
    ),
    platform: Optional[str] = Body(
        None,
        description=(
            "Platform preset — automatically picks the correct dimensions. "
            "Use values from GET /media/options → image.platform_presets. "
            "Examples: instagram, instagram_story, linkedin, twitter, youtube_thumbnail, pinterest"
        ),
    ),
    width: int  = Body(1024, ge=512, le=1568, description="Custom width (ignored when platform is set). Snapped to nearest NVIDIA FLUX valid resolution."),
    height: int = Body(1024, ge=512, le=1568, description="Custom height (ignored when platform is set). Snapped to nearest NVIDIA FLUX valid resolution."),
    download: bool = Body(True, description="Return as file download (true) or base64 JSON (false)"),
    filename: Optional[str] = Body(None, description="Custom filename without extension"),
    seed: Optional[int] = Body(None, description="Set for reproducible results"),
    auto_enhance: bool = Body(False, description="Auto-enhance prompt using AI before generating"),
    brand_id: Optional[str] = Body(None, description="Brand ID to associate with the generated image"),
    background: bool = Body(False, description="Run generation in background (return task_id immediately, don't block)"),
    request: Request = None,
    db: AppwriteClient      = Depends(get_db),
):
    """
    Generate a high-quality AI image via **NVIDIA NIM FLUX.2-klein-4B**.

    ### Getting good results
    - Be **specific** in your prompt: include subject, style, lighting, mood, and setting.
    - Use `platform` to auto-select the right dimensions for your target channel.
    - Append a `style` to steer the visual feel without changing the subject.

    ### Valid dimensions (NVIDIA constraint)
    Width and height must each be a **multiple of 16** (512 – 1408).
    Total pixels must not exceed **1,062,400** (≈ 1024×1024).
    Using the `platform` preset handles this automatically.

    ### Background mode
    Set `background=true` to run generation asynchronously and return immediately with a task_id.
    Poll the result at `GET /api/v1/tasks/{task_id}`.

    ### Prompt tips
    | Bad | Good |
    |-----|------|
    | "a person" | "a confident female entrepreneur in a modern glass office, soft natural window light, photorealistic" |
    | "AI product" | "SaaS dashboard on a MacBook Pro, minimal dark UI, glowing blue accents, shallow depth of field, cinematic" |
    """
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    bearer = request.headers.get("Authorization", "") or None

    from app.config import settings

    # ── Background mode: dispatch to Celery and return immediately ──
    # (only if Redis is configured; otherwise fall through to synchronous mode)
    if background and settings.REDIS_URL:
        from app.tasks.media_tasks import generate_image_background

        task = generate_image_background.apply_async(
            args=(
                prompt,
                style,
                platform,
                width,
                height,
                filename,
                seed,
                auto_enhance,
                brand_id,
                user_id,
                tenant_id,
            ),
            queue="ai",
        )
        logger.info(
            f"[MEDIA] Image generation dispatched to background: task_id={task.id}, user={user_id[:8]}"
        )
        return {
            "success": True,
            "task_id": task.id,
            "status": "queued",
            "poll_url": f"/api/v1/tasks/{task.id}",
            "message": "Image generation queued. Poll the poll_url for results.",
        }

    # ── Synchronous mode: block until complete (backward compatible) ──
    t0 = _time.monotonic()

    # Resolve dimensions: platform preset → custom → default

    if platform and platform.lower() in _PLATFORM_PRESETS:
        img_w, img_h = _PLATFORM_PRESETS[platform.lower()]
    else:
        # Snap custom values to nearest valid NVIDIA multiple-of-16
        img_w, img_h = _snap_to_nvidia(width, height)

    # Optionally enhance prompt via LLM before generation
    base_prompt = prompt.strip()
    if auto_enhance:
        try:
            base_prompt = await ai_service.enhance_image_prompt(base_prompt, style=style, platform=platform)
        except Exception as exc:
            logger.warning(f"Prompt enhancement failed, using original: {exc}")

    # Build quality-boosted prompt
    full_prompt = base_prompt
    if style and not auto_enhance:
        full_prompt = f"{full_prompt}, {style}"
    # Always append quality boosters for photorealistic feel
    quality_suffix = ", high quality, sharp focus, professional"
    if "photorealistic" not in full_prompt.lower() and "cinematic" not in full_prompt.lower():
        full_prompt = full_prompt + quality_suffix
    # No text of any kind in the image
    full_prompt = full_prompt + ", no text, no words, no letters, no signs, no captions, no watermarks, no typography"
    # NVIDIA FLUX hard limit: 800 chars — unconditional clamp (handles unicode/newlines)
    full_prompt = full_prompt.replace("\n", " ").replace("\r", "")
    full_prompt = full_prompt[:800].rstrip(", ")

    try:
        result = await modal_service.generate_image(
            prompt=full_prompt,
            size=f"{img_w}x{img_h}",
            seed=seed,
        )
    except Exception as exc:
        logger.error(f"NVIDIA image generation failed: {exc}")
        raise HTTPException(status_code=500, detail="Image generation failed. Please try again later.")

    b64 = result.get("image_base64", "")
    if not b64:
        raise HTTPException(status_code=502, detail="Image generation returned no image data.")

    try:
        image_data = base64.b64decode(b64)
    except Exception as exc:
        logger.error(f"Base64 decode failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to decode image data from generation service.")

    content_type = "image/png"
    ext = "png"

    safe_title = (filename or full_prompt[:50]).strip()

    image_public_url: str | None = None

    # Upload to Appwrite storage bucket first (primary)
    try:
        img_filename = f"image_{_uuid.uuid4().hex[:12]}.png"
        storage = db.storage("media")
        file_meta = storage.upload_file(image_data, img_filename, "image/png")
        file_id   = file_meta.get("$id", "")
        if file_id:
            image_public_url = storage.get_file_url(file_id)
            logger.info(f"Image uploaded to Appwrite: {file_id}")
    except Exception as exc:
        logger.warning(f"Appwrite upload failed, falling back to disk: {exc}")
        # Fallback: save to local disk so the request doesn't fail completely
        try:
            media_dir = _Path(os.getcwd()) / "storage" / "media"

            media_dir.mkdir(parents=True, exist_ok=True)
            local_name = f"{_uuid.uuid4().hex}.png"
            (media_dir / local_name).write_bytes(image_data)
            image_public_url = f"https://api.contentstudio.thq.digital/media/{local_name}"
            logger.info(f"Image saved to disk (fallback): {local_name}")
        except Exception as disk_exc:
            logger.warning(f"Disk fallback also failed: {disk_exc}")

    # Save record to content library
    content_id = None
    try:
        insert_data: dict = {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "title": f"Image: {safe_title}",
            "content": image_public_url or f"nvidia-flux-generated: {safe_title}",
            "content_type": "image",
            "status": "draft",
            "metadata": {
                "prompt": full_prompt,
                "style": style,
                "width": img_w,
                "height": img_h,
                "platform": platform,
                "source": "nvidia_flux",
                "image_urls": [image_public_url] if image_public_url else [],
                "image_base64": b64,
            },
        }
        if image_public_url:
            insert_data["image_url"] = image_public_url
        if brand_id:
            insert_data["brand_id"] = brand_id
        saved = db.table("content").insert(insert_data).execute()
        content_id = saved.data[0]["id"] if saved.data else None
    except Exception as exc:
        logger.warning(f"Could not save image record to content table: {exc}")

    # Also save to media_history so GET /history/media shows it
    try:
        media_record = {
            "user_id":    user_id,
            "media_id":   str(_uuid.uuid4()),

            "content_id": content_id,
            "media_type": "image",
            "file_url":   image_public_url,
            "file_name":  f"{(filename or 'generated_image').replace(' ', '_')}.png",
            "mime_type":  "image/png",
            "size_bytes": len(image_data),
            "width":      img_w,
            "height":     img_h,
            "model":      "black-forest-labs/FLUX.2-klein-4B",
            "prompt":     full_prompt,
            "metadata": {
                "style": style,
                "platform": platform,
                "seed": seed,
                "source": "nvidia_flux",
            },
        }
        db.table("media_history").insert(media_record).execute()
        logger.info("Image saved to media_history")
    except Exception as exc:
        logger.warning(f"Could not save image to media_history (non-fatal): {exc}")

    # Always return as download (user can also get base64 if download=false)
    if download:
        dl_filename = f"{(filename or 'generated_image').replace(' ', '_')}.{ext}"
        return StreamingResponse(
            io.BytesIO(image_data),
            media_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{dl_filename}"',
                "X-Content-ID":        str(content_id) if content_id else "",
                "Access-Control-Expose-Headers": "Content-Disposition, X-Content-ID",
            },
        )

    # download=false → return base64 JSON so frontend can display/download directly
    return {
        "success":      True,
        "image_base64": b64,
        "image_url":    image_public_url,
        "content_type": content_type,
        "content_id":   content_id,
        "prompt":       full_prompt,
        "width":        img_w,
        "height":       img_h,
        "format":       ext,
    }


# ─────────────────────────────────────────────────────────────
# IMAGE DOWNLOAD PROXY  — GET /media/download-image
# Downloads any external URL and serves as attachment
# ─────────────────────────────────────────────────────────────
@router.get("/download-image", summary="Proxy-download an image from an external URL",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def download_image_proxy(
    url: str      = Query(..., description="External image URL to download"),
    filename: str = Query("image.png", description="Filename for the download"),
):
    """
    Proxies an image/file from the given URL back to the client as a
    Content-Disposition attachment. Useful for bypassing CORS on external hosts.

    The target URL is validated against loopback/private/link-local ranges
    (including the cloud metadata address) before and after every redirect
    hop to prevent SSRF.
    """
    try:
        resp = await safe_get(url, timeout=60.0)

        content_type = resp.headers.get("content-type", "application/octet-stream")
        safe = filename.replace("/", "_").replace("\\", "_").replace("..", "_")

        return StreamingResponse(
            io.BytesIO(resp.content),
            media_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{safe}"',
                "Access-Control-Expose-Headers": "Content-Disposition",
            },
        )
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Failed to fetch image: HTTP {exc.response.status_code}",
        )
    except Exception as exc:
        logger.error(f"download_image_proxy error: {exc}")
        raise HTTPException(status_code=500, detail="Failed to fetch the requested image.")


# ─────────────────────────────────────────────────────────────
# SOCIAL MEDIA CAROUSEL GENERATION  — POST /media/generate/social
# Uses Gamma AI for carousel / multi-slide social posts
# ─────────────────────────────────────────────────────────────
@router.post("/generate/social", summary="Generate social media carousel (Gamma AI)",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def generate_social_post(
    request: Request,
    input_text: str = Body(
        ...,
        description="The topic or key message for your carousel. Be specific — Gamma uses this as the creative brief.",
    ),
    title: Optional[str] = Body(
        None,
        description="Exact headline for slide 1 (optional). If omitted, Gamma generates one.",
    ),
    subtitle: Optional[str] = Body(
        None,
        description="Subtitle or hook for slide 1 (optional).",
    ),
    platform: CarouselPlatform = Body(
        CarouselPlatform.INSTAGRAM,
        description="Target platform. Use GET /media/options → carousel.platforms.",
    ),
    num_cards: CarouselSlideCount = Body(
        CarouselSlideCount.SEVEN,
        description="Number of slides. Use GET /media/options → carousel.slide_counts.",
    ),
    design_style: CarouselDesignStyle = Body(
        CarouselDesignStyle.BOLD_VIBRANT,
        description="Visual design style. Use GET /media/options → carousel.design_styles.",
    ),
    theme: Optional[CarouselTheme] = Body(
        None,
        description="Gamma theme override. Use GET /media/options → carousel.themes.",
    ),
    audience: Optional[CarouselAudience] = Body(
        None,
        description="Target audience for tone calibration. Use GET /media/options → carousel.audiences.",
    ),
    tone: Optional[CarouselTone] = Body(
        None,
        description="Content tone. Use GET /media/options → carousel.tones.",
    ),
    amount_of_text: CarouselTextDensity = Body(
        CarouselTextDensity.BRIEF,
        description="Text density per slide. Use GET /media/options → carousel.text_density.",
    ),
    image_source: CarouselImageSource = Body(
        CarouselImageSource.AI_GENERATED,
        description="Image sourcing strategy. Use GET /media/options → carousel.image_sources.",
    ),
    art_style: Optional[CarouselArtStyle] = Body(
        None,
        description="Art style for images. Use GET /media/options → carousel.art_styles.",
    ),
    dimensions: Optional[CarouselDimension] = Body(
        None,
        description="Slide dimensions. Use GET /media/options → carousel.dimensions. Default auto-selected per platform.",
    ),
    export_format: CarouselExportFormat = Body(
        CarouselExportFormat.PNG_ZIP,
        description="Output format. PNG_ZIP = ZIP of PNG slides (best for posting). Use GET /media/options → carousel.export_formats.",
    ),
    include_hashtags: bool = Body(True,  description="Include 1-3 hashtags in the design"),
    custom_hashtags: Optional[str] = Body(None, description="Specific hashtags (e.g. '#AI #ContentMarketing')"),
    extra_keywords: Optional[str] = Body(None, description="Extra keywords for consistent image style"),
    additional_instructions: Optional[str] = Body(None, description="Any extra design notes for Gamma"),
    return_file: bool = Body(True,  description="True = download file directly. False = returns generation_id for async polling."),
    auto_enhance: bool = Body(False, description="Auto-enhance topic using AI before generating"),
    brand_id: Optional[str] = Body(None, description="Brand ID to associate with the carousel"),
    background: bool = Body(False, description="Run generation in background (return task_id immediately, don't block)"),
    db: AppwriteClient = Depends(get_db),
):
    """
    ## Gamma AI Carousel Generator

    Generates a professional social media carousel using **Gamma AI**.

    ### Tips for best results
    - **`input_text`**: Be specific. "5 ways AI is changing content marketing in 2026" beats "AI content".
    - **`design_style`**: Pick one that matches your brand. `DARK_LUXURY` and `MINIMAL_CLEAN` perform best on LinkedIn.
    - **`platform`**: Auto-selects dimensions. Choose the platform where you'll actually post.
    - **`num_cards`**: 5-7 slides is optimal for Instagram; 3-5 for LinkedIn.

    ### Getting your options
    Call `GET /media/options` to see all valid values for every parameter above.

    ### Output
    Returns a **ZIP of PNG slides** (for `png` format) or a **PDF/PPTX file** — ready to upload.

    ### Background mode
    Set `background=true` to run generation asynchronously and return immediately with a task_id.
    Poll the result at `GET /api/v1/tasks/{task_id}`.

    Requires `GAMMA_API_KEY` to be configured.
    """
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    if not gamma_service.enabled:
        raise HTTPException(
            status_code=503,
            detail="Gamma AI service is not configured. Please set GAMMA_API_KEY in your .env.",
        )

    bearer = request.headers.get("Authorization", "") or None

    from app.config import settings

    # ── Background mode: dispatch to Celery and return immediately ──
    # (only if Redis is configured; otherwise fall through to synchronous mode)
    if background and settings.REDIS_URL:
        from app.tasks.media_tasks import generate_carousel_background

        # Note: carousel endpoint has many parameters; we pass simplified ones to the task
        task = generate_carousel_background.apply_async(
            args=(
                input_text,  # topic
                num_cards.value if hasattr(num_cards, "value") else int(num_cards),  # slide_count
                design_style.value if hasattr(design_style, "value") else design_style,  # style
                None,  # brand_context (could be extended later)
                user_id,
                tenant_id,
            ),
            queue="ai",
        )
        logger.info(
            f"[CAROUSEL] Carousel generation dispatched to background: task_id={task.id}, user={user_id[:8]}"
        )
        return {
            "success": True,
            "task_id": task.id,
            "status": "queued",
            "poll_url": f"/api/v1/tasks/{task.id}",
            "message": "Carousel generation queued. Poll the poll_url for results.",
        }

    # ── Synchronous mode: block until complete (backward compatible) ──
    t0 = _time.monotonic()

    if auto_enhance:
        try:
            input_text = await ai_service.enhance_carousel_prompt_fast(
                input_text,
                platform=platform.value if hasattr(platform, "value") else platform,
                num_slides=num_cards.value if hasattr(num_cards, "value") else int(num_cards),
            )
        except Exception as exc:
            logger.warning(f"Carousel prompt enhancement failed, using original: {exc}")

    # ── Resolve enum values ────────────────────────────────────────────────────
    platform_val     = platform.value if hasattr(platform, "value") else platform
    design_style_val = design_style.value if hasattr(design_style, "value") else design_style
    theme_val        = theme.value if (theme and hasattr(theme, "value")) else theme
    audience_val     = audience.value if (audience and hasattr(audience, "value")) else audience
    tone_val         = tone.value if (tone and hasattr(tone, "value")) else tone
    amount_val       = amount_of_text.value if hasattr(amount_of_text, "value") else amount_of_text
    img_source_val   = image_source.value if hasattr(image_source, "value") else image_source
    art_style_val    = art_style.value if (art_style and hasattr(art_style, "value")) else art_style
    dimensions_val   = dimensions.value if (dimensions and hasattr(dimensions, "value")) else dimensions
    export_fmt_val   = export_format.value if hasattr(export_format, "value") else export_format
    num_cards_int    = num_cards.value if hasattr(num_cards, "value") else int(num_cards)

    # Auto-select dimension based on platform if not explicitly set
    _platform_dimensions = {
        "instagram":        "4x5",
        "instagram_story":  "9x16",
        "linkedin":         "16x9",
        "facebook":         "1.91x1",
        "tiktok":           "9x16",
        "twitter":          "16x9",
        "pinterest":        "4x5",
    }
    target_dimensions = dimensions_val or _platform_dimensions.get(platform_val, "4x5")

    try:
        # ── Build AI instructions ──────────────────────────────────────────────
        base_instructions = (
            f"Create a premium social media carousel optimised for {platform_val.upper()}. "
            f"Design style: {design_style_val}. "
            "CRITICAL: This must be a social media carousel — NOT a presentation or slide deck. "
            "Maximise visual impact with bold typography, stunning imagery, and a clean, high-converting layout. "
            "Each slide must communicate ONE clear idea. Use hierarchy: big headline → supporting detail."
        )
        if audience_val:
            base_instructions += f"\n- TARGET AUDIENCE: {audience_val}"
        if tone_val:
            base_instructions += f"\n- TONE: {tone_val}"

        if title:
            base_instructions += f"\n- FIRST SLIDE HEADLINE (use exactly): '{title}'"
        if subtitle:
            base_instructions += f"\n- FIRST SLIDE SUBTITLE (use exactly): '{subtitle}'"

        if additional_instructions:
            base_instructions += f"\n\nEXTRA DESIGN NOTES: {additional_instructions}"

        if include_hashtags:
            if custom_hashtags:
                base_instructions += f"\n- Hashtags to include: {custom_hashtags}"
            else:
                base_instructions += "\n- Include 1-3 relevant hashtags as subtle design elements on the last slide."

        if extra_keywords:
            base_instructions += f"\n- Image style keywords for visual consistency: {extra_keywords}"

        # ── Resolve theme ID from Gamma ────────────────────────────────────────
        theme_id = None
        if theme_val:
            try:
                available_themes = await gamma_service.list_themes()
                for t in available_themes:
                    if t.get("name", "").lower() == theme_val.lower():
                        theme_id = t.get("id")
                        break
            except Exception as exc:
                logger.warning(f"Could not fetch Gamma themes: {exc}")

        image_options  = {"source": img_source_val, "style": art_style_val or design_style_val}
        gamma_export_as = "pptx" if export_fmt_val in ("pptx", "google_slides") else "pdf"

        result = await gamma_service.create_generation(
            input_text=input_text,
            text_mode="generate",
            format="presentation",
            theme_id=theme_id,
            num_cards=num_cards_int,
            export_as=gamma_export_as,
            additional_instructions=base_instructions,
            text_options={"amount": amount_val},
            image_options=image_options,
            card_options={"dimensions": target_dimensions},
        )

        generation_id  = result["generation_id"]
        _gamma_response = result["response"]

        # Save record to content library
        from datetime import datetime, timezone
        saved = db.table("content").insert({
            "user_id":      user_id,
            "tenant_id":    tenant_id,
            "title":        f"Carousel: {input_text[:60]}",
            "content":      generation_id,          # Gamma generation ID — use with /media/status/{id}
            "content_type": "carousel",
            "platform":     platform_val,           # top-level platform field
            "brand_id":     brand_id,
            "status":       "draft",
            "updated_at":   datetime.now(timezone.utc).isoformat(),
            "metadata": {
                "gamma_generation_id": generation_id,
                "platform":            platform_val,
                "num_cards":           num_cards_int,
                "design_style":        design_style_val,
                "export_format":       export_fmt_val,
                "type":                "gamma_carousel",
                "slide_urls":          [],          # populated below once generation completes
            },
        }).execute()
        content_id = saved.data[0]["id"] if saved.data else None

        # Also save to media_history (same as image endpoints)
        try:
            db.table("media_history").insert({
                "user_id":    user_id,
                "media_id":   str(_uuid.uuid4()),
                "content_id": content_id,
                "media_type": "carousel",
                "file_url":   None,
                "file_name":  f"carousel_{generation_id}.zip",
                "mime_type":  "application/x-zip-compressed",
                "size_bytes": 0,
                "model":      "gamma-ai",
                "prompt":     input_text,
                "metadata": {
                    "gamma_generation_id": generation_id,
                    "platform":            platform_val,
                    "num_cards":           num_cards_int,
                    "design_style":        design_style_val,
                    "export_format":       export_fmt_val,
                },
            }).execute()
            logger.info("Carousel saved to media_history")
        except Exception as exc:
            logger.warning(f"Could not save carousel to media_history (non-fatal): {exc}")

        # One-click download
        if return_file:
            try:
                gamma_data = await _poll_generation_completion(
                    generation_id, max_attempts=20, base_delay=1.0, max_delay=3.0
                )

                if gamma_data.get("status") == "completed":

                    # PDF / PPTX direct download
                    if export_format in ("pdf", "pptx", "google_slides"):
                        url = (
                            gamma_data.get("exportUrl")
                            or gamma_data.get("pdfUrl")
                            or gamma_data.get("pptxUrl")
                        )
                        if url:
                            async with httpx.AsyncClient(timeout=60.0) as client:
                                r = await client.get(url, follow_redirects=True)
                                r.raise_for_status()
                            ext  = "pdf" if export_format == "pdf" else "pptx"
                            mime = ("application/pdf" if ext == "pdf"
                                    else "application/vnd.openxmlformats-officedocument.presentationml.presentation")
                            # Update content record with export URL
                            if content_id:
                                try:
                                    db.table("content").eq("id", content_id).update(
                                        {"metadata": {"gamma_generation_id": generation_id,
                                                       "platform": platform_val,
                                                       "num_cards": num_cards_int,
                                                       "design_style": design_style_val,
                                                       "export_format": export_fmt_val,
                                                       "type": "gamma_carousel",
                                                       "export_url": url,
                                                       "slide_urls": []}}
                                    ).execute()
                                except Exception:
                                    pass
                            return StreamingResponse(
                                io.BytesIO(r.content),
                                media_type=mime,
                                headers={
                                    "Content-Disposition": f"attachment; filename=carousel_{generation_id}.{ext}",
                                    "X-Generation-ID":    generation_id,
                                    "X-Content-ID":       str(content_id) if content_id else "",
                                    "Access-Control-Expose-Headers": "Content-Disposition, X-Generation-ID, X-Content-ID",
                                },
                            )

                    # PNG download — extract slide images
                    slide_urls = await gamma_service.get_slide_images(generation_id)
                    if not slide_urls:
                        raise HTTPException(status_code=500, detail="No downloadable content found.")

                    if any(u.startswith("pdf_fallback:") for u in slide_urls):
                        pdf_url = slide_urls[0].replace("pdf_fallback:", "")
                        image_bytes_list = await gamma_service.convert_pdf_to_images(pdf_url)
                        image_contents = [
                            (f"slide_{i+1}.png", data, "png")
                            for i, data in enumerate(image_bytes_list)
                        ]
                    else:
                        image_contents = []
                        async with httpx.AsyncClient(timeout=30.0) as client:
                            for idx, url in enumerate(slide_urls):
                                try:
                                    ir = await client.get(url, follow_redirects=True)
                                    ir.raise_for_status()
                                    file_ext = url.split(".")[-1].split("?")[0] if "." in url else "png"
                                    if len(file_ext) > 4:
                                        file_ext = "png"
                                    image_contents.append((f"slide_{idx+1}.{file_ext}", ir.content, file_ext))
                                except Exception:
                                    continue

                    if not image_contents:
                        raise HTTPException(status_code=500, detail="Failed to retrieve images from Gamma.")

                    # Save slide URLs back to content record so content library shows them
                    if content_id and slide_urls:
                        try:
                            db.table("content").eq("id", content_id).update(
                                {"metadata": {"gamma_generation_id": generation_id,
                                               "platform": platform_val,
                                               "num_cards": num_cards_int,
                                               "design_style": design_style_val,
                                               "export_format": export_fmt_val,
                                               "type": "gamma_carousel",
                                               "slide_urls": slide_urls}}
                            ).execute()
                        except Exception:
                            pass

                    # Single image
                    if len(image_contents) == 1:
                        fname, data, file_ext = image_contents[0]
                        return StreamingResponse(
                            io.BytesIO(data),
                            media_type=f"image/{file_ext}",
                            headers={
                                "Content-Disposition": f"attachment; filename=carousel_{generation_id}.{file_ext}",
                                "X-Generation-ID": generation_id,
                                "X-Content-ID": str(content_id) if content_id else "",
                            },
                        )

                    # Multi-slide → ZIP
                    zip_buf = io.BytesIO()
                    with zipfile.ZipFile(zip_buf, "a", zipfile.ZIP_DEFLATED, False) as zf:
                        for fname, data, _ in image_contents:
                            zf.writestr(fname, data)
                    zip_buf.seek(0)
                    return StreamingResponse(
                        zip_buf,
                        media_type="application/x-zip-compressed",
                        headers={
                            "Content-Disposition": f"attachment; filename=carousel_{generation_id}.zip",
                            "X-Generation-ID": generation_id,
                            "X-Content-ID": str(content_id) if content_id else "",
                        },
                    )

            except Exception as exc:
                logger.exception("One-click download failed; returning async response")

        # Async / fallback response
        return {
            "success":       True,
            "status":        "processing",
            "generation_id": generation_id,
            "content_id":    content_id,
            "total_slides":  num_cards,
            "message":       "Carousel generation started. Use /media/status/{generation_id} to poll.",
        }

    except Exception as exc:
        logger.exception("generate_social_post failed")
        _handle_gamma_error(exc)


# ─────────────────────────────────────────────────────────────
# GENERATION STATUS  — GET /media/status/{generation_id}
# ─────────────────────────────────────────────────────────────
@router.get("/status/{generation_id}", summary="Poll the status of a Gamma carousel generation",
    responses={
                500: {"description": "Internal server error"},
                503: {"description": "Service unavailable"}
    }
)
async def get_generation_status(
    generation_id: str,
    
):
    """Returns the current status of a Gamma generation. On completion, includes image URLs."""
    if not gamma_service.enabled:
        raise HTTPException(status_code=503, detail="Gamma AI service not configured")

    try:
        data   = await gamma_service.get_generation(generation_id)
        status = data.get("status", "unknown")
        out: Dict[str, Any] = {"generation_id": generation_id, "status": status}

        if status == "completed":
            slide_urls = await gamma_service.get_slide_images(generation_id)
            out["slide_urls"]   = slide_urls
            out["images"]       = slide_urls
            out["total_slides"] = len(slide_urls)
            out["export_url"]   = data.get("exportUrl") or data.get("pdfUrl")
        elif status in ("failed", "error"):
            out["error"] = data.get("error", "Generation failed")

        return out

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Status check error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────
# DOWNLOAD  — GET /media/download/{generation_id}
# ─────────────────────────────────────────────────────────────
@router.get("/download/{generation_id}", summary="Download completed Gamma carousel as ZIP",
    responses={
                500: {"description": "Internal server error"},
                503: {"description": "Service unavailable"}
    }
)
async def download_carousel(
    generation_id: str,
    
):
    """
    Download a completed Gamma carousel as a ZIP of PNG slides.
    Call this after `/media/status/{generation_id}` returns `status: completed`.
    Returns 202 with `{"status":"processing"}` if still generating.
    """
    if not gamma_service.enabled:
        raise HTTPException(status_code=503, detail="Gamma AI service not configured")

    try:
        data   = await gamma_service.get_generation(generation_id)
        status = data.get("status", "unknown")

        if status in ("failed", "error"):
            raise HTTPException(status_code=500, detail=f"Generation failed: {data.get('error', 'unknown error')}")

        if status != "completed":
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=202, content={"status": status, "generation_id": generation_id, "message": "Still processing — try again shortly."})

        # Build ZIP from slide images
        slide_urls = await gamma_service.get_slide_images(generation_id)
        if not slide_urls:
            raise HTTPException(status_code=500, detail="No slide images found for this generation.")

        if any(u.startswith("pdf_fallback:") for u in slide_urls):
            pdf_url = slide_urls[0].replace("pdf_fallback:", "")
            image_bytes_list = await gamma_service.convert_pdf_to_images(pdf_url)
            image_contents = [(f"slide_{i+1}.png", data_bytes) for i, data_bytes in enumerate(image_bytes_list)]
        else:
            image_contents = []
            async with httpx.AsyncClient(timeout=30.0) as client:
                for idx, url in enumerate(slide_urls):
                    try:
                        r = await client.get(url, follow_redirects=True)
                        r.raise_for_status()
                        file_ext = url.split(".")[-1].split("?")[0] if "." in url else "png"
                        if len(file_ext) > 4:
                            file_ext = "png"
                        image_contents.append((f"slide_{idx+1}.{file_ext}", r.content))
                    except Exception:
                        continue

        if not image_contents:
            raise HTTPException(status_code=500, detail="Failed to retrieve slide images.")

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "a", zipfile.ZIP_DEFLATED, False) as zf:
            for fname, img_data in image_contents:
                zf.writestr(fname, img_data)
        zip_buf.seek(0)

        return StreamingResponse(
            zip_buf,
            media_type="application/x-zip-compressed",
            headers={
                "Content-Disposition": f"attachment; filename=carousel_{generation_id}.zip",
                "X-Generation-ID": generation_id,
            },
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Download error for {generation_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))



# ─────────────────────────────────────────────────────────────
# PROMPT ENHANCEMENT ENDPOINTS
# ─────────────────────────────────────────────────────────────

@router.post("/enhance-prompt", summary="Enhance an image or carousel prompt using AI",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def enhance_media_prompt(
    prompt: str = Body(..., description="Your simple text — e.g. 'A futuristic AI engineer on holographic screens'"),
    type: str = Body("image", description="'image' or 'carousel'"),
    platform: Optional[str] = Body(None, description="Target platform — e.g. 'Instagram', 'LinkedIn'. Used for carousel briefs."),
    num_slides: int = Body(5, ge=3, le=15, description="Number of slides (carousel only)"),

):
    """
    Send a simple prompt, get back a powerful AI-enhanced version ready for image or carousel generation.
    Copy the `enhanced` field and paste it into `/generate/image` or `/generate/social`.
    """
    try:
        if type.lower() == "carousel":
            enhanced = await ai_service.enhance_carousel_prompt(
                prompt,
                platform=platform,
                num_slides=num_slides,
            )
        else:
            enhanced = await ai_service.enhance_image_prompt(prompt)
        return {"original": prompt, "enhanced": enhanced, "type": type}
    except Exception as exc:
        logger.error(f"Prompt enhancement failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Enhancement failed: {str(exc)}")


@router.post("/generate/enhanced-image", summary="Generate image with auto-enhancement",
    responses={
                500: {"description": "Internal server error"},
                502: {"description": "Bad gateway"}
    }
)
async def generate_enhanced_image(
    request: Request,
    user_input: str = Body(
        ..., 
        description="Simple user input - will be enhanced before generation"
    ),
    platform: Optional[str] = Body(
        None,
        description="Platform preset for dimensions",
    ),
    width: int = Body(1024, ge=512, le=1408),
    height: int = Body(1024, ge=512, le=1408),
    style: Optional[str] = Body(
        None,
        description="Visual style (e.g., 'photorealistic', 'cinematic')",
    ),
    enhance_only: bool = Body(
        False,
        description="If true, only return enhanced prompt without generating image",
    ),
    download: bool = Body(True, description="Return as file download (true) or base64 JSON (false)"),
    filename: Optional[str] = Body(None, description="Custom filename without extension"),
    seed: Optional[int] = Body(None, description="Seed for reproducible results"),
    brand_id: Optional[str] = Body(None, description="Brand ID to associate with the generated image"),
    db: AppwriteClient = Depends(get_db),
):
    """
    **One-Click Enhanced Image Generation**
    
    This endpoint:
    1. Takes user's simple input
    2. Automatically enhances it using AI
    3. Generates the image with the enhanced prompt
    
    Set `enhance_only: true` to just get the enhanced prompt (preview mode).
    
    Use this when you want better results without manually crafting prompts.
    """
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    # Step 1: enhance the prompt — single fast LLM call to stay within gateway timeout
    try:
        enhanced_prompt = await ai_service.enhance_image_prompt_fast(
            prompt=user_input, style=style, platform=platform
        )
    except Exception as exc:
        logger.warning(f"Prompt enhancement failed, using original: {exc}")
        enhanced_prompt = user_input

    # If user only wants the enhancement, return that
    if enhance_only:
        return {"success": True, "original_input": user_input, "enhanced_prompt": enhanced_prompt}

    # Step 2: check credits + generate
    bearer = request.headers.get("Authorization", "") or None
    t0 = _time.monotonic()

    if platform and platform.lower() in _PLATFORM_PRESETS:
        img_w, img_h = _PLATFORM_PRESETS[platform.lower()]
    else:
        img_w, img_h = _snap_to_nvidia(width, height)

    full_prompt = enhanced_prompt.strip()
    if style and not full_prompt.endswith(style):
        full_prompt = f"{full_prompt}, {style}"
    quality_suffix = ", high quality, sharp focus, professional"
    if "photorealistic" not in full_prompt.lower() and "cinematic" not in full_prompt.lower():
        full_prompt = full_prompt + quality_suffix
    # No text of any kind in the image
    full_prompt = full_prompt + ", no text, no words, no letters, no signs, no captions, no watermarks, no typography"
    # NVIDIA FLUX hard limit: 800 chars — unconditional clamp (handles unicode/newlines)
    full_prompt = full_prompt.replace("\n", " ").replace("\r", "")
    full_prompt = full_prompt[:800].rstrip(", ")

    try:
        result = await modal_service.generate_image(
            prompt=full_prompt,
            size=f"{img_w}x{img_h}",
            seed=seed,
        )
    except Exception as exc:
        logger.error(f"Enhanced image generation failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Image generation failed: {str(exc)}")

    b64 = result.get("image_base64", "")
    if not b64:
        raise HTTPException(status_code=502, detail="Image generation returned no image data.")

    try:
        image_data = base64.b64decode(b64)
    except Exception as exc:
        logger.error(f"Base64 decode failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to decode image data.")

    safe_title = (filename or user_input[:50]).strip()

    # Upload to Appwrite
    try:
        img_filename = f"image_{_uuid.uuid4().hex[:12]}.png"
        storage = db.storage("media")
        file_meta = storage.upload_file(image_data, img_filename, "image/png")
        file_id = file_meta.get("$id", "")
        image_public_url = storage.get_file_url(file_id) if file_id else None
    except Exception as exc:
        logger.warning(f"Appwrite upload failed: {exc}")
        image_public_url = None
        try:
            media_dir = _Path(os.getcwd()) / "storage" / "media"
            media_dir.mkdir(parents=True, exist_ok=True)
            local_name = f"{_uuid.uuid4().hex}.png"
            (media_dir / local_name).write_bytes(image_data)
            image_public_url = f"https://api.contentstudio.thq.digital/media/{local_name}"
        except Exception as disk_exc:
            logger.warning(f"Disk fallback also failed: {disk_exc}")

    # Save to content library
    content_id = None
    try:
        insert_data = {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "title": f"Enhanced Image: {safe_title}",
            "content": image_public_url or f"nvidia-flux-enhanced: {safe_title}",
            "content_type": "image",
            "status": "draft",
            "metadata": {
                "original_input": user_input,
                "enhanced_prompt": full_prompt,
                "style": style,
                "width": img_w,
                "height": img_h,
                "platform": platform,
                "source": "nvidia_flux_enhanced",
                "enhancement_applied": True,
                "image_urls": [image_public_url] if image_public_url else [],
                "image_base64": b64,
            },
        }
        if image_public_url:
            insert_data["image_url"] = image_public_url
        if brand_id:
            insert_data["brand_id"] = brand_id
        saved = db.table("content").insert(insert_data).execute()
        content_id = saved.data[0]["id"] if saved.data else None
    except Exception as exc:
        logger.warning(f"Could not save image record: {exc}")

    # Save to media_history
    try:
        media_record = {
            "user_id": user_id,
            "media_id": str(_uuid.uuid4()),
            "content_id": content_id,
            "media_type": "image",
            "file_url": image_public_url,
            "file_name": f"{(filename or 'enhanced_image').replace(' ', '_')}.png",
            "mime_type": "image/png",
            "size_bytes": len(image_data),
            "width": img_w,
            "height": img_h,
            "model": "black-forest-labs/FLUX.2-klein-4B",
            "prompt": full_prompt,
            "metadata": {
                "original_input": user_input,
                "enhancement_applied": True,
                "source": "nvidia_flux_enhanced"
            },
        }
        db.table("media_history").insert(media_record).execute()
    except Exception as exc:
        logger.warning(f"Could not save to media_history: {exc}")

    # Return response
    if download:
        dl_filename = f"{(filename or 'enhanced_image').replace(' ', '_')}.png"
        return StreamingResponse(
            io.BytesIO(image_data),
            media_type="image/png",
            headers={
                "Content-Disposition": f'attachment; filename="{dl_filename}"',
                "X-Content-ID": str(content_id) if content_id else "",
                "X-Enhanced-Prompt": full_prompt[:200],  # Truncate for header
                "Access-Control-Expose-Headers": "Content-Disposition, X-Content-ID, X-Enhanced-Prompt",
            },
        )

    return {
        "success": True,
        "image_base64": b64,
        "image_url": image_public_url,
        "content_type": "image/png",
        "content_id": content_id,
        "original_input": user_input,
        "enhanced_prompt": full_prompt,
        "width": img_w,
        "height": img_h,
        "format": "png",
    }
