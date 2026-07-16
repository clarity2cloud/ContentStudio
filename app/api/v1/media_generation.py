# app/api/v1/media_generation.py
#
# ⚠️  DEPRECATED — all endpoints here redirect to the canonical /media router.
#
# Use these instead:
#   Image generation  →  POST  /api/v1/media/generate/image
#   Carousel          →  POST  /api/v1/media/generate/social
#   Options / enums   →  GET   /api/v1/media/options
#
# This file is kept so existing integrations don't get hard 404s.

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter(
    prefix="/media-gen",
    tags=["AI Media Generation (Modal)"],
    deprecated=True,
)


_REDIRECT_MSG = (
    "This endpoint has been consolidated. "
    "Please use POST /api/v1/media/generate/image for image generation, "
    "POST /api/v1/media/generate/social for carousels, "
    "and GET /api/v1/media/options for available options."
)


@router.post("/generate/image",          summary="⚠️ Deprecated — use POST /media/generate/image")
@router.post("/generate/image/download", summary="⚠️ Deprecated — use POST /media/generate/image?download=true")
@router.post("/generate/video",          summary="⚠️ Deprecated")
@router.post("/generate/video/download", summary="⚠️ Deprecated")
async def deprecated_generate():
    raise HTTPException(
        status_code=410,
        detail=_REDIRECT_MSG,
    )


@router.get("/health",  summary="⚠️ Deprecated")
@router.get("/options", summary="⚠️ Deprecated — use GET /media/options")
async def deprecated_get():
    return JSONResponse(
        status_code=301,
        content={
            "moved": True,
            "message": _REDIRECT_MSG,
            "options_url": "/api/v1/media/options",
        },
    )
