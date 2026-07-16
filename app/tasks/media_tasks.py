"""
Background tasks for media generation (images, carousels).

These tasks are dispatched to Celery queue 'ai' when ?background=true is passed
to the media generation endpoints. They handle long-running NVIDIA FLUX image
generation and Gamma AI carousel generation without blocking the HTTP request.

Each task:
- Runs in a background worker (separate process)
- Saves results to content + media_history tables
- Deducts credits from user's tenant
- Can be polled via GET /api/v1/tasks/{task_id}
"""

import asyncio
import base64
import concurrent.futures
import os
import time as _time
import uuid as _uuid
from pathlib import Path as _Path
from typing import Optional

from app.celery_app import celery_app

from app.db.appwrite_client import get_appwrite_client
from app.services import ai_service
from app.services import modal_service
from app.utils.logger import logger


# Helper to run async code in both sync and async contexts
def _run_async(coro):
    """Run a coroutine safely whether or not an event loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    else:
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()

# ─────────────────────────────────────────────────────────────────
# IMAGE GENERATION — Background task
# Called when POST /media/generate/image?background=true
# ─────────────────────────────────────────────────────────────────


@celery_app.task(bind=True, queue="ai", time_limit=120)
def generate_image_background(
    self,
    prompt: str,
    style: Optional[str],
    platform: Optional[str],
    width: int,
    height: int,
    filename: Optional[str],
    seed: Optional[int],
    auto_enhance: bool,
    brand_id: Optional[str],
    user_id: str,
    tenant_id: str,
) -> dict:
    """
    Background task for AI image generation via NVIDIA FLUX.

    Runs asynchronously in a Celery worker. Handles image generation,
    Appwrite storage upload, content record creation, and credit deduction.

    Returns:
        dict: {
            "success": bool,
            "image_url": str | None,
            "content_id": str | None,
            "prompt": str,
            "width": int,
            "height": int,
            "error": str | None,
        }
    """
    try:
        t0 = _time.monotonic()
        db = get_appwrite_client()

        # Map platform presets to dimensions (matching endpoint)
        _PLATFORM_PRESETS = {
            "instagram": (1080, 1080),
            "instagram_story": (1080, 1920),
            "linkedin": (1200, 628),
            "twitter": (1024, 576),
            "youtube_thumbnail": (1280, 720),
            "pinterest": (1000, 1500),
            "facebook": (1200, 630),
            "tiktok": (1080, 1920),
        }

        def _snap_to_nvidia(w: int, h: int) -> tuple:
            """Snap dimensions to nearest multiple of 16 (NVIDIA requirement)."""
            return (round(w / 16) * 16, round(h / 16) * 16)

        # Resolve dimensions: platform preset → custom → default
        if platform and platform.lower() in _PLATFORM_PRESETS:
            img_w, img_h = _PLATFORM_PRESETS[platform.lower()]
        else:
            img_w, img_h = _snap_to_nvidia(width, height)

        # Optionally enhance prompt via LLM before generation
        base_prompt = prompt.strip()
        if auto_enhance:
            try:
                base_prompt = _run_async(
                    ai_service.enhance_image_prompt(
                        base_prompt, style=style, platform=platform))
            except Exception as exc:
                logger.warning(
                    f"[MEDIA_BG] Prompt enhancement failed, using original: {exc}")

        # Build quality-boosted prompt
        full_prompt = base_prompt
        if style and not auto_enhance:
            full_prompt = f"{full_prompt}, {style}"
        # Always append quality boosters
        quality_suffix = ", high quality, sharp focus, professional"
        if "photorealistic" not in full_prompt.lower(
        ) and "cinematic" not in full_prompt.lower():
            full_prompt = full_prompt + quality_suffix
        # No text of any kind
        full_prompt = full_prompt + \
            ", no text, no words, no letters, no signs, no captions, no watermarks, no typography"
        # NVIDIA FLUX limit: 800 chars
        full_prompt = full_prompt.replace("\n", " ").replace("\r", "")
        full_prompt = full_prompt[:800].rstrip(", ")

        logger.info(
            f"[MEDIA_BG] Image generation started: user={user_id[:8]}, "
            f"size={img_w}x{img_h}, auto_enhance={auto_enhance}"
        )

        # Generate image via NVIDIA FLUX
        result = _run_async(
            modal_service.generate_image(
                prompt=full_prompt,
                size=f"{img_w}x{img_h}",
                seed=seed,
            )
        )

        b64 = result.get("image_base64", "")
        if not b64:
            logger.error("[MEDIA_BG] No image data returned from NVIDIA")
            return {
                "success": False,
                "image_url": None,
                "content_id": None,
                "prompt": full_prompt,
                "width": img_w,
                "height": img_h,
                "error": "Image generation returned no image data",
            }

        try:
            image_data = base64.b64decode(b64)
        except Exception as exc:
            logger.error(f"[MEDIA_BG] Base64 decode failed: {exc}")
            return {
                "success": False,
                "image_url": None,
                "content_id": None,
                "prompt": full_prompt,
                "width": img_w,
                "height": img_h,
                "error": f"Failed to decode image data: {exc}",
            }

        _content_type = "image/png"
        safe_title = (filename or full_prompt[:50]).strip()
        image_public_url: Optional[str] = None

        # Upload to Appwrite storage bucket (primary)
        try:
            img_filename = f"image_{_uuid.uuid4().hex[:12]}.png"
            storage = db.storage("media")
            file_meta = storage.upload_file(
                image_data, img_filename, "image/png")
            file_id = file_meta.get("$id", "")
            if file_id:
                image_public_url = storage.get_file_url(file_id)
                logger.info(
                    f"[MEDIA_BG] Image uploaded to Appwrite: {file_id}")
        except Exception as exc:
            logger.warning(
                f"[MEDIA_BG] Appwrite upload failed, falling back to disk: {exc}")
            # Fallback: save to local disk
            try:
                media_dir = _Path(os.getcwd()) / "storage" / "media"
                media_dir.mkdir(parents=True, exist_ok=True)
                local_name = f"{_uuid.uuid4().hex}.png"
                (_Path(media_dir) / local_name).write_bytes(image_data)
                image_public_url = f"https://api.contentstudio.thq.digital/media/{local_name}"
                logger.info(
                    f"[MEDIA_BG] Image saved to disk (fallback): {local_name}")
            except Exception as disk_exc:
                logger.warning(
                    f"[MEDIA_BG] Disk fallback also failed: {disk_exc}")

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
            logger.info(f"[MEDIA_BG] Content record created: {content_id}")
        except Exception as exc:
            logger.warning(
                f"[MEDIA_BG] Could not save image record to content table: {exc}")

        # Save to media_history
        try:
            media_record = {
                "user_id": user_id,
                "media_id": str(
                    _uuid.uuid4()),
                "content_id": content_id,
                "media_type": "image",
                "file_url": image_public_url,
                "file_name": f"{(filename or 'generated_image').replace(' ', '_')}.png",
                "mime_type": "image/png",
                "size_bytes": len(image_data),
                "width": img_w,
                "height": img_h,
                "model": "black-forest-labs/FLUX.2-klein-4B",
                "prompt": full_prompt,
                "metadata": {
                    "style": style,
                    "platform": platform,
                    "seed": seed,
                    "source": "nvidia_flux",
                },
            }
            db.table("media_history").insert(media_record).execute()
            logger.info("[MEDIA_BG] Image saved to media_history")
        except Exception as exc:
            logger.warning(
                f"[MEDIA_BG] Could not save image to media_history (non-fatal): {exc}")

        logger.info(
            "[MEDIA_BG] Image generation completed successfully: "
            f"duration={((_time.monotonic() - t0) * 1000):.1f}ms"
        )

        return {
            "success": True,
            "image_url": image_public_url,
            "content_id": content_id,
            "prompt": full_prompt,
            "width": img_w,
            "height": img_h,
            "error": None,
        }

    except Exception as exc:
        logger.exception(f"[MEDIA_BG] Image generation task failed: {exc}")
        return {
            "success": False,
            "image_url": None,
            "content_id": None,
            "prompt": prompt[:100],
            "width": width,
            "height": height,
            "error": f"Background task failed: {str(exc)}",
        }


# ─────────────────────────────────────────────────────────────────
# CAROUSEL GENERATION — Background task
# Called when POST /media/generate/social?background=true
# ─────────────────────────────────────────────────────────────────


@celery_app.task(bind=True, queue="ai", time_limit=120)
def generate_carousel_background(
    self,
    topic: str,
    slide_count: int,
    style: Optional[str],
    brand_context: Optional[str],
    user_id: str,
    tenant_id: str,
) -> dict:
    """
    Background task for carousel generation via Gamma AI.

    Runs asynchronously in a Celery worker. Handles carousel generation,
    content record creation, and credit deduction.

    Returns:
        dict: {
            "success": bool,
            "carousel_url": str | None,
            "carousel_id": str | None,
            "topic": str,
            "error": str | None,
        }
    """
    try:
        t0 = _time.monotonic()
        db = get_appwrite_client()

        logger.info(
            f"[CAROUSEL_BG] Carousel generation started: user={user_id[:8]}, "
            f"topic={topic[:40]}, slides={slide_count}"
        )

        # Call existing ai_service carousel generation
        result = _run_async(
            ai_service.generate_carousel(
                topic=topic,
                slide_count=slide_count,
                style=style,
                brand_context=brand_context,
            )
        )

        carousel_url = result.get("url") or result.get("carousel_url")
        carousel_id = result.get("id") or result.get("carousel_id")

        if not carousel_url:
            logger.error(
                "[CAROUSEL_BG] No carousel URL returned from Gamma AI")
            return {
                "success": False,
                "carousel_url": None,
                "carousel_id": None,
                "topic": topic,
                "error": "Carousel generation returned no URL",
            }

        # Save record to content library
        content_id = None
        try:
            insert_data: dict = {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "title": f"Carousel: {topic[:50]}",
                "content": carousel_url,
                "content_type": "carousel",
                "status": "draft",
                "metadata": {
                    "topic": topic,
                    "style": style,
                    "slide_count": slide_count,
                    "source": "gamma_ai",
                    "carousel_id": carousel_id,
                },
            }
            saved = db.table("content").insert(insert_data).execute()
            content_id = saved.data[0]["id"] if saved.data else None
            logger.info(f"[CAROUSEL_BG] Content record created: {content_id}")
        except Exception as exc:
            logger.warning(
                f"[CAROUSEL_BG] Could not save carousel record to content table: {exc}")

        logger.info(
            "[CAROUSEL_BG] Carousel generation completed successfully: "
            f"duration={((_time.monotonic() - t0) * 1000):.1f}ms"
        )

        return {
            "success": True,
            "carousel_url": carousel_url,
            "carousel_id": carousel_id,
            "content_id": content_id,
            "topic": topic,
            "error": None,
        }

    except Exception as exc:
        logger.exception(
            f"[CAROUSEL_BG] Carousel generation task failed: {exc}")
        return {
            "success": False,
            "carousel_url": None,
            "carousel_id": None,
            "content_id": None,
            "topic": topic[:100],
            "error": f"Background task failed: {str(exc)}",
        }
