# app/services/platform_strategies.py
#
# Platform-specific generation strategies
# Different platforms need different formats, lengths, and tones
#

from typing import Dict, Any


class PlatformStrategy:
    """Define generation strategy for each platform."""

    STRATEGIES = {
        # Short-form social media (< 300 words)
        "tweet": {
            "max_chars": 280,
            "max_words": 50,
            "format": "single tweet, punchy, no hashtags unless specified",
            "tone_default": "casual",
            "requirements": [
                "Keep under 280 characters strictly",
                "Hook must stop scroll immediately",
                "No long URLs or citations",
                "One clear idea per tweet"
            ]
        },
        "instagram_caption": {
            "max_chars": 2200,
            "max_words": 150,
            "format": "visual-first caption, emojis encouraged, hashtags at end",
            "tone_default": "friendly",
            "requirements": [
                "Write for visual content (assume image/video exists)",
                "Use line breaks for readability",
                "Keep main text under 150 words",
                "Hashtags (up to 30) on separate lines at end",
                "Call-to-action should be natural, not forced"
            ]
        },
        "tiktok": {
            "max_chars": 2200,
            "max_words": 150,
            "format": "hook + body, encourages emojis and trends",
            "tone_default": "casual",
            "requirements": [
                "Hook must grab attention in first 3 words",
                "Optimize for viral trending sounds/challenges if relevant",
                "Use trending hashtags naturally",
                "Encourage engagement/interaction",
                "Conversational, not corporate"
            ]
        },
        "linkedin_post": {
            "max_chars": 3000,
            "max_words": 350,
            "format": "professional story or insight",
            "tone_default": "professional",
            "requirements": [
                "Open with specific insight or question",
                "Avoid sales language; focus on value",
                "Use line breaks for mobile readability",
                "1-2 relevant hashtags maximum",
                "End with thoughtful question or call-to-action",
                "Share learnings, not just promotions"
            ]
        },
        "facebook_post": {
            "max_chars": 500,
            "max_words": 150,
            "format": "conversational, community-focused",
            "tone_default": "friendly",
            "requirements": [
                "Write like talking to community, not broadcasting",
                "Use personal voice",
                "Encourage comments and conversation",
                "1-3 relevant hashtags",
                "Link (if any) should feel natural"
            ]
        },
        "pinterest": {
            "max_chars": 500,
            "max_words": 100,
            "format": "benefit-focused description",
            "tone_default": "professional",
            "requirements": [
                "Describe visual benefit clearly",
                "Use keywords naturally (SEO for Pinterest)",
                "Start with keyword/benefit",
                "End with clear value proposition",
                "No hashtags"
            ]
        },
        "threads": {
            "max_chars": 500,
            "max_words": 100,
            "format": "authentic, conversational",
            "tone_default": "friendly",
            "requirements": [
                "Feel like real person, not brand voice",
                "Can be part of a thread (keep single thought)",
                "Encourage replies and discussion",
                "Avoid corporate speak",
                "Authentic and human-first"
            ]
        },
        "snapchat": {
            "max_chars": 200,
            "max_words": 40,
            "format": "very short, visual-first, ephemeral",
            "tone_default": "casual",
            "requirements": [
                "Ultra-short text (supports visual)",
                "Must be relevant to visual content",
                "Trendy/current language",
                "FOMO-inducing if appropriate",
                "Emoji-friendly"
            ]
        },

        # Messaging (character-limited)
        "sms": {
            "max_chars": 160,
            "max_words": 30,
            "format": "ultra-concise message",
            "tone_default": "friendly",
            "requirements": [
                "Single message (160 chars max)",
                "Crystal clear call-to-action",
                "Urgency or value proposition",
                "No fluff, every word counts",
                "Natural, conversational tone"
            ]
        },
        "whatsapp": {
            "max_words": 200,
            "format": "personal message, can use emojis",
            "tone_default": "friendly",
            "requirements": [
                "Feel like personal message from friend",
                "Can be slightly longer than SMS",
                "Emoji use appropriate",
                "Natural WhatsApp conversation style",
                "Clear but not salesy"
            ]
        },

        # Email
        "email": {
            "max_words": 300,
            "format": "structured: subject + body with greeting, content, closing",
            "tone_default": "professional",
            "requirements": [
                "Subject line must stop-the-scroll",
                "Personalization with first name if available",
                "Clear opening hook",
                "Body: 1-3 clear paragraphs max",
                "Specific, action-oriented CTA",
                "Warm closing with signature"
            ]
        },

        # Medium-form
        "newsletter": {
            "max_words": 1000,
            "format": "structured sections, friendly letter-like tone",
            "tone_default": "friendly",
            "requirements": [
                "Personal tone, like letter from expert friend",
                "Subject + preview line",
                "Greeting (optional but recommended)",
                "2-4 sections, each with clear value",
                "Warm, human closing",
                "CTA should feel natural, not forced"
            ]
        },

        # Long-form content
        "blog": {
            "max_words": 2000,
            "format": "structured with title, sections, subsections",
            "tone_default": "professional",
            "requirements": [
                "Compelling title that includes target keyword",
                "3-5 substantive sections with headers",
                "Opening hook with specific stat/question/scenario",
                "Each section: minimum 200+ words",
                "Real examples, case studies, concrete data",
                "Actionable tips/takeaways in each section",
                "Strong conclusion with clear next step",
                "SEO-optimized but human-first writing"
            ]
        },
        "linkedin_article": {
            "max_words": 1500,
            "format": "thought-leadership piece, professional story",
            "tone_default": "professional",
            "requirements": [
                "Compelling headline with insight/POV",
                "Opening paragraph: specific scenario/stat/question",
                "3-4 substantive sections developing the argument",
                "Use personal experience/case studies",
                "Professional but conversational tone",
                "Strong conclusion with call-to-action",
                "Avoid self-promotion; focus on value"
            ]
        },
        "landing_page": {
            "max_words": 1500,
            "format": "persuasive with sections: hero, benefits, features, social proof, CTA",
            "tone_default": "bold",
            "requirements": [
                "Hero section: headline + subheadline + CTA button description",
                "Benefits section: 3-5 clear benefits for target user",
                "How it works: 3-5 steps to value",
                "Social proof/testimonials section",
                "FAQ or objection handling",
                "Final CTA section with urgency",
                "Benefit-focused, not feature-focused",
                "Action-oriented language throughout"
            ]
        },
        "press_release": {
            "max_words": 1000,
            "format": "structured: headline, subheading, dateline, quote, details, boilerplate",
            "tone_default": "professional",
            "requirements": [
                "Headline: news angle, benefit, outcome",
                "Opening: answer who, what, where, when, why in first paragraph",
                "Quote: company perspective/context",
                "Details: 2-3 additional paragraphs with context",
                "Boilerplate: about company (standard format)",
                "Professional, journalistic tone",
                "Newsworthy angle — not pure marketing"
            ]
        },
        "images": {
            "max_words": 120,
            "format": "detailed visual scene description for AI image generation",
            "tone_default": "vivid",
            "requirements": [
                "Write a vivid visual scene description (not a caption or post)",
                "Describe subject, lighting, mood, colours, background, composition",
                "Pure image description — NO text, words, letters, or typography in the scene",
                "Match the campaign topic and phase (awareness, engagement, conversion)",
                "Each day's image must be visually distinct from other days",
                "Professional photography or illustration quality",
                "Example format: 'Wide-angle shot of a modern open office, soft morning light, diverse team collaborating around a glowing laptop, teal and white colour palette, shallow depth of field'",
                "Do NOT include hashtags, captions, or any written content"
            ]
        },
        "podcast": {
            "max_words": 800,
            "format": "episode description + show notes with timestamps",
            "tone_default": "professional",
            "requirements": [
                "Episode title with clear value prop",
                "Description: 100-150 words of episode summary",
                "Key takeaways: 3-5 main points",
                "Timestamps: if discussing multiple topics",
                "Guest info: if applicable",
                "Call-to-action: subscribe/share/visit",
                "Conversational but organized"
            ]
        },
        "webinar": {
            "max_words": 800,
            "format": "description + agenda + value prop",
            "tone_default": "professional",
            "requirements": [
                "Title: clear learning outcome",
                "Description: what attendees will learn (150-200 words)",
                "Agenda: 3-5 topics with time allocations",
                "Target audience: who should attend",
                "Presenter credentials: speaker expertise",
                "Registration incentive: what's in it for them",
                "Urgency: limited spots/time element"
            ]
        },

        # Ads
        "google_ads": {
            "max_chars": 90,
            "max_words": 15,
            "format": "headline + description",
            "tone_default": "bold",
            "requirements": [
                "Headline: 30 chars max, benefit-first",
                "Description: 90 chars max, CTA + value prop",
                "Use power words (save, get, discover, try)",
                "Clear CTA button (implied: Learn More, Get Started, etc)",
                "No punctuation unless necessary"
            ]
        },
        "meta_ads": {
            "max_words": 125,
            "format": "ad copy with strong hook",
            "tone_default": "bold",
            "requirements": [
                "Hook in first 3 words: stop the scroll",
                "Benefit-focused, not feature-focused",
                "Use emojis sparingly but effectively",
                "Clear, specific CTA",
                "Curiosity gap or urgency element",
                "Conversational but punchy"
            ]
        },
        "linkedin_ads": {
            "max_words": 150,
            "format": "professional, value-focused ad copy",
            "tone_default": "professional",
            "requirements": [
                "Hook: specific benefit or insight",
                "Body: 2-3 sentences max",
                "Value prop: crystal clear",
                "CTA: action-oriented",
                "Professional tone, no emojis",
                "B2B focused if applicable"
            ]
        },
    }

    @staticmethod
    def get_strategy(platform: str) -> Dict[str, Any]:
        """Get generation strategy for a platform."""
        return PlatformStrategy.STRATEGIES.get(
            platform.lower(),
            {
                "max_words": 500,
                "format": "standard content",
                "tone_default": "professional",
                "requirements": ["Provide high-quality content appropriate for the platform"]
            }
        )

    @staticmethod
    def get_platform_rules(platform: str) -> str:
        """Get formatted platform rules for injection into prompt."""
        strategy = PlatformStrategy.get_strategy(platform)
        rules_text = """
PLATFORM REQUIREMENTS ({platform.upper()}):
Format: {strategy.get('format', 'standard')}
Max words: {strategy.get('max_words', 'no limit')} | Max chars: {strategy.get('max_chars', 'no limit')}
Default tone: {strategy.get('tone_default', 'professional')}

Requirements:
"""
        for i, req in enumerate(strategy.get('requirements', []), 1):
            rules_text += f"{i}. {req}\n"

        return rules_text
