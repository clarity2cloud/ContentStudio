# app/models/scheduled_post.py
from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime, timezone
from enum import Enum


# ── Enums ───────────────────────────────────────────────────────────────

class ScheduleStatus(str, Enum):
    DRAFT = "draft"       # saved but not queued
    SCHEDULED = "scheduled"   # queued in APScheduler / Celery ETA
    # dispatched to Celery worker (Beat safety-net picked it up)
    QUEUED = "queued"
    PUBLISHING = "publishing"  # in-flight (worker executing)
    PUBLISHED = "published"   # successfully sent
    FAILED = "failed"      # all retries exhausted
    CANCELLED = "cancelled"   # user cancelled
    RETRYING = "retrying"    # retry in progress


class SchedulePlatform(str, Enum):
    TWITTER = "twitter"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    LINKEDIN = "linkedin"
    TIKTOK = "tiktok"
    YOUTUBE = "youtube"
    PINTEREST = "pinterest"
    THREADS = "threads"


class MediaType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    CAROUSEL = "carousel"
    STORY = "story"
    REEL = "reel"
    TEXT = "text"


class DayOfWeek(int, Enum):
    MONDAY = 0
    TUESDAY = 1
    WEDNESDAY = 2
    THURSDAY = 3
    FRIDAY = 4
    SATURDAY = 5
    SUNDAY = 6


# ── Request Models ──────────────────────────────────────────────────────

class SchedulePostRequest(BaseModel):
    """Schedule a post — can reference existing content or provide text inline."""
    platform: SchedulePlatform
    scheduled_at: datetime = Field(...,
                                   description="ISO 8601 UTC datetime — must be in the future")
    timezone: str = Field(
        "UTC", description="IANA timezone, e.g. America/New_York")

    # Content: either link an existing content item or write inline
    content_id: Optional[str] = Field(
        None, description="ID of an existing content library item")
    content_text: Optional[str] = Field(
        None, description="Post text (required if content_id is omitted)")
    title: Optional[str] = None

    # Media
    media_urls: Optional[List[str]] = None
    media_type: Optional[MediaType] = None

    # Metadata
    hashtags: Optional[List[str]] = None
    campaign_id: Optional[str] = None
    brand_id: Optional[str] = None
    connected_account_id: Optional[str] = None
    max_retries: int = Field(3, ge=0, le=10)

    @validator("scheduled_at")
    def must_be_future(cls, v):
        now = datetime.now(timezone.utc)
        if v.tzinfo is None:
            now = datetime.utcnow()
        if v <= now:
            raise ValueError("scheduled_at must be in the future")
        return v

    @validator("content_text", always=True)
    def content_required(cls, v, values):
        if not v and not values.get("content_id"):
            raise ValueError("Provide either content_id or content_text")
        return v


class UpdateScheduleRequest(BaseModel):
    """Patch a scheduled post."""
    scheduled_at: Optional[datetime] = None
    content_text: Optional[str] = None
    hashtags: Optional[List[str]] = None
    media_urls: Optional[List[str]] = None
    status: Optional[ScheduleStatus] = None

    @validator("scheduled_at")
    def must_be_future(cls, v):
        if v:
            now = datetime.now(timezone.utc)
            if v.tzinfo is None:
                now = datetime.utcnow()
            if v <= now:
                raise ValueError("scheduled_at must be in the future")
        return v


class BulkScheduleItem(BaseModel):
    platform: SchedulePlatform
    content_text: str
    scheduled_at: datetime
    hashtags: Optional[List[str]] = None
    media_urls: Optional[List[str]] = None
    media_type: Optional[MediaType] = None


class BulkScheduleRequest(BaseModel):
    posts: List[BulkScheduleItem] = Field(..., min_items=1, max_items=50)
    timezone: str = "UTC"
    campaign_id: Optional[str] = None
    brand_id: Optional[str] = None


class QueueSlotCreate(BaseModel):
    """Recurring time slot in the posting queue (Buffer-style)."""
    platform: SchedulePlatform
    day_of_week: DayOfWeek = Field(..., description="0=Monday … 6=Sunday")
    time_of_day: str = Field(...,
                             description="HH:MM (24 h)",
                             pattern=r"^\d{2}:\d{2}$")
    timezone: str = "UTC"
    label: Optional[str] = None
    brand_id: Optional[str] = None
    is_active: bool = True


class AIGenerateScheduleRequest(BaseModel):
    """Generate content with AI and schedule it immediately."""
    platform: SchedulePlatform
    topic: str
    scheduled_at: datetime
    timezone: str = "UTC"
    tone: str = "casual"
    include_hashtags: bool = True
    include_emojis: bool = True
    campaign_id: Optional[str] = None
    brand_id: Optional[str] = None
    custom_instructions: Optional[str] = None
    max_retries: int = 3

    @validator("scheduled_at")
    def must_be_future(cls, v):
        now = datetime.now(timezone.utc)
        if v.tzinfo is None:
            now = datetime.utcnow()
        if v <= now:
            raise ValueError("scheduled_at must be in the future")
        return v


class AIFillWeekRequest(BaseModel):
    """AI generates + schedules a full week of posts for a platform."""
    platform: SchedulePlatform
    topic: str
    posts_per_week: int = Field(3, ge=1, le=7)
    start_date: datetime = Field(...,
                                 description="First day of the week to fill (ISO 8601)")
    timezone: str = "UTC"
    tone: str = "casual"
    include_hashtags: bool = True
    campaign_id: Optional[str] = None
    brand_id: Optional[str] = None
    custom_instructions: Optional[str] = None


class QueueFillRequest(BaseModel):
    """Fill upcoming empty queue slots with AI-generated content."""
    topic: str
    days_ahead: int = Field(7, ge=1, le=30)
    tone: str = "casual"
    brand_id: Optional[str] = None
    custom_instructions: Optional[str] = None


class OptimizeContentRequest(BaseModel):
    """Rewrite existing content to be native for a target platform."""
    content_text: str
    target_platform: SchedulePlatform
    include_hashtags: bool = True
    include_emojis: bool = True
    brand_context: Optional[str] = None


class HashtagRequest(BaseModel):
    content_text: str
    platform: SchedulePlatform
    count: int = Field(10, ge=3, le=30)


# ── Response Models ─────────────────────────────────────────────────────

class ScheduledPostResponse(BaseModel):
    id: str
    user_id: str
    platform: str
    content_text: Optional[str] = None
    title: Optional[str] = None
    media_urls: Optional[List[str]] = None
    media_type: Optional[str] = None
    hashtags: Optional[List[str]] = None
    scheduled_at: Optional[str] = None
    timezone: str = "UTC"
    status: str
    campaign_id: Optional[str] = None
    brand_id: Optional[str] = None
    content_id: Optional[str] = None
    connected_account_id: Optional[str] = None
    published_at: Optional[str] = None
    platform_post_id: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


class QueueSlotResponse(BaseModel):
    id: str
    user_id: str
    platform: str
    day_of_week: int
    time_of_day: str
    timezone: str
    label: Optional[str] = None
    brand_id: Optional[str] = None
    is_active: bool
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


class BestTimeSlot(BaseModel):
    day_of_week: int   # 0=Mon … 6=Sun
    day_label: str   # "Monday"
    hour: int   # 0–23
    time_label: str   # "9:00 AM"
    score: float  # 0–10
    reason: str


class BestTimesResponse(BaseModel):
    platform: str
    recommendations: List[BestTimeSlot]
    summary: str
    generated_at: str
