from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum


class Platform(str, Enum):
    """Social media platform enum"""
    TWITTER = "twitter"
    INSTAGRAM = "instagram"
    LINKEDIN = "linkedin"
    FACEBOOK = "facebook"


# ==================== REQUEST MODELS ====================

class ConnectTwitterRequest(BaseModel):
    """Connect Twitter account request"""
    oauth_token: str = Field(..., description="OAuth token from Twitter")
    oauth_token_secret: str = Field(...,
                                    description="OAuth token secret from Twitter")
    screen_name: Optional[str] = Field(
        default=None, description="Twitter username")


class PostTweetRequest(BaseModel):
    """Post tweet request"""
    content_id: Optional[str] = Field(
        default=None, description="Content ID from database")
    tweet_text: Optional[str] = Field(
        default=None, description="Tweet text (if not using content_id)")

    class Config:
        # At least one must be provided
        pass


class DisconnectSocialAccountRequest(BaseModel):
    """Disconnect social account request"""
    platform: Platform


# ==================== RESPONSE MODELS ====================

class SocialAccountResponse(BaseModel):
    """Social account response"""
    id: str
    user_id: str
    platform: str
    platform_user_id: Optional[str] = None
    username: Optional[str] = None
    is_active: bool = True
    connected_at: datetime
    metadata: dict = {}

    class Config:
        from_attributes = True


class TwitterAuthUrlResponse(BaseModel):
    """Twitter OAuth URL response"""
    auth_url: str
    oauth_token: str
    oauth_token_secret: str
    message: str = "Please visit this URL to authorize Twitter access"


class PostTweetResponse(BaseModel):
    """Post tweet response"""
    success: bool
    tweet_id: Optional[str] = None
    tweet_url: Optional[str] = None
    message: str
