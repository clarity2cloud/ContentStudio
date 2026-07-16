# app/services/credits_service.py
#
# Open-source stub: all functions are no-ops.
# Credits are disabled for free local development.

from typing import Optional, Dict, Any

CREDIT_COSTS: Dict[str, int] = {
    "post_generation": 6,
    "blog_generation": 20,
    "image": 8,
    "enhancing": 10,
    "carousel": 40,
    "campaign": 100,
    "rewrite": 5,
    "scheduling": 1,
    "analytics": 2,
    "content_generation": 15,
    "viral_intel": 50,
    "hook": 20,
    "reel_script": 30,
    "content_map": 10,
    "repurpose": 6,
    "chat_message": 6,
}

PLAN_CREDITS: Dict[str, int] = {
    "free": 100,
    "starter": 1200,
    "pro": 5000,
    "growth": 20000,
}


def get_credit_cost(action: str) -> int:
    """Return credit cost for an action (unused in open-source)."""
    return CREDIT_COSTS.get(action, 1)


async def get_credit_balance(
    tenant_id: str,
    auth_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Return unlimited credits for open-source development."""
    return {
        "tenant_id": tenant_id,
        "balance": 999999,
        "plan": "open-source-unlimited",
        "total_purchased": 999999,
        "total_used": 0,
        "subscription_active": True,
        "last_renewal_at": None,
    }


async def check_balance_for_action(
    action: str,
    tenant_id: str,
    auth_token: Optional[str] = None,
    required_cost: Optional[int] = None,
) -> None:
    """No-op: credits are unlimited in open-source."""
    pass


async def deduct_credits(
    tenant_id: str,
    action: str,
    amount: int,
    metadata: Optional[Dict] = None,
) -> Dict[str, Any]:
    """No-op: credits are unlimited in open-source."""
    return {
        "success": True,
        "credits_deducted": 0,
        "action": action,
        "new_balance": 999999,
    }


async def get_credit_transactions(
    tenant_id: str,
    limit: int = 50,
    offset: int = 0,
) -> Dict:
    """Return empty transaction list (unused in open-source)."""
    return {"transactions": [], "limit": limit, "offset": offset}


async def get_plans() -> Dict:
    """Return single unlimited plan for open-source."""
    return {
        "plans": ["open-source-unlimited"],
        "plan_credits": {"open-source-unlimited": 999999},
        "plans_detail": [{"plan_key": "open-source-unlimited", "credits_per_cycle": 999999}],
        "source": "open-source",
    }


async def check_and_deduct(
    action: str,
    user_id: str,
    bearer_token: str,
    db,
    metadata: Optional[Dict] = None,
) -> int:
    """No-op: credits are unlimited in open-source."""
    return 0


def _extract_tenant_id(bearer_token: str, user_id: str, db) -> str:
    """Return demo tenant ID (unused in open-source)."""
    return "demo-tenant"
