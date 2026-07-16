# app/services/twitter_service.py
#
# X (Twitter) API v2 — OAuth 2.0 PKCE
#
# Docs: https://developer.twitter.com/en/docs/twitter-api
#
# Key facts:
#   • Free tier: write-only (create tweets). No read, no delete on free.
#   • Basic/Pro tier: full read+write
#   • Auth: OAuth 2.0 Authorization Code + PKCE (user context)
#   • Post endpoint: POST /2/tweets
#   • Rate limit: 1 tweet / 15 min on free; 300 / 15 min on Basic+

import httpx
from typing import Dict, Any, Optional
from app.config import settings
from app.utils.logger import logger

X_API_BASE = "https://api.twitter.com/2"
X_AUTH_BASE = "https://twitter.com/i/oauth2"


class TwitterService:

    def __init__(self):
        self.client_id = getattr(
            settings, "TWITTER_CLIENT_ID", None) or getattr(
            settings, "TWITTER_API_KEY", None)
        self.client_secret = getattr(
            settings, "TWITTER_CLIENT_SECRET", None) or getattr(
            settings, "TWITTER_API_SECRET", None)
        self.enabled = bool(self.client_id and self.client_secret)
        if not self.enabled:
            logger.warning(
                "Twitter (X) credentials not set — social features disabled")

    # ── OAuth 2.0 PKCE flow ─────────────────────────────────────────────────

    def get_auth_url(
            self,
            callback_url: str,
            state: str,
            code_challenge: str) -> str:
        """
        Build the authorization URL for OAuth 2.0 PKCE flow.

        The frontend redirects the user here. After approval Twitter redirects
        to callback_url with ?code=… and ?state=…
        """
        params = (
            "response_type=code"
            f"&client_id={self.client_id}"
            f"&redirect_uri={callback_url}"
            "&scope=tweet.read%20tweet.write%20users.read%20offline.access"
            f"&state={state}"
            f"&code_challenge={code_challenge}"
            "&code_challenge_method=S256"
        )
        return f"{X_AUTH_BASE}/authorize?{params}"

    async def exchange_code(
        self,
        code: str,
        code_verifier: str,
        callback_url: str,
    ) -> Dict[str, Any]:
        """
        Exchange an authorization code for access + refresh tokens.

        Returns: { access_token, refresh_token, expires_in, scope }
        """
        import base64
        creds = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()).decode()
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{X_AUTH_BASE}/token",
                headers={
                    "Authorization": f"Basic {creds}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": callback_url,
                    "code_verifier": code_verifier,
                },
            )
            r.raise_for_status()
            return r.json()

    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh an expired access token using the refresh token."""
        import base64
        creds = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()).decode()
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{X_AUTH_BASE}/token",
                headers={
                    "Authorization": f"Basic {creds}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            r.raise_for_status()
            return r.json()

    async def get_user_info(self, access_token: str) -> Dict[str, Any]:
        """Fetch the authenticated user's profile from X API v2."""
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                f"{X_API_BASE}/users/me",
                params={"user.fields": "id,name,username,profile_image_url,public_metrics"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            r.raise_for_status()
            data = r.json().get("data", {})
            return {
                "user_id": data.get("id"),
                "username": data.get("username"),
                "name": data.get("name"),
                "profile_image_url": data.get("profile_image_url"),
                "followers_count": data.get(
                    "public_metrics",
                    {}).get("followers_count"),
            }

    # ── Posting ─────────────────────────────────────────────────────────────

    async def post_tweet(
        self,
        access_token: str,
        tweet_text: str,
        reply_to_tweet_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        POST /2/tweets  — requires OAuth 2.0 user context token with tweet.write scope.

        Rate limits:
          Free:  1 request / 15 min per user
          Basic: 100 requests / 24 h per user
        """
        if not self.enabled:
            raise ValueError("Twitter (X) credentials not configured")
        if len(tweet_text) > 280:
            tweet_text = tweet_text[:277] + "..."

        payload: Dict[str, Any] = {"text": tweet_text}
        if reply_to_tweet_id:
            payload["reply"] = {"in_reply_to_tweet_id": reply_to_tweet_id}

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{X_API_BASE}/tweets",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            r.raise_for_status()
            data = r.json().get("data", {})

        tweet_id = data.get("id", "")
        logger.info(f"Tweet posted: {tweet_id}")
        return {
            "tweet_id": tweet_id,
            "tweet_url": f"https://x.com/i/web/status/{tweet_id}",
            "text": data.get("text", tweet_text),
            "created_at": data.get("created_at", ""),
        }

    async def verify_credentials(
        self,
        access_token: str,
    ) -> Dict[str, Any]:
        return await self.get_user_info(access_token)


# Singleton
twitter_service = TwitterService()
