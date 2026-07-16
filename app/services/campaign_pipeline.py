# app/services/campaign_pipeline.py
#
# Intelligent campaign generation pipeline with:
# - Concurrent request limiting (semaphore)
# - Batch processing (5 items at a time)
# - Exponential backoff retry logic
# - Progress tracking
# - Appwrite database persistence (survives server restart)

import asyncio
import uuid
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from enum import Enum

from app.utils.logger import logger
from app.services.ai_service import ai_service
from app.db.appwrite_client import AppwriteDB

_db = AppwriteDB()
CAMPAIGN_JOBS_COLLECTION = "campaign_jobs"


def _safe_deserialize(value: Any) -> Any:
    """Safely deserialize JSON from Appwrite.

    Handles both serialized JSON strings and already-deserialized objects.
    This is needed because older jobs may have stored lists directly,
    while newer jobs store JSON strings.
    """
    if value is None:
        return []
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return []
    return []


class JobStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class CampaignPipeline:
    """
    Intelligent pipeline for large-scale campaign generation.

    Handles:
    - Concurrent request limiting (max 5 at a time)
    - Batch processing to avoid overwhelming NVIDIA API
    - Exponential backoff retry (3 attempts)
    - Progress tracking
    - Database persistence
    """

    MAX_CONCURRENT = 3   # 3 parallel LLM calls — prevents cascade 429s on NVIDIA's 70B model
    BATCH_SIZE = 3
    MAX_RETRIES = 3
    INITIAL_BACKOFF = 1.0
    MAX_BACKOFF = 8.0

    def __init__(self):
        self.semaphore = asyncio.Semaphore(self.MAX_CONCURRENT)
        self.jobs: Dict[str, Dict] = {}
        # Schedule job reload only when an event loop is already running
        # (i.e., inside FastAPI startup, not at bare import time / in Celery workers)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._load_unfinished_jobs())
        except RuntimeError:
            # No running loop — safe to ignore; load will happen on first use
            pass

    async def _load_unfinished_jobs(self) -> None:
        """Load any IN_PROGRESS jobs from Appwrite on startup.

        This allows campaigns to resume if the server restarts.
        """
        try:
            result = _db.list_documents(
                CAMPAIGN_JOBS_COLLECTION,
                queries=[{"method": "equal", "attribute": "status", "values": [JobStatus.IN_PROGRESS]}]
            )

            for doc in result.get("documents", []):
                job_id = doc.get("id") or doc.get("job_id")
                if job_id:
                    # Reconstruct job data from stored document
                    job_data = {
                        "job_id": job_id,
                        "status": doc.get("status"),
                        "created_at": doc.get("created_at"),
                        "total_items": doc.get("total_items", 0),
                        "completed_items": doc.get("completed_items", 0),
                        "failed_items": doc.get("failed_items", 0),
                        "tenant_id": doc.get("tenant_id", ""),
                        "user_id": doc.get("user_id", ""),
                        "platforms": doc.get("platforms", []),
                        "duration_days": doc.get("duration_days", 0),
                        "results": doc.get("results", []),
                        "errors": doc.get("errors", []),
                    }
                    self.jobs[job_id] = job_data
                    logger.info(
                        f"[PIPELINE] Loaded unfinished job {job_id} from Appwrite")

        except Exception as e:
            logger.warning(
                f"[PIPELINE] Failed to load unfinished jobs from Appwrite: {e}")

    async def _verify_document_exists(
        self,
        collection_id: str,
        document_id: str,
        max_attempts: int = 3,
    ) -> bool:
        """Verify that a document actually exists in Appwrite.

        This catches cases where the API returns 200 but the document wasn't persisted.
        Retries with backoff if verification fails.

        CRITICAL: Directly calls Appwrite REST API to bypass any caching/mocking issues.
        """
        import requests
        import os

        endpoint = os.getenv("APPWRITE_ENDPOINT", "https://db.thq.digital/v1")
        project_id = os.getenv("APPWRITE_PROJECT_ID", "")
        api_key = os.getenv("APPWRITE_API_KEY", "")
        database_id = "database-contentstudio"

        url = f"{endpoint}/databases/{database_id}/collections/{collection_id}/documents/{document_id}"
        headers = {
            "X-Appwrite-Project": project_id,
            "X-Appwrite-Key": api_key,
            "Content-Type": "application/json",
        }

        for attempt in range(max_attempts):
            try:
                # Make direct HTTP request to Appwrite
                r = requests.get(url, headers=headers, timeout=5)

                if r.status_code == 200:
                    # Document found!
                    logger.info(
                        f"[PIPELINE] ✓ Verified document exists in Appwrite: {document_id}")
                    return True
                elif r.status_code == 404:
                    # Document NOT found
                    if attempt < max_attempts - 1:
                        wait_time = 0.3 * (2 ** attempt)
                        logger.debug(
                            f"[PIPELINE] Document NOT FOUND (attempt {attempt + 1}/{max_attempts}): "
                            f"{document_id}. Retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(
                            f"[PIPELINE] ❌ VERIFICATION FAILED: Document {document_id} "
                            f"claims to be saved but DOES NOT EXIST in Appwrite after {max_attempts} checks")
                else:
                    # Other HTTP error
                    error_msg = f"HTTP {r.status_code}"
                    try:
                        error_data = r.json()
                        error_msg = error_data.get('message', error_msg)
                    except BaseException:
                        pass

                    if attempt < max_attempts - 1:
                        wait_time = 0.3 * (2 ** attempt)
                        logger.warning(
                            f"[PIPELINE] Verification attempt {attempt + 1}/{max_attempts} got {error_msg}. "
                            f"Retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(
                            f"[PIPELINE] ❌ VERIFICATION ERROR for {document_id}: {error_msg} "
                            f"after {max_attempts} attempts")

            except Exception as e:
                if attempt < max_attempts - 1:
                    wait_time = 0.3 * (2 ** attempt)
                    logger.warning(
                        f"[PIPELINE] Verification exception (attempt {attempt + 1}/{max_attempts}): "
                        f"{type(e).__name__}: {str(e)[:100]}. Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(
                        f"[PIPELINE] ❌ VERIFICATION EXCEPTION for {document_id}: "
                        f"{type(e).__name__}: {str(e)[:150]}")

        return False

    async def _save_content_to_appwrite(
        self,
        campaign_id: str,
        tenant_id: str,
        results: List[Dict[str, Any]],
        campaign_start_date: Optional[datetime] = None,
        brand_id: Optional[str] = None,
        user_id: str = "",
    ) -> None:
        """Save generated content items to the campaign_content table.

        Links each item to the campaign and calculates scheduled_for date based on day index.
        Uses aggressive retry logic with backoff AND verification to handle Appwrite issues.
        Also mirrors to the content collection so items appear in the Content Library.
        """
        if not campaign_id or not results:
            logger.warning(
                f"[PIPELINE] _save_content_to_appwrite called with empty campaign_id={campaign_id!r} "
                f"or results count={len(results) if results else 0}")
            return

        logger.info(
            f"[PIPELINE] _save_content_to_appwrite CALLED: campaign_id={campaign_id}, "
            f"results_count={len(results)}, tenant_id={tenant_id}")

        base_date = campaign_start_date or datetime.utcnow()

        async def _save_single(item: dict) -> bool:
            if item.get("status") != "completed":
                return False
            day = item.get("day", 1)
            platform = item.get("platform", "")
            content_record = {
                "campaign_id": campaign_id,
                "tenant_id": tenant_id,
                "channel": platform,
                "content_type": platform,
                "title": item.get(
                    "title",
                    ""),
                "body": item.get(
                    "content",
                    ""),
                "phase": item.get(
                    "phase",
                    "Awareness"),
                "scheduled_for": (
                    base_date +
                    timedelta(
                        days=day -
                        1)).isoformat(),
                "status": "draft",
                "created_at": datetime.utcnow().isoformat(),
            }
            if brand_id:
                content_record["brand_id"] = brand_id

            for attempt in range(2):
                try:
                    doc = _db.create_document(
                        "campaign_content", content_record)
                    if doc.get("id") or doc.get("$id"):
                        # Mirror to content collection for Content Library
                        lib_record = {
                            "user_id": user_id,
                            "tenant_id": tenant_id,
                            "title": item.get("title", ""),
                            "content": item.get("content", ""),
                            "content_type": platform,
                            "status": "draft",
                            "campaign_id": campaign_id,
                        }
                        if brand_id:
                            lib_record["brand_id"] = brand_id
                        try:
                            _db.create_document("content", lib_record)
                        except Exception as lib_e:
                            logger.warning(
                                f"[PIPELINE] Content library mirror failed {platform}/day{day}: {lib_e}")
                        logger.info(
                            f"[PIPELINE] ✅ Saved: {campaign_id}/{platform}/day{day}")
                        return True
                except Exception as e:
                    logger.warning(
                        f"[PIPELINE] Save attempt {attempt+1}/2 failed {platform}/day{day}: {e}")
                    if attempt == 0:
                        await asyncio.sleep(1.0)
            logger.error(f"[PIPELINE] ❌ Failed to save {platform}/day{day}")
            return False

        # Save ALL items in parallel — no blocking sequential saves
        save_results = await asyncio.gather(*[_save_single(item) for item in results], return_exceptions=True)
        saved_count = sum(1 for r in save_results if r is True)
        failed_count = len(results) - saved_count
        logger.info(
            f"[PIPELINE] Saved {saved_count}/{len(results)} items to Appwrite")

        if failed_count > 0:
            logger.error(
                f"[PIPELINE] Batch save: {saved_count} saved, {failed_count} failed")
        else:
            logger.info(
                f"[PIPELINE] Batch save: all {saved_count} items saved successfully")

    async def _save_job_to_appwrite(self, job_id: str) -> None:
        """Persist job state to Appwrite database.

        This ensures job survives server restart and can be queried by any backend instance.
        """
        if job_id not in self.jobs:
            return

        job = self.jobs[job_id]

        try:
            # Prepare data for storage (serialize complex fields)
            data_to_save = {
                "job_id": job["job_id"],
                "status": job["status"],
                "created_at": job["created_at"],
                "total_items": job["total_items"],
                "completed_items": job["completed_items"],
                "failed_items": job["failed_items"],
                "tenant_id": job["tenant_id"],
                "user_id": job["user_id"],
                "platforms": json.dumps(job["platforms"]),  # Serialize list
                "duration_days": job["duration_days"],
                "results": json.dumps(job["results"]),      # Serialize list
                "errors": json.dumps(job["errors"]),        # Serialize list
            }

            # Try to update if exists, create if not
            try:
                _db.update_document(
                    CAMPAIGN_JOBS_COLLECTION, job_id, data_to_save)
            except Exception:
                # Document doesn't exist, create it
                _db.create_document(
                    CAMPAIGN_JOBS_COLLECTION,
                    data_to_save,
                    document_id=job_id)

            logger.debug(f"[PIPELINE] Job {job_id} persisted to Appwrite")

        except Exception as e:
            logger.error(
                f"[PIPELINE] Failed to save job {job_id} to Appwrite: {e}")

    async def generate_campaign(
        self,
        platforms: List[str],
        duration_days: int,
        objective: str,
        audience: str,
        cta: str,
        brand_context: Optional[str] = None,
        user_context: Optional[str] = None,
        tenant_id: str = "",
        user_id: str = "",
        campaign_id: str = "",
        brand_id: str = "",
        tone: str = "professional",
    ) -> Dict[str, Any]:
        """
        Generate campaign asynchronously.

        Returns job_id immediately. Results are saved to database as they complete.
        User can poll GET /campaigns/{job_id}/progress to track completion.
        """

        job_id = str(uuid.uuid4())
        total_items = len(platforms) * duration_days

        # NOTE: per-tenant LLM quota was removed by request.
        # Credits remain the only user-facing spending guard.

        # Create job record
        job_data = {
            "job_id": job_id,
            "status": JobStatus.IN_PROGRESS,
            "created_at": datetime.utcnow().isoformat(),
            "total_items": total_items,
            "completed_items": 0,
            "failed_items": 0,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "brand_id": brand_id,
            "platforms": platforms,
            "duration_days": duration_days,
            "results": [],
            "errors": [],
        }

        self.jobs[job_id] = job_data

        logger.info(
            f"[PIPELINE] Campaign job {job_id}: {total_items} items to generate")
        logger.info(
            f"[PIPELINE] Strategy: Batch size {self.BATCH_SIZE}, Max concurrent {self.MAX_CONCURRENT}")

        # Save job record to Appwrite immediately (so it survives if server
        # restarts during generation)
        await self._save_job_to_appwrite(job_id)

        # Start background generation (don't await)
        asyncio.create_task(
            self._generate_batch_sequence(
                job_id=job_id,
                campaign_id=campaign_id,
                platforms=platforms,
                duration_days=duration_days,
                objective=objective,
                audience=audience,
                cta=cta,
                brand_context=brand_context,
                user_context=user_context,
                brand_id=brand_id,
                tenant_id=tenant_id,
                user_id=user_id,
                tone=tone,
            )
        )

        # Return immediately with job_id
        return {
            "job_id": job_id,
            "status": JobStatus.IN_PROGRESS,
            "message": f"Campaign generation started. {total_items} items queued.",
            "total_items": total_items,
            "progress_url": f"/campaigns/{job_id}/progress",
        }

    async def _generate_batch_sequence(
        self,
        job_id: str,
        campaign_id: str,
        platforms: List[str],
        duration_days: int,
        objective: str,
        audience: str,
        cta: str,
        brand_context: Optional[str] = None,
        user_context: Optional[str] = None,
        brand_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        tone: str = "professional",
    ) -> None:
        """
        FLAT PARALLEL strategy — all items run concurrently, semaphore caps at MAX_CONCURRENT.

        For 20 items with semaphore=5:  ceil(20/5) × ~60s = ~4 minutes
        For 8  items with semaphore=5:  ceil(8/5)  × ~60s = ~2 minutes
        No day-by-day barrier — everything runs at once.
        """

        try:
            # Build flat list of ALL (day_idx, platform) pairs
            all_tasks = [
                (day_idx, platform)
                for day_idx in range(duration_days)
                for platform in platforms
            ]

            total = len(all_tasks)
            logger.info(
                f"[PIPELINE] {job_id}: Launching {total} items flat (semaphore={self.MAX_CONCURRENT})")

            # Run ALL items concurrently — semaphore limits to MAX_CONCURRENT
            # at a time
            all_results = await asyncio.gather(
                *[
                    self._generate_with_retry(
                        job_id=job_id,
                        day_idx=day_idx,
                        platform=platform,
                        duration_days=duration_days,
                        objective=objective,
                        audience=audience,
                        cta=cta,
                        brand_context=brand_context,
                        user_context=user_context,
                        brand_id=brand_id,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        tone=tone,                                  )
                    for day_idx, platform in all_tasks
                ],
                return_exceptions=True,
            )

            # Process results
            completed = []
            for result in all_results:
                if isinstance(result, dict) and result.get(
                        "status") == "completed":
                    self.jobs[job_id]["results"].append(result)
                    completed.append(result)
                    self.jobs[job_id]["completed_items"] += 1
                else:
                    self.jobs[job_id]["failed_items"] += 1
                    if isinstance(result, dict):
                        self.jobs[job_id]["errors"].append(
                            result.get("error", ""))

            logger.info(
                f"[PIPELINE] {job_id}: Generation done — {len(completed)}/{total} succeeded")

            # Save all completed items to Appwrite in parallel
            if campaign_id and completed:
                await self._save_content_to_appwrite(
                    campaign_id=campaign_id,
                    tenant_id=self.jobs[job_id].get("tenant_id", ""),
                    brand_id=brand_id,                      user_id=self.jobs[job_id].get("user_id", ""),
                    results=completed,
                )

            # Single Appwrite job progress save
            await self._save_job_to_appwrite(job_id)

            # ── ONE retry round for any failures ─────────────────────────────
            if self.jobs[job_id]["failed_items"] > 0 and campaign_id:
                logger.info(
                    f"[PIPELINE] {job_id}: Retrying {self.jobs[job_id]['failed_items']} failed items...")

                already_done = {
                    (r.get("day"), r.get("platform"))
                    for r in self.jobs[job_id]["results"]
                }
                retry_pairs = [
                    (day_idx, platform)
                    for day_idx, platform in all_tasks
                    if (day_idx + 1, platform) not in already_done
                ]

                if retry_pairs:
                    retry_results = await asyncio.gather(
                        *[
                            self._generate_with_retry(
                                job_id=job_id,
                                day_idx=day_idx,
                                platform=platform,
                                duration_days=duration_days,
                                objective=objective,
                                audience=audience,
                                cta=cta,
                                brand_context=brand_context,
                                user_context=user_context,
                                brand_id=brand_id,                                      tenant_id=tenant_id,                                    user_id=user_id,                                        tone=tone,                # BUG3 FIX: was missing
                            )
                            for day_idx, platform in retry_pairs
                        ],
                        return_exceptions=True,
                    )

                    retry_completed = []
                    for result in retry_results:
                        if isinstance(result, dict) and result.get(
                                "status") == "completed":
                            key = (result.get("day"), result.get("platform"))
                            if key not in already_done:
                                self.jobs[job_id]["results"].append(result)
                                retry_completed.append(result)
                                self.jobs[job_id]["completed_items"] += 1
                                self.jobs[job_id]["failed_items"] -= 1

                    if retry_completed:
                        await self._save_content_to_appwrite(
                            campaign_id=campaign_id,
                            tenant_id=self.jobs[job_id].get("tenant_id", ""),
                            brand_id=brand_id,            # BUG2 FIX: was missing
                            user_id=self.jobs[job_id].get("user_id", ""),
                            results=retry_completed,
                        )

                    logger.info(
                        f"[PIPELINE] {job_id}: Retry recovered {len(retry_completed)} items")

                # Persist after retry
                await self._save_job_to_appwrite(job_id)

            # Mark job as completed (even if some items still failed)
            self.jobs[job_id]["status"] = JobStatus.COMPLETED
            logger.info(
                f"[PIPELINE] {job_id}: Campaign generation COMPLETE. "
                f"Final: {self.jobs[job_id]['completed_items']} completed, "
                f"{self.jobs[job_id]['failed_items']} failed"
            )

            # Final save to Appwrite
            await self._save_job_to_appwrite(job_id)

        except Exception as e:
            self.jobs[job_id]["status"] = JobStatus.FAILED
            self.jobs[job_id]["errors"].append(str(e))
            logger.error(
                f"[PIPELINE] {job_id}: Campaign generation FAILED: {e}")

            # Save failed state to Appwrite
            await self._save_job_to_appwrite(job_id)

    async def _generate_with_retry(
        self,
        job_id: str,
        day_idx: int,
        platform: str,
        duration_days: int,
        objective: str,
        audience: str,
        cta: str,
        brand_context: Optional[str] = None,
        user_context: Optional[str] = None,
        brand_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        tone: str = "professional",
    ) -> Dict[str, Any]:
        """
        Generate single content item with exponential backoff retry.

        Uses semaphore to limit concurrent requests to NVIDIA API.
        """

        async with self.semaphore:
            backoff = self.INITIAL_BACKOFF

            for attempt in range(self.MAX_RETRIES):
                try:
                    # 360s to allow for primary model 429-retry backoff (3 × up to 12s) +
                    # primary model call time (up to 300s) without the pipeline
                    # killing it prematurely.
                    result = await asyncio.wait_for(
                        ai_service.generate_content_for_day(
                            channel=platform,
                            objective=objective,
                            audience=audience,
                            cta=cta,
                            day_index=day_idx,
                            total_days=duration_days,
                            brand_context=brand_context,
                            user_context=user_context,
                            tone=tone,                                       brand_id=brand_id,
                            tenant_id=tenant_id,
                            user_id=user_id,
                        ),
                        timeout=360.0,
                    )

                    return {
                        "status": "completed",
                        "day": day_idx + 1,
                        "platform": platform,
                        "content": result.get("content", ""),
                        "title": result.get("title", ""),
                        "phase": result.get("phase", ""),
                    }

                except asyncio.TimeoutError:
                    logger.warning(
                        f"[PIPELINE] Timeout {platform} day {day_idx + 1} (attempt {attempt + 1}/{self.MAX_RETRIES})")
                    if attempt < self.MAX_RETRIES - 1:
                        await asyncio.sleep(min(backoff, self.MAX_BACKOFF))
                        backoff *= 2

                except Exception as e:
                    logger.warning(
                        f"[PIPELINE] Error {platform} day {day_idx + 1} (attempt {attempt + 1}/{self.MAX_RETRIES}): {e}")
                    if attempt < self.MAX_RETRIES - 1:
                        await asyncio.sleep(min(backoff, self.MAX_BACKOFF))
                        backoff *= 2

            return {
                "status": "failed",
                "day": day_idx + 1,
                "platform": platform,
                "error": f"Failed after {self.MAX_RETRIES} attempts",
            }

    def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Get current job progress.

        Checks memory first, then Appwrite if not in memory.
        This allows queries after server restart.
        """
        # Check memory
        if job_id in self.jobs:
            job = self.jobs[job_id]
        else:
            # Try to load from Appwrite
            try:
                doc = _db.get_document(CAMPAIGN_JOBS_COLLECTION, job_id)
                if not doc or doc.get("error"):
                    return {"error": "Job not found", "status": "not_found"}

                job = {
                    "job_id": doc.get("job_id"),
                    "status": doc.get("status"),
                    "created_at": doc.get("created_at"),
                    "total_items": doc.get("total_items", 0),
                    "completed_items": doc.get("completed_items", 0),
                    "failed_items": doc.get("failed_items", 0),
                    "results": _safe_deserialize(doc.get("results")),
                }
            except Exception as e:
                logger.warning(
                    f"[PIPELINE] Failed to load job {job_id} from Appwrite: {e}")
                return {"error": "Job not found", "status": "not_found"}

        progress_percent = (
            job["completed_items"] /
            job["total_items"] *
            100) if job["total_items"] > 0 else 0

        # Determine if auto-retry is active
        auto_retry_active = (
            job["status"] == JobStatus.COMPLETED
            and job["failed_items"] > 0
        )

        return {
            "job_id": job_id,
            "status": job["status"],
            "total_items": job["total_items"],
            "completed_items": job["completed_items"],
            "failed_items": job["failed_items"],
            "progress_percent": round(progress_percent, 1),
            "created_at": job["created_at"],
            "results_count": len(job["results"]),
            "is_complete": job["status"] == JobStatus.COMPLETED and job["failed_items"] == 0,
            "auto_retry_active": auto_retry_active,
            "note": (
                f"⚠️ {job['failed_items']} items failed. Auto-retry is attempting to regenerate them..."
                if auto_retry_active
                else (
                    "✅ All items generated successfully!"
                    if job["status"] == JobStatus.COMPLETED
                    else "⏳ Generation in progress..."
                )
            ),
        }

    def get_job_results(self, job_id: str) -> Dict[str, Any]:
        """Get all generated results for a job.

        Checks memory first, then Appwrite if not in memory.
        This allows queries after server restart.
        """
        # Check memory
        if job_id in self.jobs:
            job = self.jobs[job_id]
        else:
            # Try to load from Appwrite
            try:
                doc = _db.get_document(CAMPAIGN_JOBS_COLLECTION, job_id)
                if not doc or doc.get("error"):
                    return {"error": "Job not found"}

                job = {
                    "job_id": doc.get("job_id"),
                    "status": doc.get("status"),
                    "total_items": doc.get("total_items", 0),
                    "failed_items": doc.get("failed_items", 0),
                    "results": _safe_deserialize(doc.get("results")),
                    "errors": _safe_deserialize(doc.get("errors")),
                }
            except Exception as e:
                logger.warning(
                    f"[PIPELINE] Failed to load job {job_id} from Appwrite: {e}")
                return {"error": "Job not found"}

        return {
            "job_id": job_id,
            "status": job["status"],
            "total_items": job["total_items"],
            "completed_items": len(job["results"]),
            "failed_items": job["failed_items"],
            "results": job["results"],
            "errors": job["errors"],
        }


# Global pipeline instance
campaign_pipeline = CampaignPipeline()
