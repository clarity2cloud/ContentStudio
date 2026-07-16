"""
Background task for Viral Intel RAG pipeline.

Why Celery?
  The Viral Intel scan takes 30–60 seconds:
    • 9 parallel scrapers  (Reddit, YouTube, Google Trends, RSS,
                            HackerNews, Mastodon, Wikipedia, TikTok, Twitter)
    • 1 LLM call           (generates 10 content angles from scraped data)
    • 1 Appwrite save      (stores angles in Content Library)
    • 1 billing deduction  (deducts 50 credits)

  Blocking the HTTP request for 60 s is fragile:
    — Ingress timeout, pod restart, or load-balancer disconnect = silent failure
    — User has no progress feedback during the wait
    — Pod memory spikes on concurrent scans block API traffic

  With Celery:
    — POST /trends/scout returns {task_id} in < 100 ms
    — Worker handles the scan in the background
    — Frontend polls GET /trends/scout/{task_id} every 3 s
    — On pod restart, Celery retries the task automatically

Graceful degradation:
  In development (ENV=development or no REDIS_URL) the task runs eagerly
  (task_always_eager=True in celery_app.py) — no broker needed, behaves
  exactly like a synchronous call.
"""

import asyncio
import concurrent.futures
import time as _time
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.celery_app import celery_app
from app.services.trend_service import trend_service
from app.services.ai_service import ai_service
from app.utils.logger import logger

# IST = UTC+5:30 (same as trends.py)
_IST = timezone(timedelta(hours=5, minutes=30))


# ── Async helper (same pattern as campaign_tasks.py) ─────────────────────────

def _run_async(coro):
    """Run an async coroutine safely from a sync Celery task."""
    try:
        asyncio.get_running_loop()
        # Event loop already running — run in a new thread
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    except RuntimeError:
        # No event loop — just run directly
        return asyncio.run(coro)


# ── Markdown formatter (duplicated from trends.py to avoid circular imports) ──

def _format_angles_as_markdown(angles: list, keyword: str, days: int) -> str:
    lines = [
        f"# Viral Intel Report — {keyword}",
        f"*Scanned: {datetime.now(_IST).strftime('%d %b %Y, %H:%M IST')} | {days}-day window*",
        "",
        "---",
        "",
    ]
    for angle in angles:
        rank = angle.get("rank", "")
        headline = angle.get("angle", "")
        demand = angle.get("demand_score", "")
        trigger = angle.get("trigger", "")
        fmt = angle.get("format", "")
        why = angle.get("why_trending", "")
        hook = angle.get("hook", "")
        caption = angle.get("caption", "")
        visual_style = angle.get("visual_style", "")
        audience = angle.get("target_audience", "")
        best_time = angle.get("best_time", "")
        image_prompt = angle.get("image_prompt", "")
        hashtags = " ".join(angle.get("hashtags", []))
        platforms = ", ".join(angle.get("platforms_signal", []))

        lines += [
            f"## {rank}. {headline}",
            f"**Demand Score:** {demand}/100 | **Trigger:** {trigger} | **Format:** {fmt}",
            "",
            f"**Why it's trending:** {why}",
            "",
            f'**Hook:** "{hook}"',
            "",
        ]
        if caption:
            lines += [f"**Caption:** {caption}", ""]
        if visual_style:
            lines += [f"**Visual Style:** {visual_style}", ""]
        if audience:
            lines += [f"**Target Audience:** {audience}", ""]
        if best_time:
            lines += [f"**Best Time to Post:** {best_time}", ""]
        if platforms:
            lines += [f"**Seen on:** {platforms}", ""]
        if hashtags:
            lines += [f"**Hashtags:** {hashtags}", ""]
        if image_prompt:
            lines += [f"**Image Prompt:** {image_prompt}", ""]
        lines += ["---", ""]

    return "\n".join(lines).strip()


# ── Celery Task ─────────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    queue="ai",
    name="app.tasks.viral_intel_tasks.run_viral_intel_scan",
    time_limit=180,        # hard kill after 3 min
    soft_time_limit=150,   # SoftTimeLimitExceeded raised at 2m30s — allows clean exit
    max_retries=2,
    default_retry_delay=15,
    acks_late=True,
)
def run_viral_intel_scan(
    self,
    keyword: str,
    days: int,
    brand_id: Optional[str],
    user_id: str,
    tenant_id: str,
) -> dict:
    """
    Full Viral Intel RAG pipeline:
      1. Scrape 9 sources in parallel
      2. LLM generates 10 content angles from scraped data
      3. Save angles to Content Library (Appwrite)
      4. Deduct 50 credits from tenant billing

    Returns the same dict shape as the old synchronous endpoint so the
    frontend polling path and the dev-mode eager path are identical.
    """
    import traceback as _tb
    t0 = _time.monotonic()

    logger.info(
        f"[VI_TASK] Starting — keyword={keyword!r} days={days} user={user_id[:8]}")

    try:
        # ── Step 1: Scrape all sources in parallel ───────────────────────────
        signals = _run_async(trend_service.aggregate(keyword, days))

        data_points = {
            "reddit": len(signals["reddit"]),
            "youtube": len(signals["youtube"]),
            "google_trends": len(signals["google_trends"]),
            "rss": len(signals["rss"]),
            "hackernews": len(signals["hackernews"]),
            "mastodon": len(signals["mastodon"]),
            "wikipedia": len(signals["wikipedia"]),
            "tiktok": len(signals["tiktok"]),
        }
        total_points = sum(data_points.values())
        active_sources = sum(
            1 for v in signals["sources_active"].values() if v)
        logger.info(
            f"[VI_TASK] {total_points} data points from {active_sources}/8 sources")

        # ── Step 2: Resolve brand context (so angles adapt to brand voice) ────
        brand_context = ""
        if brand_id:
            try:
                from app.db.appwrite_client import get_appwrite_client
                from app.services.content_helpers import resolve_brand
                _db = get_appwrite_client()
                brand_context = resolve_brand(_db, brand_id)
                if brand_context:
                    logger.info(
                        f"[VI_TASK] Brand context resolved for {brand_id[:8]}")
            except Exception as e:
                logger.warning(
                    f"[VI_TASK] Brand context resolution failed (non-fatal): {e}")

        # ── Step 3: Generate AI angles from scraped data + brand context ─────
        angles = _run_async(
            ai_service.generate_trend_angles(
                signals, keyword, brand_context))
        logger.info(
            f"[VI_TASK] Generated {len(angles)} angles in {int(_time.monotonic()-t0)}s")

        # ── Step 3: Save to Content Library ──────────────────────────────────
        content_id: Optional[str] = None
        save_error: Optional[str] = None

        if angles and not (
                len(angles) == 1 and angles[0].get("demand_score") == 0):
            try:
                from app.db.appwrite_client import get_appwrite_client
                from app.services.content_helpers import save_content

                db = get_appwrite_client()
                markdown_body = _format_angles_as_markdown(
                    angles, keyword, days)
                content_id = save_content(
                    db=db,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    brand_id=brand_id,
                    title=f"Viral Intel: {keyword[:80]}",
                    content=markdown_body,
                    content_type="viral_intel",
                    metadata={
                        "keyword": keyword,
                        "days": days,
                        "angle_count": len(angles),
                        "sources": signals["sources_active"],
                        "data_points": data_points,
                        # Brand-adaptation traceability — months later you can still see
                        # whether this report was tuned to a brand and which one.
                        "brand_adapted": bool(brand_context),
                        "brand_id": brand_id,
                    },
                    platform="viral_intel",
                )
                if content_id:
                    logger.info(f"[VI_TASK] Saved → content_id={content_id}")
                else:
                    save_error = "save returned None — check ERROR logs"
                    logger.error(
                        f"[VI_TASK] Save returned None for user={user_id} keyword={keyword!r}")
            except Exception as e:
                save_error = f"{type(e).__name__}: {e}"
                logger.error(f"[VI_TASK] Save failed: {e}\n{_tb.format_exc()}")

        duration_s = int(_time.monotonic() - t0)
        logger.info(
            f"[VI_TASK] Completed in {duration_s}s — {len(angles)} angles, content_id={content_id}")

        return {
            "keyword": keyword,
            "days": days,
            "sources": signals["sources_active"],
            "data_points": data_points,
            "angles": angles,
            "content_id": content_id,
            "save_error": save_error,
            "duration_s": duration_s,
            # Lets the frontend show a "Tuned to {brand}" badge
            "brand_adapted": bool(brand_context),
            "brand_id": brand_id,
        }

    except Exception as exc:
        logger.error(
            f"[VI_TASK] Failed for keyword={keyword!r}: {exc}\n{_tb.format_exc()}")
        # Retry up to max_retries times with a short backoff
        raise self.retry(exc=exc, countdown=15)
