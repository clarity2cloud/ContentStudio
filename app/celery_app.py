# app/celery_app.py
"""
Celery application instance for ContentStudio AI.

Broker  : Redis  (same instance as the optional cache tier)
Backend : Redis  (stores task results for polling)

Workers execute:
  - Post publishing  (app.tasks.post_tasks)
  - Background AI generation  (app.tasks.ai_tasks)

Celery Beat runs periodic tasks:
  - check_and_publish_pending_posts  — safety-net every 60 s
    (primary timing is APScheduler or Celery ETA tasks; Beat catches edge cases)

Graceful degradation:
  If REDIS_URL is not set the Celery app object is created but unusable.
  All call-sites guard with `if settings.REDIS_URL` so nothing is dispatched
  when Redis is absent (dev / no-Docker mode falls back to APScheduler).
"""

from app.config import settings

# ── Optional Celery import with graceful fallback ───────────────────────────
try:
    from celery import Celery
    from kombu import Queue
except ImportError:
    # Mock Celery for development when Celery is not installed
    class MockConfig:
        """Mock configuration object for Celery."""
        def update(self, **kwargs):
            """Accept any configuration update."""
            pass

    class MockCelery:
        """Mock Celery app for development/demo mode without Celery installed."""
        def __init__(self, *args, **kwargs):
            self.conf = MockConfig()

        def task(self, *args, **kwargs):
            def decorator(func):
                # Add mock apply_async to function for task compatibility
                def mock_apply_async(*a, **k):
                    result = type('Result', (), {'id': 'mock', 'result': None})()
                    return result
                func.apply_async = mock_apply_async
                return func
            return decorator

        def AsyncResult(self, task_id):
            """Mock AsyncResult for task status polling."""
            return type('AsyncResult', (), {
                'id': task_id,
                'state': 'SUCCESS',
                'result': None,
                'info': {}
            })()

    # Mock Queue for kombu
    def Queue(*args, **kwargs):
        return {}

    Celery = MockCelery

# ── Build the Celery instance ───────────────────────────────────────────
_broker_url = settings.REDIS_URL or "redis://localhost:6379/0"
_backend_url = settings.REDIS_URL or "redis://localhost:6379/0"

celery_app = Celery(
    "contentstudio",
    broker=_broker_url,
    backend=_backend_url,
    include=[
        "app.tasks.post_tasks",
        "app.tasks.ai_tasks",
        "app.tasks.media_tasks",
        "app.tasks.campaign_tasks",
        "app.tasks.email_tasks",
        "app.tasks.text_generation_tasks",
        "app.tasks.viral_intel_tasks",
    ],
)

# ── Configuration ───────────────────────────────────────────────────────
celery_app.conf.update(
    # Serialization — JSON everywhere for safety & debuggability
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    event_serializer="json",

    # Time-zone
    timezone="UTC",
    enable_utc=True,

    # Reliability
    task_acks_late=True,              # Ack AFTER task completes, not before
    worker_prefetch_multiplier=1,     # Don't prefetch — tasks are slow AI calls
    task_track_started=True,          # STARTED state stored in backend
    task_reject_on_worker_lost=True,  # Re-queue if worker dies mid-task

    # Results
    result_expires=7200,              # Keep results for 2 hours
    result_persistent=True,

    # Queues — default queue for everything
    task_default_queue="default",
    task_queues=[
        Queue("default"),
        Queue("posts"),   # dedicated queue for scheduled posts
        Queue("ai"),      # dedicated queue for heavy AI tasks
    ],

    # Retry broker connection on startup (important in Docker where Redis may
    # start slow)
    broker_connection_retry_on_startup=True,
    broker_connection_max_retries=10,

    # SYNCHRONOUS FALLBACK: run tasks inline when Redis is not available.
    # Covers two cases:
    #   1. ENV=development  — no broker needed, tasks run inline for easy debugging
    #   2. REDIS_URL not set — production deploy without Redis yet; tasks still work,
    #      just synchronously (no background offload). Prevents OperationalError 500s
    #      on all apply_async() call-sites that have no REDIS_URL guard of their own.
    task_always_eager=(settings.ENV == "development" or not settings.REDIS_URL),
    task_eager_propagates=(settings.ENV == "development" or not settings.REDIS_URL),

    # Beat schedule — safety-net periodic tasks
    beat_schedule={
        "publish-pending-posts": {
            "task": "app.tasks.post_tasks.check_and_publish_pending_posts",
            "schedule": 60.0,   # every 60 seconds
            "options": {"queue": "posts"},
        },
    },
)
