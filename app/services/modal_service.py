# app/services/nvidia_service.py  (kept as modal_service.py for import compatibility)
#
# All AI media generation via NVIDIA NIM — no Modal dependency.
#
#  Image  : FLUX.2-klein-4B
#           https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.2-klein-4b
#
#  Video  : Cosmos-1.0 Diffusion 7B Text2World
#           https://ai.api.nvidia.com/v1/genai/nvidia/cosmos-1-0-diffusion-7b-text2world

import httpx
from typing import Optional, Dict, Any

from app.config import settings
from app.utils.logger import logger

# ── NVIDIA NIM endpoints ────────────────────────────────────────────────
NVIDIA_IMAGE_URL = "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.2-klein-4b"
NVIDIA_VIDEO_URL = "https://ai.api.nvidia.com/v1/genai/nvidia/cosmos-1-0-diffusion-7b-text2world"

IMAGE_TIMEOUT = httpx.Timeout(connect=30.0, read=180.0, write=10.0, pool=5.0)
VIDEO_TIMEOUT = httpx.Timeout(connect=30.0, read=600.0, write=10.0, pool=5.0)


class ModalService:   # name kept for backward-compat with existing imports

    def _check_nvidia_key(self) -> None:
        if not settings.NVIDIA_API_KEY:
            raise RuntimeError(
                "NVIDIA_API_KEY is not configured. Add it to your .env file."
            )

    def _nvidia_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {settings.NVIDIA_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _post(self, url: str, payload: dict, timeout: httpx.Timeout,
                    headers: Optional[dict] = None) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers or {})
            if resp.status_code != 200:
                raise RuntimeError(
                    f"NVIDIA endpoint returned {resp.status_code}: {resp.text[:400]}")
            return resp.json()

    # ── Shared prompt builder (matches /generate/enhanced-image exactly) ────

    async def build_enhanced_image(
        self,
        user_input: str,
        style: Optional[str] = None,
        platform: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Match the exact prompt construction logic from /generate/enhanced-image.
        Returns dict with enhanced_prompt, width, height, size_str.
        """
        from app.services.ai_service import ai_service as _ai_svc

        # Step 1: enhance
        try:
            enhanced_prompt = await _ai_svc.enhance_image_prompt_fast(
                prompt=user_input, style=style, platform=platform
            )
        except Exception:
            enhanced_prompt = user_input

        # Step 2: resolve dimensions (same _PLATFORM_PRESETS as media.py)
        _PLATFORM_PRESETS = {
            "instagram": (1080, 1080), "instagram_story": (720, 1456),
            "story": (720, 1456), "twitter": (1328, 800),
            "linkedin": (1328, 800), "blog": (1328, 800),
            "facebook": (1328, 800), "pinterest": (800, 1328),
            "youtube": (1328, 800),
        }
        if platform and platform.lower() in _PLATFORM_PRESETS:
            img_w, img_h = _PLATFORM_PRESETS[platform.lower()]
        else:
            img_w, img_h = 1024, 1024

        # Step 3: build final prompt (exact same logic as media.py:1155-1165)
        full_prompt = enhanced_prompt.strip()
        if style and not full_prompt.endswith(style):
            full_prompt = f"{full_prompt}, {style}"
        quality_suffix = ", high quality, sharp focus, professional"
        if "photorealistic" not in full_prompt.lower(
        ) and "cinematic" not in full_prompt.lower():
            full_prompt = full_prompt + quality_suffix
        full_prompt += ", no text, no words, no letters, no signs, no captions, no watermarks, no typography"
        full_prompt = full_prompt.replace("\n", " ").replace("\r", "")
        full_prompt = full_prompt[:800].rstrip(", ")

        return {
            "enhanced_prompt": full_prompt,
            "width": img_w,
            "height": img_h,
            "size_str": f"{img_w}x{img_h}",
        }

    # ── Image Generation — NVIDIA FLUX.2-klein-4B ───────────────────────────

    async def generate_image(
        self,
        prompt: str,
        negative_prompt: str = "",
        size: str = "1024x1024",
        num_inference_steps: int = 4,
        guidance_scale: float = 0.0,
        seed: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Generate an image via NVIDIA NIM FLUX.2-klein-4B.
        Returns dict with image_base64, mime_type, width, height, seed, model.
        """
        self._check_nvidia_key()

        try:
            w, h = [int(x) for x in size.split("x")]
        except Exception:
            w, h = 1024, 1024

        # FLUX.2-klein-4B only accepts: prompt, width, height, seed
        payload: Dict[str, Any] = {"prompt": prompt, "width": w, "height": h}
        if seed is not None:
            payload["seed"] = seed

        logger.info(f"NVIDIA image gen — prompt='{prompt[:60]}' size={size}")

        # Retry up to 3 times on transient failures
        max_retries = 3
        for attempt in range(max_retries):
            try:
                data = await self._post(NVIDIA_IMAGE_URL, payload, IMAGE_TIMEOUT, self._nvidia_headers())
                logger.info("NVIDIA image gen complete")
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # 1s, 2s, 4s
                    logger.warning(f"NVIDIA image gen attempt {attempt + 1} failed ({e}), retrying in {wait_time}s...")
                    import asyncio
                    await asyncio.sleep(wait_time)
                else:
                    raise

        # NVIDIA returns: {"artifacts": [{"base64": "...", "seed": 123}]}
        if "artifacts" in data and data["artifacts"]:
            artifact = data["artifacts"][0]
            image_b64 = artifact.get("base64", "")
            returned_seed = artifact.get("seed", seed or 0)
        elif "image_base64" in data:
            image_b64 = data["image_base64"]
            returned_seed = data.get("seed", seed or 0)
        else:
            raise RuntimeError(
                f"Unexpected NVIDIA image response keys: {list(data.keys())}")

        if not image_b64:
            raise RuntimeError(
                f"NVIDIA returned empty image_base64. Response keys: {list(data.keys())}")

        return {
            "image_base64": image_b64,
            "mime_type": "image/png",
            "width": w,
            "height": h,
            "seed": returned_seed,
            "model": "black-forest-labs/FLUX.2-klein-4B",
            "prompt": prompt,
        }

    # ── Video Generation — NVIDIA Cosmos 1.0 Text2World ─────────────────────

    async def generate_video(
        self,
        prompt: str,
        negative_prompt: str = "blurry, distorted, low quality, jittery",
        resolution: str = "1280x704",
        num_frames: int = 121,
        guidance_scale: float = 7.0,
        fps: int = 24,
        seed: Optional[int] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Generate a video via NVIDIA NIM Cosmos-1.0 Diffusion 7B Text2World.
        Returns dict with video_base64, mime_type, resolution, seed, model.
        """
        self._check_nvidia_key()

        payload: Dict[str, Any] = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "resolution": resolution,
            "cfg_scale": guidance_scale,
        }
        if seed is not None:
            payload["seed"] = seed

        logger.info(
            f"NVIDIA video gen — prompt='{prompt[:60]}' resolution={resolution}")
        data = await self._post(NVIDIA_VIDEO_URL, payload, VIDEO_TIMEOUT, self._nvidia_headers())
        logger.info("NVIDIA video gen complete")

        # Cosmos returns: {"video": "<base64_mp4>"} or {"artifacts": [...]}
        if "video" in data:
            video_b64 = data["video"]
            returned_seed = data.get("seed", seed or 0)
        elif "artifacts" in data and data["artifacts"]:
            artifact = data["artifacts"][0]
            video_b64 = artifact.get("base64", "")
            returned_seed = artifact.get("seed", seed or 0)
        else:
            raise RuntimeError(
                f"Unexpected NVIDIA video response keys: {list(data.keys())}")

        return {
            "video_base64": video_b64,
            "mime_type": "video/mp4",
            "resolution": resolution,
            "seed": returned_seed,
            "model": "nvidia/cosmos-1-0-diffusion-7b-text2world",
            "prompt": prompt,
        }

    # ── Health checks ───────────────────────────────────────────────────────

    async def check_image_health(self) -> Dict[str, Any]:
        if not settings.NVIDIA_API_KEY:
            return {
                "status": "not_configured",
                "model": "FLUX.2-klein-4B",
                "provider": "NVIDIA NIM"}
        return {
            "status": "ok",
            "model": "black-forest-labs/FLUX.2-klein-4B",
            "provider": "NVIDIA NIM"}

    async def check_video_health(self) -> Dict[str, Any]:
        if not settings.NVIDIA_API_KEY:
            return {
                "status": "not_configured",
                "model": "cosmos-1-0-diffusion-7b-text2world",
                "provider": "NVIDIA NIM"}
        return {
            "status": "ok",
            "model": "nvidia/cosmos-1-0-diffusion-7b-text2world",
            "provider": "NVIDIA NIM"}


modal_service = ModalService()
