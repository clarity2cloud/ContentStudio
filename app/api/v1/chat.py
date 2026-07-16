# app/api/v1/chat.py
"""
Conversational AI agent endpoints.

Threads are persistent conversation sessions. Each turn runs the full
agent loop: LLM → tool calls (parallel) → final response, all saved to Appwrite.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.db.appwrite_client import AppwriteDB
from app.utils.logger import logger
import app.services.agent_service as agent_svc

router = APIRouter(prefix="/chat", tags=["AI Chat Agent"])

_db = AppwriteDB()


# ── Request / Response models ─────────────────────────────────────────────────

class CreateThreadRequest(BaseModel):
    title: Optional[str] = Field(None, description="Thread title — auto-generated from first message if omitted")
    brand_id: Optional[str] = Field(None, description="Brand to attach context from")


class SendMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, description="User message to the agent")
    brand_id: Optional[str] = Field(None, description="Brand ID for context")


class UpdateThreadRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


# ── Brand context helpers ─────────────────────────────────────────────────────

def _load_brand_context(brand_id: str, user_id: str) -> Optional[str]:
    """
    Fetch brand from Appwrite and build a rich brand-context block for the LLM.
    Uses brand_validator.build_brand_block() — the single source of truth for
    brand context — so chat sees exactly the same brand data as every other
    generation path (AI gen, campaigns, platform-native).
    """
    from app.services import brand_validator as _bv
    from app.services.cache_service import cache, brand_context_key
    try:
        cached = cache.get(brand_context_key(brand_id))
        if isinstance(cached, str) and cached:
            return cached

        brand = _db.get_document("brand_profiles", brand_id)
        if not brand or brand.get("user_id") != user_id:
            return None

        block = _bv.build_brand_block(brand)
        if block:
            cache.set(brand_context_key(brand_id), block, ttl=3600)
        return block or None
    except Exception as e:
        logger.warning(f"[CHAT] Could not load brand {brand_id}: {e}")
        return None


def _resolve_default_brand_id(user_id: str) -> Optional[str]:
    """Return the user's default brand_id, or None if they have no brands."""
    from app.services.cache_service import cache
    cache_key = f"default_brand:{user_id}"
    try:
        cached = cache.get(cache_key)
        if isinstance(cached, str) and cached:
            return cached
        res = (
            _db.list_documents("brand_profiles", queries=[
                {"method": "equal", "attribute": "user_id",    "values": [user_id]},
                {"method": "equal", "attribute": "is_default", "values": [True]},
                {"method": "limit", "values": [1]},
            ])
        )
        docs = res.get("documents", [])
        if docs:
            bid = docs[0].get("$id") or docs[0].get("id")
            if bid:
                cache.set(cache_key, bid, ttl=3600)
                return bid
    except Exception as e:
        logger.debug(f"[CHAT] Default brand lookup failed: {e}")
    return None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/threads", summary="Create a new conversation thread")
async def create_thread(
    body: CreateThreadRequest = None,
    
    
):
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    if body is None:
        body = CreateThreadRequest()
    # Resolve brand: explicit body.brand_id → user's default brand
    effective_brand_id = body.brand_id or _resolve_default_brand_id(user_id)
    thread = agent_svc.create_thread(
        user_id, tenant_id,
        title=body.title,
        brand_id=effective_brand_id,
    )
    return {"thread": thread}


@router.get("/threads", summary="List all conversation threads for the current user")
async def list_threads(
    limit: int = Query(50, ge=1, le=100),


):
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    threads = agent_svc.list_threads(user_id, tenant_id, limit=limit)
    return {"threads": threads, "count": len(threads)}


@router.get("/threads/{thread_id}", summary="Get thread metadata and message history",
    responses={
                404: {"description": "Not found"}
    }
)
async def get_thread(
    thread_id: str,
    
):
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    thread = agent_svc.get_thread(thread_id, user_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    messages = agent_svc.get_thread_messages(thread_id)
    return {"thread": thread, "messages": messages}


@router.get("/threads/{thread_id}/messages", summary="Get messages in a thread",
    responses={
                404: {"description": "Not found"}
    }
)
async def get_messages(
    thread_id: str,
    limit: int = Query(100, ge=1, le=200),

):
    user_id = "demo-user"
    thread = agent_svc.get_thread(thread_id, user_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    messages = agent_svc.get_thread_messages(thread_id, limit=limit)
    return {"messages": messages, "count": len(messages)}


@router.post("/threads/{thread_id}/messages", summary="Send a message — runs the full agent loop",
    responses={
                404: {"description": "Not found"}
    }
)
async def send_message(
    request: Request,
    thread_id: str,
    body: SendMessageRequest,
    
    
):
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    thread = agent_svc.get_thread(thread_id, user_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    # ── Brand resolution (priority order) ────────────────────────────────────
    # 1. Explicit brand_id on this message  (user switched brand mid-thread)
    # 2. brand_id stored on the thread      (set at thread creation)
    # 3. User's default brand               (automatic if brand profile exists)
    # 4. No brand → LLM still generates, just without brand identity
    effective_brand_id = (
        body.brand_id
        or thread.get("brand_id")
        or _resolve_default_brand_id(user_id)
    )

    brand_context: Optional[str] = None
    if effective_brand_id:
        brand_context = _load_brand_context(effective_brand_id, user_id)
        if brand_context:
            logger.info(f"[CHAT] Brand context loaded: {effective_brand_id} for thread={thread_id}")
        else:
            logger.warning(f"[CHAT] Brand {effective_brand_id} resolved but context empty")

    bearer_token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()

    # Pre-check: ensure user has at least the minimum per-message credit (6)
    # before kicking off the full LLM call. Actual word-based deduction happens
    # inside run_agent_turn after we know the response word count.

    result = await agent_svc.run_agent_turn(
        thread_id=thread_id,
        user_message=body.message,
        user_id=user_id,
        tenant_id=tenant_id,
        brand_context=brand_context,
        bearer_token=bearer_token,
    )
    return result


@router.patch("/threads/{thread_id}", summary="Rename a thread",
    responses={
                404: {"description": "Not found"}
    }
)
async def update_thread(
    thread_id: str,
    body: UpdateThreadRequest,
    
):
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    ok = agent_svc.update_thread_title(thread_id, user_id, body.title)
    if not ok:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"success": True}


@router.delete("/threads/{thread_id}", summary="Archive (soft-delete) a thread",
    responses={
                404: {"description": "Not found"}
    }
)
async def delete_thread(
    thread_id: str,
    
):
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    ok = agent_svc.delete_thread(thread_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"success": True}
