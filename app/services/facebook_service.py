import requests
from typing import Dict, Any, Optional
from app.utils.logger import logger


class FacebookService:
    def __init__(self):
        """Initialize Facebook service"""
        self.graph_api_base = "https://graph.facebook.com/v18.0"
        self.enabled = True
        logger.info("✅ Facebook service initialized")

    async def post_to_page(
        self,
        page_access_token: str,
        page_id: str,
        message: str,
        link: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Post to a Facebook Page

        Args:
            page_access_token: Page access token
            page_id: Facebook Page ID
            message: Post message
            link: Optional link to share

        Returns:
            Dict with post_id, post_url
        """
        try:
            url = f"{self.graph_api_base}/{page_id}/feed"

            params = {
                "message": message,
                "access_token": page_access_token
            }

            if link:
                params["link"] = link

            response = requests.post(url, data=params)
            response.raise_for_status()
            data = response.json()

            post_id = data.get("id")

            logger.info(f"✅ Facebook post published: {post_id}")

            return {
                "post_id": post_id,
                "post_url": f"https://www.facebook.com/{post_id}",
                "status": "published"
            }

        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Facebook API error: {str(e)}")
            raise Exception(f"Facebook API error: {str(e)}")
        except Exception as e:
            logger.error(f"❌ Failed to post to Facebook: {str(e)}")
            raise Exception(f"Failed to post to Facebook: {str(e)}")

    async def post_photo(
        self,
        page_access_token: str,
        page_id: str,
        photo_url: str,
        caption: str
    ) -> Dict[str, Any]:
        """
        Post a photo to Facebook Page

        Args:
            page_access_token: Page access token
            page_id: Facebook Page ID
            photo_url: Public URL of the photo
            caption: Photo caption

        Returns:
            Dict with post_id, post_url
        """
        try:
            url = f"{self.graph_api_base}/{page_id}/photos"

            params = {
                "url": photo_url,
                "caption": caption,
                "access_token": page_access_token
            }

            response = requests.post(url, data=params)
            response.raise_for_status()
            data = response.json()

            post_id = data.get("post_id")

            logger.info(f"✅ Facebook photo published: {post_id}")

            return {
                "post_id": post_id,
                "post_url": f"https://www.facebook.com/{post_id}",
                "status": "published"
            }

        except Exception as e:
            logger.error(f"❌ Failed to post Facebook photo: {str(e)}")
            raise Exception(f"Failed to post Facebook photo: {str(e)}")

    async def get_page_info(
        self,
        page_access_token: str,
        page_id: str
    ) -> Dict[str, Any]:
        """Get Facebook Page information"""
        try:
            url = f"{self.graph_api_base}/{page_id}"
            params = {
                "fields": "id,name,username,fan_count,followers_count",
                "access_token": page_access_token
            }

            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            return {
                "page_id": data.get("id"),
                "name": data.get("name"),
                "username": data.get("username"),
                "fan_count": data.get("fan_count"),
                "followers_count": data.get("followers_count")
            }

        except Exception as e:
            logger.error(f"❌ Failed to get Facebook page info: {str(e)}")
            raise Exception(f"Failed to get Facebook page info: {str(e)}")

    async def verify_credentials(
        self,
        access_token: str
    ) -> Dict[str, Any]:
        """Verify Facebook credentials and get user info"""
        try:
            url = f"{self.graph_api_base}/me"
            params = {
                "fields": "id,name,email",
                "access_token": access_token
            }

            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            return {
                "user_id": data.get("id"),
                "name": data.get("name"),
                "email": data.get("email")
            }

        except Exception as e:
            logger.error(f"❌ Failed to verify Facebook credentials: {str(e)}")
            raise Exception(f"Failed to verify Facebook credentials: {str(e)}")


# Global Facebook service instance
facebook_service = FacebookService()
