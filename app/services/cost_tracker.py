# app/services/cost_tracker.py
"""
DISABLED — cost tracking & quotas were removed by product request.

The credit system (app/services/credits_service.py) is the single user-facing
spending guard. This module is kept as a no-op stub so any historical imports
remain valid and do nothing.
"""

from typing import Dict


# Public constants kept for backward compatibility (always 0 = disabled).
DAILY_TOKEN_QUOTA = 0
DAILY_REQUEST_QUOTA = 0
DAILY_COST_USD_QUOTA = 0.0


def check_quota(tenant_id: str) -> None:
    """No-op. Quota enforcement was removed."""
    return None


def record_call(**kwargs) -> Dict:
    """No-op. Cost tracking was removed."""
    return {"tokens": 0, "cost_usd": 0.0}


def estimate_cost_usd(
        model: str,
        prompt_tokens: int,
        completion_tokens: int) -> float:
    """No-op estimator — always returns 0."""
    return 0.0


def get_today_usage(tenant_id: str) -> Dict:
    return {
        "tenant_id": tenant_id,
        "disabled": True,
        "note": "Cost tracking disabled. Credits are the user-facing spending guard.",
    }


def get_month_usage(tenant_id: str) -> Dict:
    return {
        "tenant_id": tenant_id,
        "disabled": True,
    }


def get_global_today() -> Dict:
    return {
        "disabled": True,
        "note": "Cost tracking disabled. Credits are the user-facing spending guard.",
    }
