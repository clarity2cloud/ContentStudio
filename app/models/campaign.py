# app/models/campaign.py
from datetime import datetime
from typing import Optional, List, Union
from pydantic import BaseModel, field_validator
from enum import Enum
from app.models.content import Tone


class CampaignStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    REVIEW = "review"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class CampaignObjective(str, Enum):
    # Awareness
    BRAND_AWARENESS = "brand_awareness"
    REACH = "reach"
    THOUGHT_LEADERSHIP = "thought_leadership"
    COMMUNITY_BUILDING = "community_building"
    # Consideration
    ENGAGEMENT = "engagement"
    WEBSITE_TRAFFIC = "website_traffic"
    VIDEO_VIEWS = "video_views"
    APP_INSTALLS = "app_installs"
    LEAD_GENERATION = "lead_generation"
    # Conversion
    SALES = "sales"
    CONVERSIONS = "conversions"
    PRODUCT_LAUNCH = "product_launch"
    RETARGETING = "retargeting"
    CART_ABANDONMENT = "cart_abandonment"
    # Loyalty / Retention
    CUSTOMER_RETENTION = "customer_retention"
    UPSELL_CROSS_SELL = "upsell_cross_sell"
    REFERRAL = "referral"
    # Events & PR
    EVENT_PROMOTION = "event_promotion"
    WEBINAR_PROMOTION = "webinar_promotion"
    PRESS_COVERAGE = "press_coverage"
    # Seasonal
    SEASONAL_PROMOTION = "seasonal_promotion"
    HOLIDAY_CAMPAIGN = "holiday_campaign"
    FLASH_SALE = "flash_sale"
    # Research
    SURVEY_FEEDBACK = "survey_feedback"
    MARKET_RESEARCH = "market_research"


class CampaignTargetAudience(str, Enum):
    # Professional roles
    MARKETING_PROFESSIONALS = "marketing_professionals"
    SALES_PROFESSIONALS = "sales_professionals"
    CONTENT_CREATORS = "content_creators"
    FOUNDERS_ENTREPRENEURS = "founders_entrepreneurs"
    C_SUITE_EXECUTIVES = "c_suite_executives"
    PRODUCT_MANAGERS = "product_managers"
    DEVELOPERS = "developers"
    DESIGNERS = "designers"
    HR_PROFESSIONALS = "hr_professionals"
    FINANCE_PROFESSIONALS = "finance_professionals"
    # Business segments
    SMALL_BUSINESS_OWNERS = "small_business_owners"
    MID_MARKET_BUSINESSES = "mid_market_businesses"
    ENTERPRISE_DECISION_MAKERS = "enterprise_decision_makers"
    STARTUPS = "startups"
    AGENCIES = "agencies"
    FREELANCERS = "freelancers"
    NONPROFITS = "nonprofits"
    # Consumer segments
    GENERAL_CONSUMERS = "general_consumers"
    TECH_ENTHUSIASTS = "tech_enthusiasts"
    EARLY_ADOPTERS = "early_adopters"
    MILLENNIALS = "millennials"
    GEN_Z = "gen_z"
    PARENTS = "parents"
    STUDENTS = "students"
    SENIORS = "seniors"
    # Interest-based
    SPORTS_FITNESS = "sports_fitness"
    FASHION_BEAUTY = "fashion_beauty"
    FOOD_BEVERAGE = "food_beverage"
    TRAVEL_HOSPITALITY = "travel_hospitality"
    HEALTH_WELLNESS = "health_wellness"
    GAMING = "gaming"
    SUSTAINABILITY = "sustainability"
    FINANCE_INVESTING = "finance_investing"


class CampaignCTA(str, Enum):
    # Awareness / Discovery
    LEARN_MORE = "learn_more"
    SEE_HOW = "see_how"
    EXPLORE_NOW = "explore_now"
    WATCH_NOW = "watch_now"
    READ_MORE = "read_more"
    # Lead capture
    SIGN_UP = "sign_up"
    GET_STARTED = "get_started"
    JOIN_FREE = "join_free"
    CLAIM_YOUR_SPOT = "claim_your_spot"
    REQUEST_DEMO = "request_demo"
    BOOK_A_CALL = "book_a_call"
    GET_QUOTE = "get_quote"
    APPLY_NOW = "apply_now"
    # Purchase
    BUY_NOW = "buy_now"
    SHOP_NOW = "shop_now"
    ORDER_NOW = "order_now"
    ADD_TO_CART = "add_to_cart"
    CLAIM_OFFER = "claim_offer"
    GET_DISCOUNT = "get_discount"
    START_FREE_TRIAL = "start_free_trial"
    # Download / Access
    DOWNLOAD = "download"
    GET_FREE_EBOOK = "get_free_ebook"
    ACCESS_NOW = "access_now"
    INSTALL_FREE = "install_free"
    # Engagement / Community
    SUBSCRIBE = "subscribe"
    FOLLOW_US = "follow_us"
    JOIN_COMMUNITY = "join_community"
    SHARE_NOW = "share_now"
    # Support / Contact
    CONTACT_US = "contact_us"
    TALK_TO_US = "talk_to_us"
    GET_HELP = "get_help"


class Channel(str, Enum):
    # Social media
    TWITTER = "twitter"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    LINKEDIN = "linkedin"
    LINKEDIN_ARTICLE = "linkedin_article"
    TIKTOK = "tiktok"
    YOUTUBE = "youtube"
    YOUTUBE_SHORTS = "youtube_shorts"
    PINTEREST = "pinterest"
    THREADS = "threads"
    SNAPCHAT = "snapchat"
    # Content
    BLOG = "blog"
    NEWSLETTER = "newsletter"
    EMAIL = "email"
    PODCAST = "podcast"
    WEBINAR = "webinar"
    # Paid / Ads
    GOOGLE_ADS = "google_ads"
    META_ADS = "meta_ads"
    LINKEDIN_ADS = "linkedin_ads"
    # Messaging
    WHATSAPP = "whatsapp"
    SMS = "sms"
    # Other
    PRESS_RELEASE = "press_release"
    LANDING_PAGE = "landing_page"
    # Visuals
    IMAGES = "images"


class CampaignCreate(BaseModel):
    name: str
    objective: Union[CampaignObjective, List[CampaignObjective], str]
    target_audience: Optional[CampaignTargetAudience] = None
    channels: List[Channel]
    cta: Optional[CampaignCTA] = None
    tone: Optional[Tone] = None
    brand_id: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    budget: Optional[float] = None   # in USD, optional
    notes: Optional[str] = None

    @field_validator("objective", mode="before")
    @classmethod
    def validate_objectives(cls, v):
        """Accept a single value, a list, or a comma-separated string — validate each."""
        valid = {e.value for e in CampaignObjective}
        if isinstance(v, list):
            for item in v:
                val = item.strip() if isinstance(
                    item, str) else (
                    item.value if hasattr(
                        item, "value") else str(item))
                if val not in valid:
                    raise ValueError(f"Invalid objective: '{val}'")
            # Return as joined string so API handler gets a plain string
            return ", ".join(
                (item.strip() if isinstance(
                    item, str) else (
                    item.value if hasattr(
                        item, "value") else str(item))) for item in v)
        if isinstance(v, str) and "," in v:
            for part in v.split(","):
                part = part.strip()
                if part not in valid:
                    raise ValueError(f"Invalid objective: '{part}'")
            return v  # already a valid comma-separated string
        # Single value — let the original enum coercion handle it
        return v


class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    objective: Optional[Union[CampaignObjective,
                              List[CampaignObjective], str]] = None
    target_audience: Optional[CampaignTargetAudience] = None
    channels: Optional[List[Channel]] = None
    cta: Optional[CampaignCTA] = None
    tone: Optional[Tone] = None
    brand_id: Optional[str] = None
    status: Optional[CampaignStatus] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    budget: Optional[float] = None
    notes: Optional[str] = None

    @field_validator("objective", mode="before")
    @classmethod
    def validate_objective_update(cls, v):
        if v is None:
            return v
        valid = {e.value for e in CampaignObjective}
        if isinstance(v, list):
            for item in v:
                val = item.strip() if isinstance(
                    item, str) else (
                    item.value if hasattr(
                        item, "value") else str(item))
                if val not in valid:
                    raise ValueError(f"Invalid objective: '{val}'")
            return ", ".join(
                (item.strip() if isinstance(
                    item, str) else (
                    item.value if hasattr(
                        item, "value") else str(item))) for item in v)
        if isinstance(v, str) and "," in v:
            for part in v.split(","):
                part = part.strip()
                if part not in valid:
                    raise ValueError(f"Invalid objective: '{part}'")
            return v
        return v


class CampaignResponse(BaseModel):
    id: str
    user_id: str
    name: str
    objective: str
    target_audience: Optional[str] = None
    channels: List[str]
    cta: Optional[str] = None
    tone: Optional[str] = None
    brand_id: Optional[str] = None
    status: CampaignStatus
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    budget: Optional[float] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
