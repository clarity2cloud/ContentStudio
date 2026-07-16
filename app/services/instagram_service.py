import requests
from typing import Dict, Any, Optional
from app.config import settings
from app.utils.logger import logger


class InstagramService:
    def __init__(self):
        """Initialize Instagram service"""
        self.graph_api_base = "https://graph.facebook.com/v18.0"
        self.enabled = True
        logger.info("✅ Instagram service initialized")

    async def post_image(
        self,
        access_token: str,
        instagram_account_id: str,
        image_url: str,
        caption: str
    ) -> Dict[str, Any]:
        """
        Post an image to Instagram

        Args:
            access_token: User's access token
            instagram_account_id: Instagram Business Account ID
            image_url: Public URL of the image to post
            caption: Image caption

        Returns:
            Dict with post_id, permalink
        """
        try:
            # Step 1: Create media container
            container_url = f"{self.graph_api_base}/{instagram_account_id}/media"
            container_params = {
                "image_url": image_url,
                "caption": caption,
                "access_token": access_token
            }

            container_response = requests.post(
                container_url, data=container_params)
            container_response.raise_for_status()
            container_data = container_response.json()

            creation_id = container_data.get("id")
            if not creation_id:
                raise Exception("Failed to create media container")

            # Step 2: Publish the container
            publish_url = f"{self.graph_api_base}/{instagram_account_id}/media_publish"
            publish_params = {
                "creation_id": creation_id,
                "access_token": access_token
            }

            publish_response = requests.post(publish_url, data=publish_params)
            publish_response.raise_for_status()
            publish_data = publish_response.json()

            post_id = publish_data.get("id")

            # Get permalink
            permalink_url = f"{self.graph_api_base}/{post_id}?fields=permalink&access_token={access_token}"
            permalink_response = requests.get(permalink_url)
            permalink_response.raise_for_status()
            permalink_data = permalink_response.json()

            logger.info(f"✅ Instagram post published: {post_id}")

            return {
                "post_id": post_id,
                "permalink": permalink_data.get("permalink"),
                "status": "published"
            }

        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Instagram API error: {str(e)}")
            raise Exception(f"Instagram API error: {str(e)}")
        except Exception as e:
            logger.error(f"❌ Failed to post to Instagram: {str(e)}")
            raise Exception(f"Failed to post to Instagram: {str(e)}")

    async def post_story(
        self,
        access_token: str,
        instagram_account_id: str,
        image_url: str
    ) -> Dict[str, Any]:
        """
        Post a story to Instagram

        Args:
            access_token: User's access token
            instagram_account_id: Instagram Business Account ID
            image_url: Public URL of the image

        Returns:
            Dict with story_id
        """
        try:
            # Create story container
            container_url = f"{self.graph_api_base}/{instagram_account_id}/media"
            container_params = {
                "image_url": image_url,
                "media_type": "STORIES",
                "access_token": access_token
            }

            container_response = requests.post(
                container_url, data=container_params)
            container_response.raise_for_status()
            container_data = container_response.json()

            creation_id = container_data.get("id")

            # Publish story
            publish_url = f"{self.graph_api_base}/{instagram_account_id}/media_publish"
            publish_params = {
                "creation_id": creation_id,
                "access_token": access_token
            }

            publish_response = requests.post(publish_url, data=publish_params)
            publish_response.raise_for_status()
            publish_data = publish_response.json()

            logger.info(
                f"✅ Instagram story published: {publish_data.get('id')}")

            return {
                "story_id": publish_data.get("id"),
                "status": "published"
            }

        except Exception as e:
            logger.error(f"❌ Failed to post Instagram story: {str(e)}")
            raise Exception(f"Failed to post Instagram story: {str(e)}")

    async def verify_credentials(
        self,
        access_token: str,
        instagram_account_id: str
    ) -> Dict[str, Any]:
        """Verify Instagram credentials and get account info"""
        try:
            url = f"{self.graph_api_base}/{instagram_account_id}"
            params = {
                "fields": "username,name,followers_count,follows_count,media_count",
                "access_token": access_token}

            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            return {
                "account_id": instagram_account_id,
                "username": data.get("username"),
                "name": data.get("name"),
                "followers_count": data.get("followers_count"),
                "follows_count": data.get("follows_count"),
                "media_count": data.get("media_count")
            }

        except Exception as e:
            logger.error(f"❌ Failed to verify Instagram credentials: {str(e)}")
            raise Exception(
                f"Failed to verify Instagram credentials: {str(e)}")

    async def refresh_long_lived_token(
            self, current_long_lived_token: str) -> Optional[Dict[str, Any]]:
        """
        Exchange a still-valid 60-day token for a fresh 60-day token.

        Meta allows refreshing a long-lived token any time after it is 24h old and
        before it expires. Returns {"access_token", "expires_in"} or None if the
        Meta app credentials are not configured / the call fails.
        """
        app_id = getattr(settings, "META_APP_ID", "") or ""
        app_secret = getattr(settings, "META_APP_SECRET", "") or ""
        if not (app_id and app_secret and current_long_lived_token):
            logger.warning(
                "[IG-REFRESH] Meta app credentials not configured — cannot refresh token")
            return None
        try:
            url = f"{self.graph_api_base}/oauth/access_token"
            params = {
                "grant_type": "fb_exchange_token",
                "client_id": app_id,
                "client_secret": app_secret,
                "fb_exchange_token": current_long_lived_token,
            }
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            return {
                "access_token": data["access_token"],
                "expires_in": data.get("expires_in", 60 * 24 * 3600),
            }
        except Exception as e:
            logger.error(f"[IG-REFRESH] token refresh failed: {e}")
            return None


# Global Instagram service instance
instagram_service = InstagramService()


async def refresh_expiring_instagram_tokens(db, within_days: int = 7) -> int:
    """
    Renew Instagram long-lived tokens that expire within `within_days`.

    Designed to be invoked by a scheduled job (APScheduler / Celery beat). Reads
    `social_accounts` rows for the instagram platform, refreshes any whose
    `token_expires_at` is near, and writes back the new encrypted token + expiry.

    Returns the number of accounts refreshed. Never raises — safe for a scheduler.
    """
    from datetime import datetime, timezone, timedelta
    from app.utils.encryption import encrypt, decrypt

    refreshed = 0
    try:
        rows = db.table("social_accounts").select(
            "*").eq("platform", "instagram").eq("is_active", True).execute()
        accounts = rows.data or []
        now = datetime.now(timezone.utc)
        threshold = now + timedelta(days=within_days)

        for acc in accounts:
            raw_exp = acc.get("token_expires_at")
            # Only refresh rows that are near expiry (or have no recorded
            # expiry).
            if raw_exp:
                try:
                    exp = datetime.fromisoformat(
                        str(raw_exp).replace("Z", "+00:00"))
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    if exp > threshold:
                        continue
                except Exception:
                    pass  # unparseable → attempt refresh to be safe

            current = decrypt(
                acc.get("refresh_token") or "") or decrypt(
                acc.get("access_token") or "")
            if not current:
                continue

            result = await instagram_service.refresh_long_lived_token(current)
            if not result:
                continue

            new_token = result["access_token"]
            new_exp = (
                now +
                timedelta(
                    seconds=int(
                        result.get(
                            "expires_in",
                            60 *
                            24 *
                            3600)))).isoformat()
            db.table("social_accounts").update({
                "access_token": encrypt(new_token),
                "refresh_token": encrypt(new_token),
                "token_expires_at": new_exp,
                "updated_at": now.isoformat(),
            }).eq("id", acc["id"]).execute()
            refreshed += 1
            logger.info(
                f"[IG-REFRESH] refreshed token for account {acc.get('id')}")
    except Exception as e:
        logger.error(f"[IG-REFRESH] sweep failed (non-fatal): {e}")
    return refreshed
