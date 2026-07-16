# app/services/scheduler_service.py
#
# Post scheduling service — two modes depending on Redis availability:
#
#   ┌─ Redis available (production / Docker) ─────────────────────────────────┐
#   │ schedule_post  → Celery apply_async(eta=scheduled_time)                 │
#   │   • Task persisted in Redis — survives server restarts                  │
#   │   • Celery worker executes _execute_scheduled_post at the right time     │
#   │   • Celery Beat (check_and_publish_pending_posts) acts as a safety net  │
#   └─────────────────────────────────────────────────────────────────────────┘
#
#   ┌─ No Redis (dev / local) ────────────────────────────────────────────────┐
#   │ schedule_post  → APScheduler DateTrigger (in-process)                  │
#   │   • Classic behaviour, exactly as before                               │
#   └─────────────────────────────────────────────────────────────────────────┘
#
# Responsibilities:
#   • Queue posts for future execution
#   • Execute posts via platform services at fire time
#   • Retry failed posts (max 3 attempts, 15-min backoff)
#   • Reload pending jobs on server restart
#   • Publish-now (skip the queue and fire immediately)

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from datetime import datetime, timezone, timedelta
from typing import Optional
import json as _json

from app.utils.logger import logger
from app.utils.encryption import decrypt


# ── Platform services ───────────────────────────────────────────────────
try:
    from app.services.twitter_service import twitter_service
    from app.services.instagram_service import instagram_service
    from app.services.linkedin_service import linkedin_service
    from app.services.facebook_service import facebook_service
except ImportError:
    twitter_service = instagram_service = linkedin_service = facebook_service = None

RETRY_BACKOFF_MINUTES = 15   # wait between retries


class SchedulerService:

    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self._started = False
        logger.info("Scheduler service created (will start on first use)")

    def _ensure_started(self):
        """Start APScheduler lazily so it works without a running event loop at import time."""
        if not self._started:
            try:
                self.scheduler.start()
                self._started = True
                logger.info("APScheduler started")
            except Exception as e:
                logger.warning(f"APScheduler start: {e}")

    # ── Public API ──────────────────────────────────────────────────────────

    async def schedule_post(
            self,
            scheduled_post_id: str,
            scheduled_time: datetime):
        """
        Queue a post for future execution.

        With Redis → dispatch a Celery ETA task (task persists across restarts).
        Without Redis → APScheduler DateTrigger (in-process, dev mode).
        """
        from app.config import settings as _cfg
        if _cfg.REDIS_URL:
            try:
                from app.tasks.post_tasks import publish_post as _publish_task
                _publish_task.apply_async(
                    args=[scheduled_post_id],
                    eta=scheduled_time,
                    queue="posts",
                )
                logger.info(
                    f"⏰ [Celery] Queued post {scheduled_post_id} → {scheduled_time}"
                )
                return
            except Exception as celery_err:
                logger.warning(
                    f"⚠️ Celery dispatch failed, falling back to APScheduler: {celery_err}"
                )

        # Fallback / dev mode — APScheduler in-process
        self._ensure_started()
        try:
            job_id = f"post_{scheduled_post_id}"
            self.scheduler.add_job(
                self._execute_scheduled_post,
                trigger=DateTrigger(run_date=scheduled_time),
                args=[scheduled_post_id],
                id=job_id,
                replace_existing=True,
            )
            logger.info(
                f"⏰ [APScheduler] Queued post {scheduled_post_id} → {scheduled_time}")
        except Exception as e:
            logger.error(f"❌ schedule_post failed: {e}")
            raise

    async def publish_now(self, scheduled_post_id: str):
        """Bypass the queue and publish immediately."""
        await self._execute_scheduled_post(scheduled_post_id)

    def cancel_scheduled_post(self, scheduled_post_id: str):
        """
        Cancel a scheduled post.

        With Celery: DB status is updated to 'cancelled' by the caller (API layer).
          The in-flight ETA task will check the status on execution and exit if
          it's not 'scheduled'/'retrying' — so no explicit revocation needed.
        With APScheduler: remove the in-process job.
        """
        from app.config import settings as _cfg
        if not _cfg.REDIS_URL:
            # APScheduler path
            self._ensure_started()
            job_id = f"post_{scheduled_post_id}"
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
                logger.info(f"🚫 [APScheduler] Cancelled job {job_id}")
            else:
                logger.warning(f"⚠️ [APScheduler] Job not found: {job_id}")
        else:
            logger.info(
                f"🚫 [Celery] Post {scheduled_post_id} marked cancelled in DB "
                "— in-flight task will exit on status check"
            )

    async def reschedule_post(
            self,
            scheduled_post_id: str,
            new_scheduled_time: datetime):
        """
        Move an existing post to a new fire time.

        With Celery: schedule a new ETA task; the old one exits on status check.
        With APScheduler: use reschedule_job.
        """
        from app.config import settings as _cfg
        if _cfg.REDIS_URL:
            # Re-dispatch with new ETA — old task exits when it finds status
            # updated
            await self.schedule_post(scheduled_post_id, new_scheduled_time)
            logger.info(
                f"🔄 [Celery] Rescheduled {scheduled_post_id} → {new_scheduled_time}")
        else:
            # APScheduler path
            self._ensure_started()
            job_id = f"post_{scheduled_post_id}"
            try:
                self.scheduler.reschedule_job(
                    job_id, trigger=DateTrigger(run_date=new_scheduled_time)
                )
                logger.info(
                    f"🔄 [APScheduler] Rescheduled {job_id} → {new_scheduled_time}")
            except Exception as e:
                logger.error(f"❌ reschedule_post failed: {e}")
                raise

    async def load_pending_posts(self):
        """
        Reload all pending scheduled posts from DB on server restart.

        With Celery (Redis available):
          Future posts are re-dispatched as ETA tasks (the old ETA task was lost
          when Redis flushed or was restarted — this restores them).
          Overdue posts (scheduled_at in the past) are fired immediately.

        Without Celery:
          Only future posts are loaded into APScheduler.
          Overdue posts are fired immediately in-process.
        """
        from app.config import settings as _cfg
        try:
            from app.db.appwrite_client import get_appwrite_client
            client = get_appwrite_client()
            result = (
                client.table("scheduled_posts")
                .select("id, scheduled_at, status")
                .eq("status", "scheduled")
                .limit(500)
                .execute()
            )
            now = datetime.now(timezone.utc)
            count = 0
            overdue = 0
            for post in (result.data or []):
                try:
                    fire_at = datetime.fromisoformat(
                        post["scheduled_at"].replace("Z", "+00:00")
                    )
                    if fire_at <= now:
                        # Overdue — fire immediately
                        if _cfg.REDIS_URL:
                            from app.tasks.post_tasks import publish_post as _pt
                            _pt.apply_async(args=[post["id"]], queue="posts")
                        else:
                            await self._execute_scheduled_post(post["id"])
                        overdue += 1
                    else:
                        await self.schedule_post(post["id"], fire_at)
                        count += 1
                except Exception as inner:
                    logger.warning(
                        f"Could not re-queue post {post['id']}: {inner}")
            logger.info(
                f"♻️ Startup: {count} future post(s) re-queued, "
                f"{overdue} overdue post(s) fired immediately"
            )
        except Exception as e:
            logger.warning(f"Could not load pending posts on startup: {e}")

    def shutdown(self):
        if self._started:
            self.scheduler.shutdown()
        logger.info("Scheduler shutdown")

    # ── Execution engine ────────────────────────────────────────────────────

    async def _execute_scheduled_post(self, scheduled_post_id: str):
        """Called by APScheduler (or publish_now). Sends the post, handles retries."""
        from app.db.appwrite_client import get_appwrite_client
        db = get_appwrite_client()

        try:
            res = db.table("scheduled_posts").select(
                "*").eq("id", scheduled_post_id).execute()
            if not res.data:
                logger.error(f"❌ Post not found: {scheduled_post_id}")
                return

            post = res.data[0]

            # Only fire if still awaiting execution
            if post.get("status") not in ("scheduled", "queued", "retrying"):
                logger.info(
                    f"⏭️ Skipping post {scheduled_post_id} — status: {post.get('status')}")
                return

            # Mark as publishing
            db.table("scheduled_posts").update({"status": "publishing"}).eq(
                "id", scheduled_post_id).execute()

            content_text = post.get("content_text", "")
            platform = post.get("platform", "")

            # Fetch text from content library if inline text is missing
            if not content_text and post.get("content_id"):
                cnt = db.table("content").select("content").eq(
                    "id", post["content_id"]).execute()
                if cnt.data:
                    content_text = cnt.data[0].get("content", "")

            if not content_text:
                raise ValueError("No content text found for post")

            # Fetch connected social account
            account = self._get_social_account(db, post)

            platform_post_id = await self._dispatch(platform, content_text, post, account)

            # Success
            db.table("scheduled_posts").update({
                "status": "published",
                "published_at": datetime.now(timezone.utc).isoformat(),
                "platform_post_id": platform_post_id or "",
                "error_message": "",
            }).eq("id", scheduled_post_id).execute()

            # Sync content library status
            if post.get("content_id"):
                db.table("content").update({"status": "published"}).eq(
                    "id", post["content_id"]).execute()

            logger.info(
                f"✅ Published {platform} post {scheduled_post_id} → {platform_post_id}")

        except Exception as exc:
            logger.error(f"❌ Execution failed for {scheduled_post_id}: {exc}")
            await self._handle_failure(db, scheduled_post_id, str(exc))

    # ── Dispatch ────────────────────────────────────────────────────────────

    async def _dispatch(
            self,
            platform: str,
            text: str,
            post: dict,
            account: Optional[dict]) -> str:
        """Route to the right platform service. Returns platform-assigned post ID."""
        if platform == "twitter":
            if not account:
                raise ValueError("No Twitter account connected")
            result = await twitter_service.post_tweet(
                access_token=decrypt(account["access_token"]),
                tweet_text=text,
            )
            return result.get("tweet_id", "")

        elif platform == "instagram":
            if not account:
                raise ValueError("No Instagram account connected")
            meta = self._parse_meta(post.get("metadata"))
            image_url = (
                (post.get("media_urls") or [None])[0]
                or meta.get("image_url")
            )
            if not image_url:
                raise ValueError(
                    "Instagram posts require at least one media_url")
            result = await instagram_service.post_image(
                access_token=decrypt(account["access_token"]),
                instagram_account_id=account.get("account_id", ""),
                image_url=image_url,
                caption=text,
            )
            return result.get("post_id", "")

        elif platform == "linkedin":
            if not account:
                raise ValueError("No LinkedIn account connected")
            result = await linkedin_service.post_text(
                access_token=decrypt(account["access_token"]),
                person_urn=account.get("account_id", ""),
                text=text,
            )
            return result.get("post_id", "")

        elif platform == "facebook":
            if not account:
                raise ValueError("No Facebook account connected")
            result = await facebook_service.post_to_page(
                page_access_token=decrypt(account["access_token"]),
                page_id=account.get("account_id", ""),
                message=text,
            )
            return result.get("post_id", "")

        else:
            # Platforms without direct API integration yet — mark as published
            logger.warning(
                f"⚠️ No direct integration for {platform} — marking published (manual)")
            return f"manual_{platform}"

    # ── Retry logic ─────────────────────────────────────────────────────────

    async def _handle_failure(self, db, scheduled_post_id: str, error: str):
        """Increment retry counter; re-queue or mark as failed."""
        try:
            res = db.table("scheduled_posts").select(
                "retry_count, max_retries").eq("id", scheduled_post_id).execute()
            if not res.data:
                return

            retry_count = int(res.data[0].get("retry_count", 0))
            max_retries = int(res.data[0].get("max_retries", 3))
            retry_count += 1

            if retry_count <= max_retries:
                next_fire = datetime.now(
                    timezone.utc) + timedelta(minutes=RETRY_BACKOFF_MINUTES * retry_count)
                db.table("scheduled_posts").update({
                    "status": "retrying",
                    "retry_count": retry_count,
                    "error_message": f"Attempt {retry_count} failed: {error}",
                }).eq("id", scheduled_post_id).execute()
                await self.schedule_post(scheduled_post_id, next_fire)
                logger.info(
                    f"🔁 Retry {retry_count}/{max_retries} for {scheduled_post_id} at {next_fire}")
            else:
                db.table("scheduled_posts").update({
                    "status": "failed",
                    "retry_count": retry_count,
                    "error_message": error,
                }).eq("id", scheduled_post_id).execute()
                logger.error(
                    f"💀 Post {scheduled_post_id} permanently failed after {retry_count} retries")
        except Exception as e:
            logger.error(f"❌ _handle_failure itself failed: {e}")

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _get_social_account(self, db, post: dict) -> Optional[dict]:
        """Fetch the connected social account for a post."""
        acc_id = post.get("connected_account_id")
        platform = post.get("platform", "")
        user_id = post.get("user_id", "")

        try:
            if acc_id:
                res = db.table("social_accounts").select(
                    "*").eq("id", acc_id).execute()
            else:
                res = (
                    db.table("social_accounts")
                    .select("*")
                    .eq("user_id", user_id)
                    .eq("platform", platform)
                    .eq("is_active", True)
                    .execute()
                )
            return res.data[0] if res.data else None
        except Exception:
            return None

    @staticmethod
    def _parse_meta(meta) -> dict:
        if isinstance(meta, dict):
            return meta
        if isinstance(meta, str):
            try:
                return _json.loads(meta)
            except Exception:
                pass
        return {}


# ── Singleton ───────────────────────────────────────────────────────────
scheduler_service = SchedulerService()
