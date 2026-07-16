# app/api/v1/admin_usage.py
"""
Admin / Engineering Observability Endpoints
═══════════════════════════════════════════════════════════════════════════════

Read-only utilities for engineering & ops. Cost tracking and quotas were
removed by product request — credits remain the single user-facing spending
guard. The endpoints kept here are platform discovery + runtime health.

Endpoints:
  GET /admin/health             — runtime metrics (cache / Redis status)
  GET /admin/platform-profiles  — catalog of every supported platform + its
                                  specialist persona, voice rules, constraints
"""

from fastapi import APIRouter

from app.services.cache_service import cache
from app.services import platform_personas

router = APIRouter(prefix="/admin", tags=["Admin Observability"])


@router.get("/health", summary="Runtime health & cache stats")
async def admin_health():
    return {
        "cache":  cache.stats(),
    }


@router.get("/platform-profiles", summary="List supported platforms + their generation profiles")
async def platforms():
    """
    Returns every platform we natively support, with persona summary,
    constraints, and angle pool. Useful for the frontend to render an accurate
    platform selector.
    """
    out = []
    for name in platform_personas.list_all_platforms():
        prof = platform_personas.get_profile(name)
        out.append({
            "platform":         name,
            "persona":          prof.persona,
            "voice_directives": prof.voice_directives,
            "structure_rules":  prof.structure_rules,
            "constraints":      prof.constraints,
            "temperature":      prof.temperature,
            "max_tokens":       prof.max_tokens,
            "angle_pool":       prof.angle_pool,
            "model_tier":       prof.model_tier,
        })
    return {"platforms": out, "total": len(out)}
