# app/models/brand.py
"""
Brand Profile — the Umbrella

The brand profile is the single source of truth for everything the LLM needs
to write expert-level, on-brand content. Every field here is injected into
generation prompts via brand_validator.build_brand_block().
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


# ── Create / Update / Response ───────────────────────────────────────────────
class BrandProfileCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    company_name: Optional[str] = Field(None, max_length=256)
    website_url: Optional[str] = Field(None)
    industry: Optional[str] = Field(None)
    tone: Optional[str] = Field(None)
    voice: Optional[str] = Field(None)
    positioning: Optional[str] = Field(None)
    target_audience: Optional[str] = Field(None)
    vocabulary: Optional[List[str]] = Field(default_factory=list)
    avoid_words: Optional[List[str]] = Field(default_factory=list)
    cta_examples: Optional[List[str]] = Field(default_factory=list)
    is_default: Optional[bool] = Field(default=False)

    # Knowledge fields
    brand_story: Optional[str] = None
    goals: Optional[List[str]] = Field(default_factory=list)

    # Content psychology — creative modifiers sent to the LLM
    # psychological_trigger: fear | curiosity | controversy | ego | desire
    psychological_trigger: Optional[str] = None
    # delivery_tone: provocative | authoritative | insider_leak | confessional
    delivery_tone: Optional[str] = None


class BrandProfileUpdate(BaseModel):
    name: Optional[str] = None
    company_name: Optional[str] = Field(None, max_length=256)
    website_url: Optional[str] = None
    industry: Optional[str] = None
    tone: Optional[str] = None
    voice: Optional[str] = None
    positioning: Optional[str] = None
    target_audience: Optional[str] = None
    vocabulary: Optional[List[str]] = None
    avoid_words: Optional[List[str]] = None
    cta_examples: Optional[List[str]] = None
    is_default: Optional[bool] = None

    # Knowledge fields
    brand_story: Optional[str] = None
    goals: Optional[List[str]] = None

    # Content psychology
    psychological_trigger: Optional[str] = None
    delivery_tone: Optional[str] = None


class BrandProfileResponse(BaseModel):
    id: str
    user_id: str
    name: str
    company_name: Optional[str] = None
    website_url: Optional[str] = None
    industry: Optional[str] = None
    tone: Optional[str] = None
    voice: Optional[str] = None
    positioning: Optional[str] = None
    target_audience: Optional[str] = None
    vocabulary: List[str] = []
    avoid_words: List[str] = []
    cta_examples: List[str] = []
    is_default: bool = False
    created_at: datetime
    updated_at: datetime

    # Knowledge fields
    brand_story: Optional[str] = None
    goals: List[str] = []

    # Content psychology
    psychological_trigger: Optional[str] = None
    delivery_tone: Optional[str] = None

    # Completeness score (computed at response time, not stored)
    completeness_score: Optional[int] = None
    completeness_tier: Optional[str] = None
    next_best_action: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True
