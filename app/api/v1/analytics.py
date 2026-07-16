from fastapi import APIRouter, HTTPException, Depends, Query
from typing import List, Optional, Annotated
from datetime import datetime, timezone
from app.models.analytics import (
    EngagementTrend, AnalyticsPlatform,
    PostAnalyticsResponse
)
from app.services.analytics_service import analytics_service
from app.core.database import get_db
from app.db.appwrite_client import AppwriteClient
from app.utils.logger import logger

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/overview", summary="Get analytics overview",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def get_analytics_overview(

    db: AppwriteClient = Depends(get_db),
):
    """
    Get overall analytics overview for current user

    Returns:
    - Total posts published
    - Total content created
    - Scheduled posts count
    - Connected platforms
    - Platform-wise analytics
    - Recent posts
    """
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    try:
        # Return mock analytics data for demo mode
        return {
            "total_content": 0,
            "draft_count": 0,
            "published_count": 0,
            "scheduled_count": 0,
            "connected_platforms": [],
            "total_scheduled": 0,
            "posted_count": 0,
            "recent_posts": [],
            "platform_stats": {}
        }
    except Exception as e:
        logger.error(f"❌ Get analytics overview error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trends", response_model=List[EngagementTrend], summary="Get engagement trends",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def get_engagement_trends(

    db: AppwriteClient = Depends(get_db),
    days: int = Query(30, ge=1, le=365, description="Number of days to analyze"),
):
    """
    Get engagement trends over time

    - **days**: Number of days to analyze (1-365, default: 30)

    Returns daily engagement metrics (likes, comments, shares, impressions)
    """
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    try:
        # Return mock engagement trends
        return [
            {"date": "2026-07-07", "likes": 0, "comments": 0, "shares": 0, "impressions": 0}
        ]
    except Exception as e:
        logger.error(f"❌ Get engagement trends error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/top-content", summary="Get top performing content",
    responses={
                500: {"description": "Internal server error"}
    }
)
async def get_top_performing_content(

    db: AppwriteClient = Depends(get_db),
    limit: int = Query(10, ge=1, le=50, description="Number of top posts to return"),
    platform: Optional[AnalyticsPlatform] = Query(None, description="Filter by platform"),
):
    """
    Get top performing content by engagement rate

    - **limit**: Number of posts to return (1-50, default: 10)
    - **platform**: Filter by platform (optional)

    Returns posts sorted by engagement rate (highest first)
    """
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    try:
        # Return mock top content
        return []

    except Exception as e:
        logger.error(f"❌ Get top content error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/post/{post_id}", response_model=PostAnalyticsResponse, summary="Get post analytics",
    responses={
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def get_post_analytics(
    post_id: str,

    db: AppwriteClient = Depends(get_db),
):
    """
    Get analytics for a specific post

    - **post_id**: Scheduled post ID

    Returns detailed analytics for the post
    """
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    try:
        # Return mock post analytics
        return PostAnalyticsResponse(
            id=post_id,
            scheduled_post_id=post_id,
            platform="twitter",
            likes=0,
            comments=0,
            shares=0,
            impressions=0,
            engagement_rate=0.0,
            fetched_at=datetime.now(timezone.utc),
            metadata={}
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Get post analytics error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/refresh/{post_id}", summary="Refresh post analytics",
    responses={
                404: {"description": "Not found"},
                500: {"description": "Internal server error"}
    }
)
async def refresh_post_analytics(
    post_id: str,

    db: AppwriteClient = Depends(get_db),
):
    """
    Manually refresh analytics for a specific post

    - **post_id**: Scheduled post ID

    Fetches latest analytics from the social platform
    """
    user_id = "demo-user"
    tenant_id = "demo-tenant"
    try:
        # Return mock refresh result
        return {
            "message": "Analytics refreshed successfully",
            "post_id": post_id,
            "analytics": {
                "id": post_id,
                "platform": "twitter",
                "likes": 0,
                "comments": 0,
                "shares": 0,
                "impressions": 0
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Refresh analytics error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))