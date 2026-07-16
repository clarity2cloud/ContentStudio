from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class AnalyticsPlatform(str, Enum):
    """Analytics platform enum"""
    TWITTER = "twitter"
    INSTAGRAM = "instagram"
    LINKEDIN = "linkedin"
    FACEBOOK = "facebook"
    ALL = "all"


class TimeRange(str, Enum):
    """Time range for analytics"""
    TODAY = "today"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"
    ALL_TIME = "all_time"


# ==================== RESPONSE MODELS ====================

class PostAnalyticsResponse(BaseModel):
    """Individual post analytics"""
    id: str
    scheduled_post_id: str
    platform: str
    likes: int = 0
    comments: int = 0
    shares: int = 0
    impressions: int = 0
    engagement_rate: float = 0.0
    fetched_at: datetime
    metadata: Dict[str, Any] = {}

    class Config:
        from_attributes = True


class PlatformAnalytics(BaseModel):
    """Analytics summary for a platform"""
    platform: str
    total_posts: int
    total_likes: int
    total_comments: int
    total_shares: int
    total_impressions: int
    avg_engagement_rate: float
    best_performing_post: Optional[Dict[str, Any]] = None


class OverallAnalytics(BaseModel):
    """Overall analytics across all platforms"""
    total_posts: int
    total_content_created: int
    total_scheduled: int
    total_published: int
    platforms_connected: List[str]
    platform_analytics: List[PlatformAnalytics]
    recent_posts: List[Dict[str, Any]]


class EngagementTrend(BaseModel):
    """Engagement trend data"""
    date: str
    likes: int
    comments: int
    shares: int
    impressions: int


class AnalyticsDashboard(BaseModel):
    """Complete analytics dashboard"""
    overview: OverallAnalytics
    engagement_trends: List[EngagementTrend]
    top_performing_content: List[Dict[str, Any]]
    platform_breakdown: Dict[str, Any]
