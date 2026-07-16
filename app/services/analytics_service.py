from typing import Dict, Any, List
from datetime import datetime, timedelta, timezone
from app.utils.logger import logger
from app.db.appwrite_client import AppwriteClient


class AnalyticsService:
    def __init__(self):
        """Initialize analytics service"""
        logger.info("✅ Analytics service initialized")

    async def fetch_twitter_analytics(
        self,
        post_id: str,
        access_token: str,
        access_token_secret: str
    ) -> Dict[str, Any]:
        """
        Fetch analytics for a Twitter post

        Note: Twitter API v2 required for detailed analytics
        This is a placeholder - implement with actual Twitter API
        """
        try:
            # Placeholder - in production, use Twitter API v2
            # For now, return mock data
            logger.info(f"Fetching Twitter analytics for post {post_id}")

            return {
                "likes": 0,
                "retweets": 0,
                "replies": 0,
                "impressions": 0,
                "engagement_rate": 0.0
            }

        except Exception as e:
            logger.error(f"❌ Failed to fetch Twitter analytics: {str(e)}")
            return {
                "likes": 0,
                "retweets": 0,
                "replies": 0,
                "impressions": 0,
                "engagement_rate": 0.0
            }

    async def get_overall_analytics(
        self,
        user_id: str,
        db_client: AppwriteClient
    ) -> Dict[str, Any]:
        """Get overall analytics for user"""
        try:
            # Get total content created
            content_stats = db_client.table("content")\
                .select("status", count="exact")\
                .eq("user_id", user_id)\
                .execute()

            total_content = content_stats.count if hasattr(
                content_stats, 'count') else 0

            # Count by status
            draft_count = len(
                [c for c in (content_stats.data or []) if c.get("status") == "draft"])
            published_count = len(
                [c for c in (content_stats.data or []) if c.get("status") == "published"])
            _scheduled_count = len(
                [c for c in (content_stats.data or []) if c.get("status") == "scheduled"])

            # Get connected platforms
            platforms = db_client.table("social_accounts")\
                .select("platform")\
                .eq("user_id", user_id)\
                .eq("is_active", True)\
                .execute()

            connected_platforms = list(
                set([p["platform"] for p in (platforms.data or [])]))

            # Get scheduled posts count
            scheduled_posts = db_client.table("scheduled_posts")\
                .select("*", count="exact")\
                .eq("user_id", user_id)\
                .execute()

            total_scheduled = scheduled_posts.count if hasattr(
                scheduled_posts, 'count') else 0

            # Get posted count
            posted_count = len(
                [p for p in (scheduled_posts.data or []) if p.get("status") == "posted"])

            # Get recent posts
            recent_posts = db_client.table("scheduled_posts")\
                .select("*, content(title, content, content_type)")\
                .eq("user_id", user_id)\
                .eq("status", "posted")\
                .order("posted_at", desc=True)\
                .limit(10)\
                .execute()

            recent_posts_data = []
            for post in (recent_posts.data or []):
                recent_posts_data.append({
                    "id": post["id"],
                    "platform": post["platform"],
                    "posted_at": post["posted_at"],
                    "content": post.get("content", {})
                })

            # Get platform analytics
            platform_analytics = await self._get_platform_analytics(user_id, db_client)

            return {
                "total_posts": posted_count,
                "total_content_created": total_content,
                "total_scheduled": total_scheduled,
                "total_published": published_count,
                "total_draft": draft_count,
                "platforms_connected": connected_platforms,
                "platform_analytics": platform_analytics,
                "recent_posts": recent_posts_data
            }

        except Exception as e:
            logger.error(f"❌ Failed to get overall analytics: {str(e)}")
            return {
                "total_posts": 0,
                "total_content_created": 0,
                "total_scheduled": 0,
                "total_published": 0,
                "platforms_connected": [],
                "platform_analytics": [],
                "recent_posts": []
            }

    async def _get_platform_analytics(
        self,
        user_id: str,
        db_client: AppwriteClient
    ) -> List[Dict[str, Any]]:
        """Get analytics breakdown by platform"""
        try:
            # Get all posted posts grouped by platform
            posts = db_client.table("scheduled_posts")\
                .select("*, post_analytics(*)")\
                .eq("user_id", user_id)\
                .eq("status", "posted")\
                .execute()

            platform_stats = {}

            for post in (posts.data or []):
                platform = post["platform"]

                if platform not in platform_stats:
                    platform_stats[platform] = {
                        "platform": platform,
                        "total_posts": 0,
                        "total_likes": 0,
                        "total_comments": 0,
                        "total_shares": 0,
                        "total_impressions": 0,
                        "engagement_rates": []
                    }

                platform_stats[platform]["total_posts"] += 1

                # Get analytics if available
                analytics = post.get("post_analytics", [])
                if analytics and len(analytics) > 0:
                    latest = analytics[0]
                    platform_stats[platform]["total_likes"] += latest.get(
                        "likes", 0)
                    platform_stats[platform]["total_comments"] += latest.get(
                        "comments", 0)
                    platform_stats[platform]["total_shares"] += latest.get(
                        "shares", 0)
                    platform_stats[platform]["total_impressions"] += latest.get(
                        "impressions", 0)
                    platform_stats[platform]["engagement_rates"].append(
                        latest.get("engagement_rate", 0.0))

            # Calculate averages
            result = []
            for platform, stats in platform_stats.items():
                avg_engagement = (
                    sum(stats["engagement_rates"]) / len(stats["engagement_rates"])
                    if stats["engagement_rates"] else 0.0
                )

                result.append({
                    "platform": platform,
                    "total_posts": stats["total_posts"],
                    "total_likes": stats["total_likes"],
                    "total_comments": stats["total_comments"],
                    "total_shares": stats["total_shares"],
                    "total_impressions": stats["total_impressions"],
                    "avg_engagement_rate": round(avg_engagement, 2)
                })

            return result

        except Exception as e:
            logger.error(f"❌ Failed to get platform analytics: {str(e)}")
            return []

    async def get_engagement_trends(
        self,
        user_id: str,
        db_client: AppwriteClient,
        days: int = 30
    ) -> List[Dict[str, Any]]:
        """Get engagement trends over time"""
        try:
            # Get analytics from the last N days
            start_date = datetime.now(timezone.utc) - timedelta(days=days)

            analytics = db_client.table("post_analytics")\
                .select("*, scheduled_posts!inner(user_id, posted_at)")\
                .eq("scheduled_posts.user_id", user_id)\
                .gte("fetched_at", start_date.isoformat())\
                .execute()

            # Group by date
            trends_by_date = {}

            for record in (analytics.data or []):
                posted_at = record.get("scheduled_posts", {}).get("posted_at")
                if not posted_at:
                    continue

                date = datetime.fromisoformat(posted_at).date().isoformat()

                if date not in trends_by_date:
                    trends_by_date[date] = {
                        "date": date,
                        "likes": 0,
                        "comments": 0,
                        "shares": 0,
                        "impressions": 0
                    }

                trends_by_date[date]["likes"] += record.get("likes", 0)
                trends_by_date[date]["comments"] += record.get("comments", 0)
                trends_by_date[date]["shares"] += record.get("shares", 0)
                trends_by_date[date]["impressions"] += record.get(
                    "impressions", 0)

            # Sort by date
            trends = sorted(trends_by_date.values(), key=lambda x: x["date"])

            return trends

        except Exception as e:
            logger.error(f"❌ Failed to get engagement trends: {str(e)}")
            return []

    async def get_top_performing_content(
        self,
        user_id: str,
        db_client: AppwriteClient,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get top performing content by engagement"""
        try:
            # Get all analytics with content
            analytics = db_client.table("post_analytics")\
                .select("*, scheduled_posts!inner(user_id, content_id, platform, posted_at), content:content_id(*)")\
                .eq("scheduled_posts.user_id", user_id)\
                .order("engagement_rate", desc=True)\
                .limit(limit)\
                .execute()

            top_content = []

            for record in (analytics.data or []):
                content = record.get("content", {})
                scheduled_post = record.get("scheduled_posts", {})

                top_content.append({
                    "content_id": content.get("id"),
                    "title": content.get("title"),
                    "content_type": content.get("content_type"),
                    "platform": scheduled_post.get("platform"),
                    "posted_at": scheduled_post.get("posted_at"),
                    "likes": record.get("likes", 0),
                    "comments": record.get("comments", 0),
                    "shares": record.get("shares", 0),
                    "impressions": record.get("impressions", 0),
                    "engagement_rate": record.get("engagement_rate", 0.0)
                })

            return top_content

        except Exception as e:
            logger.error(f"❌ Failed to get top performing content: {str(e)}")
            return []


# Global analytics service instance
analytics_service = AnalyticsService()
