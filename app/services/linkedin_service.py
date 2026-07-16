import requests
from typing import Dict, Any, Optional
from app.utils.logger import logger


class LinkedInService:
    def __init__(self):
        """Initialize LinkedIn service"""
        self.api_base = "https://api.linkedin.com/v2"
        self.enabled = True
        logger.info("✅ LinkedIn service initialized")

    async def post_text(
        self,
        access_token: str,
        person_urn: str,
        text: str
    ) -> Dict[str, Any]:
        """
        Post text to LinkedIn

        Args:
            access_token: User's access token
            person_urn: LinkedIn person URN (e.g., urn:li:person:ABC123)
            text: Post content

        Returns:
            Dict with post_id, post_url
        """
        try:
            url = f"{self.api_base}/ugcPosts"

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0"
            }

            payload = {
                "author": person_urn,
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {
                            "text": text
                        },
                        "shareMediaCategory": "NONE"
                    }
                },
                "visibility": {
                    "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
                }
            }

            response = requests.post(url, headers=headers, json=payload)
            response.raise_for_status()

            # Get post ID from response header
            post_id = response.headers.get("x-restli-id")

            logger.info(f"✅ LinkedIn post published: {post_id}")

            return {
                "post_id": post_id,
                "post_url": f"https://www.linkedin.com/feed/update/{post_id}",
                "status": "published"
            }

        except requests.exceptions.RequestException as e:
            logger.error(f"❌ LinkedIn API error: {str(e)}")
            raise Exception(f"LinkedIn API error: {str(e)}")
        except Exception as e:
            logger.error(f"❌ Failed to post to LinkedIn: {str(e)}")
            raise Exception(f"Failed to post to LinkedIn: {str(e)}")

    async def post_article(
        self,
        access_token: str,
        person_urn: str,
        title: str,
        content: str,
        article_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Post an article to LinkedIn

        Args:
            access_token: User's access token
            person_urn: LinkedIn person URN
            title: Article title
            content: Article description
            article_url: Optional article URL

        Returns:
            Dict with post_id, post_url
        """
        try:
            url = f"{self.api_base}/ugcPosts"

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0"
            }

            media_content = {
                "status": "READY",
                "description": {
                    "text": content
                },
                "title": {
                    "text": title
                }
            }

            if article_url:
                media_content["originalUrl"] = article_url

            payload = {
                "author": person_urn,
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {
                            "text": content
                        },
                        "shareMediaCategory": "ARTICLE",
                        "media": [media_content]
                    }
                },
                "visibility": {
                    "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
                }
            }

            response = requests.post(url, headers=headers, json=payload)
            response.raise_for_status()

            post_id = response.headers.get("x-restli-id")

            logger.info(f"✅ LinkedIn article published: {post_id}")

            return {
                "post_id": post_id,
                "post_url": f"https://www.linkedin.com/feed/update/{post_id}",
                "status": "published"
            }

        except Exception as e:
            logger.error(f"❌ Failed to post LinkedIn article: {str(e)}")
            raise Exception(f"Failed to post LinkedIn article: {str(e)}")

    async def verify_credentials(
        self,
        access_token: str
    ) -> Dict[str, Any]:
        """
        Verify LinkedIn credentials and return user info.

        Uses /v2/userinfo (OpenID Connect) — works for all LinkedIn app versions.
        Falls back to /v2/me for legacy apps registered before 2023.

        Docs: https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/sign-in-with-linkedin-v2
        """
        try:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "X-Restli-Protocol-Version": "2.0.0",
            }

            # Primary: OpenID Connect userinfo endpoint (works for all new
            # LinkedIn apps)
            oidc_resp = requests.get(
                f"{self.api_base}/userinfo", headers=headers)
            if oidc_resp.status_code == 200:
                data = oidc_resp.json()
                user_id = data.get("sub", "")
                person_urn = f"urn:li:person:{user_id}"
                return {
                    "person_urn": person_urn,
                    "user_id": user_id,
                    "name": data.get("name", ""),
                    "first_name": data.get("given_name", ""),
                    "last_name": data.get("family_name", ""),
                    "email": data.get("email", ""),
                    "picture": data.get("picture", ""),
                }

            # Fallback: legacy /v2/me (deprecated for newer apps but still
            # works for some)
            me_resp = requests.get(
                f"{self.api_base}/me",
                headers=headers,
                params={"projection": "(id,firstName,lastName)"},
            )
            me_resp.raise_for_status()
            data = me_resp.json()
            user_id = data.get("id", "")
            person_urn = f"urn:li:person:{user_id}"
            first_name = data.get(
                "firstName",
                {}).get(
                "localized",
                {}).get(
                "en_US",
                "")
            last_name = data.get(
                "lastName",
                {}).get(
                "localized",
                {}).get(
                "en_US",
                "")
            return {
                "person_urn": person_urn,
                "user_id": user_id,
                "name": f"{first_name} {last_name}".strip(),
                "first_name": first_name,
                "last_name": last_name,
            }

        except Exception as e:
            logger.error(f"❌ Failed to verify LinkedIn credentials: {str(e)}")
            raise Exception(f"Failed to verify LinkedIn credentials: {str(e)}")


# Global LinkedIn service instance
linkedin_service = LinkedInService()
