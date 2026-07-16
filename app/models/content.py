from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum


class ContentType(str, Enum):
    """Content type enum"""
    BLOG = "blog"
    TWEET = "tweet"
    EMAIL = "email"
    INSTAGRAM_CAPTION = "instagram_caption"
    LINKEDIN_POST = "linkedin_post"
    FACEBOOK_POST = "facebook_post"
    IMAGE = "image"
    CAROUSEL = "carousel"
    TIKTOK = "tiktok"
    YOUTUBE_SHORTS = "youtube_shorts"
    YOUTUBE = "youtube"
    PINTEREST = "pinterest"
    THREADS = "threads"
    SNAPCHAT = "snapchat"
    NEWSLETTER = "newsletter"
    PODCAST = "podcast"
    WEBINAR = "webinar"
    SMS = "sms"
    WHATSAPP = "whatsapp"
    LINKEDIN_ARTICLE = "linkedin_article"
    PRESS_RELEASE = "press_release"
    LANDING_PAGE = "landing_page"
    GOOGLE_ADS = "google_ads"
    META_ADS = "meta_ads"
    LINKEDIN_ADS = "linkedin_ads"
    HOOK = "hook"
    REEL_SCRIPT = "reel_script"
    VIRAL_INTEL = "viral_intel"


class ContentStatus(str, Enum):
    """Content status enum"""
    DRAFT = "draft"
    PUBLISHED = "published"
    SCHEDULED = "scheduled"
    DELETED = "deleted"


class Tone(str, Enum):
    """Writing tone enum"""
    PROFESSIONAL = "professional"
    CASUAL = "casual"
    FRIENDLY = "friendly"
    FORMAL = "formal"
    HUMOROUS = "humorous"
    INSPIRATIONAL = "inspirational"
    BOLD = "bold"
    EMPATHETIC = "empathetic"
    AUTHORITATIVE = "authoritative"
    EDUCATIONAL = "educational"
    URGENCY = "urgency"
    STORYTELLING = "storytelling"
    DATA_DRIVEN = "data_driven"


class ContentFormat(str, Enum):
    """Content format/style enum"""
    STANDARD = "standard"
    QUESTION = "question"
    LISTICLE = "listicle"
    HOW_TO = "how-to"
    POWER_WORD = "power_word"
    CURIOSITY_GAP = "curiosity_gap"
    STORYTELLING = "storytelling"
    DATA_DRIVEN = "data_driven"
    EMOTIONAL = "emotional"


# ==================== REQUEST MODELS ====================

class GenerateBlogRequest(BaseModel):
    """Generate blog post request"""
    topic: str = Field(..., min_length=3, max_length=500,
                       description="Blog topic")
    keywords: Optional[List[str]] = Field(
        default=[], description="Keywords to include")
    tone: Tone = Field(default=Tone.PROFESSIONAL, description="Writing tone")
    format: Optional[ContentFormat] = Field(
        default=ContentFormat.STANDARD,
        description="Content format style")
    word_count: Optional[int] = Field(
        default=500,
        ge=100,
        le=3000,
        description="Approximate word count")
    custom_instructions: Optional[str] = Field(
        default=None,
        max_length=10000,
        description="Additional custom instructions")


class GenerateTweetRequest(BaseModel):
    """Generate tweet request"""
    topic: str = Field(..., min_length=3, max_length=200,
                       description="Tweet topic")
    tone: Tone = Field(default=Tone.CASUAL, description="Writing tone")
    format: Optional[ContentFormat] = Field(
        default=ContentFormat.STANDARD,
        description="Content format style")
    include_hashtags: bool = Field(
        default=True, description="Include hashtags")
    include_emojis: bool = Field(default=True, description="Include emojis")
    custom_instructions: Optional[str] = Field(
        default=None,
        max_length=10000,
        description="Additional custom instructions")


class GenerateEmailRequest(BaseModel):
    """Generate email request"""
    subject: str = Field(..., min_length=3, max_length=200,
                         description="Email subject")
    purpose: str = Field(..., min_length=10, max_length=500,
                         description="Email purpose/context")
    tone: Tone = Field(default=Tone.PROFESSIONAL, description="Writing tone")
    format: Optional[ContentFormat] = Field(
        default=ContentFormat.STANDARD,
        description="Content format style")
    recipient_name: Optional[str] = Field(
        default=None, description="Recipient name")
    custom_instructions: Optional[str] = Field(
        default=None,
        max_length=10000,
        description="Additional custom instructions")


class GenerateCaptionRequest(BaseModel):
    """Generate social media caption request"""
    platform: str = Field(...,
                          description="Platform (instagram, facebook, linkedin)")
    context: str = Field(..., min_length=10, max_length=500,
                         description="Image/post context")
    tone: Tone = Field(default=Tone.CASUAL, description="Writing tone")
    format: Optional[ContentFormat] = Field(
        default=ContentFormat.STANDARD,
        description="Content format style")
    include_hashtags: bool = Field(
        default=True, description="Include hashtags")
    include_emojis: bool = Field(default=True, description="Include emojis")
    custom_instructions: Optional[str] = Field(
        default=None,
        max_length=10000,
        description="Additional custom instructions")


class GenerateMultiSocialRequest(BaseModel):
    """Generate content for multiple platforms at once"""
    platforms: List[str] = Field(
        ..., description="List of platforms (instagram, facebook, linkedin, twitter)")
    topic: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Topic or keywords. If omitted or empty, AI will intelligently suggest one based on your brand.")
    tone: Tone = Field(default=Tone.CASUAL, description="Writing tone")
    format: Optional[ContentFormat] = Field(
        default=ContentFormat.STANDARD,
        description="Content format style")
    include_hashtags: bool = Field(
        default=True, description="Include hashtags")
    include_emojis: bool = Field(default=True, description="Include emojis")
    custom_instructions: Optional[str] = Field(
        default=None,
        max_length=10000,
        description="Additional custom instructions")


class ValidationReport(BaseModel):
    """Validation report from content generation"""
    meta_commentary_removed: bool = True
    hallucinations_flagged: bool = False
    hallucinations_count: int = 0
    unverified_stats: int = 0
    extreme_claims: int = 0
    hallucination_details: List[dict] = []
    structure_valid: bool = True
    structure_issues: List[str] = []
    tone_consistent: bool = True
    tone_issues: List[str] = []
    brand_consistent: bool = True
    brand_issues: List[str] = []
    overall_quality_score: str = "excellent"  # excellent, good, fair, needs_review


class CreateContentRequest(BaseModel):
    """Create content manually or from generation"""
    title: Optional[str] = Field(default=None, max_length=500)
    content: str = Field(..., min_length=1)
    content_type: ContentType
    status: ContentStatus = Field(default=ContentStatus.DRAFT)
    metadata: Optional[dict] = Field(default={})
    image_url: Optional[str] = Field(default=None)
    campaign_id: Optional[str] = Field(default=None)
    brand_id: Optional[str] = Field(
        default=None, description="Associated brand")
    validation: Optional[ValidationReport] = Field(
        default=None, description="Validation report from generation")


class UpdateContentRequest(BaseModel):
    """Update content request"""
    title: Optional[str] = Field(default=None, max_length=500)
    content: Optional[str] = None
    status: Optional[ContentStatus] = None
    metadata: Optional[dict] = None


# ==================== RESPONSE MODELS ====================

class ContentResponse(BaseModel):
    """Content response model"""
    id: str
    user_id: str
    title: Optional[str] = None
    content: str
    content_type: str
    status: str
    metadata: dict = {}
    image_url: Optional[str] = None
    image_base64: Optional[str] = None
    campaign_id: Optional[str] = None
    brand_id: Optional[str] = None
    validation: Optional[dict] = None  # Validation report from generation
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class GenerateContentResponse(BaseModel):
    """Generated content response"""
    content: str
    title: Optional[str] = None
    metadata: dict = {}
    saved: bool = False
    content_id: Optional[str] = None


class GenerateMultiSocialResponse(BaseModel):
    """Generated multi-platform content response"""
    topic_used: str
    results: List[GenerateContentResponse]


class ContentListResponse(BaseModel):
    """List of content items"""
    items: List[ContentResponse]
    total: int
    page: int
    page_size: int
