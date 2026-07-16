from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # Application
    APP_NAME: str = "ContentStudio AI Backend"
    ENV: str = "development"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Appwrite (ContentStudio DB) — optional; falls back to mock data if unset
    APPWRITE_ENDPOINT: str = ""
    APPWRITE_PROJECT_ID: str = ""
    APPWRITE_API_KEY: str = ""

    # NVIDIA NIM API — LLM + Image Gen
    # Primary : meta/llama-3.3-70b-instruct — 70B, best quality
    # Fallback: meta/llama-3.1-8b-instruct  — 8B, fast last resort
    NVIDIA_API_KEY: str = ""
    NVIDIA_MODEL: str = "meta/llama-3.3-70b-instruct"
    NVIDIA_FALLBACK_MODEL: str = "meta/llama-3.1-8b-instruct"

    # Gamma AI (carousel generation)
    GAMMA_API_KEY: str = ""

    # Security — local app key (used to derive the encryption key for stored
    # social tokens; see app/utils/encryption.py)
    SECRET_KEY: str

    # Dedicated key for encrypting stored social tokens (app/utils/encryption.py).
    # If unset, falls back to a key derived from SECRET_KEY. Required (no
    # fallback) when ENV=production — see ENCRYPTION_KEY check in app/main.py.
    ENCRYPTION_KEY: str = ""

    # This build ships with authentication stubbed out (app/core/dependencies.py
    # always returns a demo user) — a deliberate choice for the open-source
    # local/dev build. Startup refuses to boot with ENV=production unless this
    # is explicitly set to true, so it can never be exposed to the internet
    # without a conscious opt-in. Wire up real auth before flipping this on.
    ALLOW_DEMO_AUTH_IN_PRODUCTION: bool = False

    # Number of trusted reverse-proxy hops in front of this app that append to
    # X-Forwarded-For (e.g. 1 for a single ingress/load balancer). Only this
    # many hops from the right are trusted; the header is ignored entirely
    # when set to 0 (default), which is the safe choice absent a known proxy.
    TRUSTED_PROXY_COUNT: int = 0

    # Redis — used for cache (Tier 2) and Celery broker/backend
    # In Docker: redis://contentstudio-redis:6379/0
    # Leave blank to run without Redis (in-process LRU cache only, APScheduler
    # only)
    REDIS_URL: str = ""

    # Frontend + API base URLs
    FRONTEND_URL: str = "http://localhost:3000"
    API_BASE_URL: str = "https://api.contentstudio.thq.digital/api/v1"

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173,https://contentstudio.thq.digital"

    def get_cors_origins(self) -> List[str]:
        if isinstance(self.CORS_ORIGINS, str):
            return [o.strip()
                    for o in self.CORS_ORIGINS.split(",") if o.strip()]
        return self.CORS_ORIGINS

    # Email Service
    RESEND_API_KEY: str = ""
    MAIL_FROM: str = "ContentStudio <onboarding@resend.dev>"

    # Twitter / X
    TWITTER_CLIENT_ID: str = ""
    TWITTER_CLIENT_SECRET: str = ""
    TWITTER_API_KEY: str = ""
    TWITTER_API_SECRET: str = ""
    TWITTER_BEARER_TOKEN: str = ""
    TWITTER_CALLBACK_URL: str = "https://api.contentstudio.thq.digital/api/v1/social/oauth/twitter/callback"

    # LinkedIn
    LINKEDIN_CLIENT_ID: str = ""
    LINKEDIN_CLIENT_SECRET: str = ""
    LINKEDIN_CALLBACK_URL: str = "https://api.contentstudio.thq.digital/api/v1/social/oauth/linkedin/callback"

    # Meta (Instagram + Facebook)
    META_APP_ID: str = ""
    META_APP_SECRET: str = ""
    META_CALLBACK_URL: str = "https://api.contentstudio.thq.digital/api/v1/social/oauth/instagram/callback"
    FACEBOOK_CALLBACK_URL: str = "https://api.contentstudio.thq.digital/api/v1/social/oauth/facebook/callback"

    # Viral Intel — RAG trend scouting
    REDDIT_CLIENT_ID: str = ""
    REDDIT_CLIENT_SECRET: str = ""
    REDDIT_USER_AGENT: str = "ContentStudio/1.0"
    YOUTUBE_DATA_API_KEY: str = ""
    # Instagram — dedicate a public-only IG account (no private data accessed)
    INSTAGRAM_SCRAPER_USERNAME: str = ""
    INSTAGRAM_SCRAPER_PASSWORD: str = ""
    # X/Twitter — any free Twitter account (twikit unofficial scraper)
    TWITTER_SCRAPER_USERNAME: str = ""
    TWITTER_SCRAPER_PASSWORD: str = ""
    # Required by twikit to bypass KEY_BYTE error on first login
    TWITTER_SCRAPER_EMAIL: str = ""
    # Browserless — self-hosted headless Chrome (Docker) for stealth social scraping
    # Handles TikTok (no public API), plus Instagram/Twitter fallback via Google Search
    # docker run -p 3000:3000 --restart always -d browserless/chrome:latest
    BROWSERLESS_URL: str = "http://localhost:3000"

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


settings = Settings()
