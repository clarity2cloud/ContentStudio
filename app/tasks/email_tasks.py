"""
Background tasks for email sending.

Email sending is dispatched to Celery queue 'default' with automatic retry
logic (3× attempts with exponential backoff). This decouples email delivery
from request handling, preventing transient email API failures from breaking
user-facing operations.

Each task:
- Sends email via Resend API
- Auto-retries on transient failures (up to 3 attempts)
- Logs failures for monitoring
"""

from typing import Optional, Dict, Any

from app.celery_app import celery_app
from app.utils.logger import logger

# Import resend — available when RESEND_API_KEY is configured
try:
    import resend
except ImportError:
    resend = None


@celery_app.task(
    bind=True,
    queue="default",
    time_limit=30,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3},
    default_retry_delay=60,
)
def send_email_background(
    self,
    to: str,
    subject: str,
    html_content: str,
    from_addr: Optional[str] = None,
    reply_to: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> dict:
    """
    Background task for sending emails via Resend API.

    Automatically retries on transient failures (3× attempts, 60s+ backoff).
    Logs all attempts for monitoring.

    Args:
        to: Recipient email address
        subject: Email subject line
        html_content: Email body (HTML)
        from_addr: Sender address (defaults to MAIL_FROM env var)
        reply_to: Reply-to address (optional)
        headers: Custom email headers (optional)
        metadata: Email metadata for tracking (optional)

    Returns:
        dict: {
            "success": bool,
            "message_id": str | None,
            "error": str | None,
            "attempt": int,
        }
    """
    if not resend:
        logger.error("[EMAIL_BG] Resend library not available")
        return {
            "success": False,
            "message_id": None,
            "error": "Resend library not available",
            "attempt": self.request.retries,
        }

    try:
        logger.info(
            f"[EMAIL_BG] Sending email: to={to}, subject={subject[:50]}..., "
            f"attempt={self.request.retries + 1}"
        )

        # Build email payload
        payload = {
            "to": to,
            "subject": subject,
            "html": html_content,
        }

        if from_addr:
            payload["from"] = from_addr
        if reply_to:
            payload["reply_to"] = reply_to
        if headers:
            payload["headers"] = headers
        if metadata:
            payload["metadata"] = metadata

        # Send via Resend
        response = resend.emails.send(**payload)

        if isinstance(response, dict) and response.get("id"):
            logger.info(
                f"[EMAIL_BG] Email sent successfully: message_id={response['id']}, to={to}")
            return {
                "success": True,
                "message_id": response.get("id"),
                "error": None,
                "attempt": self.request.retries + 1,
            }
        else:
            # Transient error — will retry
            error_msg = response.get(
                "message", str(response)) if isinstance(
                response, dict) else str(response)
            logger.warning(
                f"[EMAIL_BG] Email send failed (will retry): {error_msg}, to={to}")
            raise Exception(f"Resend API returned error: {error_msg}")

    except Exception as exc:
        attempt = self.request.retries + 1
        logger.warning(
            f"[EMAIL_BG] Email send failed (attempt {attempt}/4): {exc}, to={to}")

        # Let Celery handle retry logic
        # If we've hit max retries, the task will be marked as failed
        if self.request.retries >= self.max_retries:
            logger.error(
                f"[EMAIL_BG] Email send dead-lettered after {self.max_retries + 1} attempts: to={to}, "
                f"subject={subject[:50]}, error={exc}")
            return {
                "success": False,
                "message_id": None,
                "error": f"Failed after {self.max_retries + 1} attempts: {str(exc)}",
                "attempt": self.request.retries + 1,
            }

        # Raise to trigger Celery retry
        raise exc


# ─────────────────────────────────────────────────────────────────────
# Convenience wrapper tasks for specific email types (optional, for clarity)
# ─────────────────────────────────────────────────────────────────────


@celery_app.task(
    bind=True,
    queue="default",
    time_limit=30,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3},
    default_retry_delay=60,
)
def send_auth_email(
    self,
    to: str,
    email_type: str,  # 'registration', 'password_reset', 'email_verification'
    data: Dict[str, Any],  # {'token': '...', 'link': '...', etc}
) -> dict:
    """
    Convenience task for sending authentication emails.

    Args:
        to: Recipient email
        email_type: Type of auth email ('registration', 'password_reset', 'email_verification')
        data: Email data dict with template variables

    Returns:
        dict: Same as send_email_background
    """
    # Template building would go here — for now, delegate to send_email_background
    # In production, you'd template this properly with Resend email templates

    subject_map = {
        "registration": "Welcome to ContentStudio AI!",
        "password_reset": "Reset Your ContentStudio Password",
        "email_verification": "Verify Your Email Address",
    }

    subject = subject_map.get(email_type, "ContentStudio")
    html_content = f"<p>Hi,</p><p>Email type: {email_type}</p><p>Data: {data}</p>"

    task = send_email_background.apply_async(
        args=(to, subject, html_content),
        queue="default",
    )
    # AsyncResult is not JSON-serialisable — return a plain dict instead
    return {"task_id": task.id, "status": "queued"}


@celery_app.task(
    bind=True,
    queue="default",
    time_limit=30,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3},
    default_retry_delay=60,
)
def send_notification_email(
    self,
    to: str,
    subject: str,
    html_content: str,
) -> dict:
    """
    Convenience task for sending notification emails (campaign, scheduling, alerts, etc).

    Args:
        to: Recipient email
        subject: Email subject
        html_content: Email body (HTML)

    Returns:
        dict: Same as send_email_background
    """
    task = send_email_background.apply_async(
        args=(to, subject, html_content),
        queue="default",
    )
    # AsyncResult is not JSON-serialisable — return a plain dict instead
    return {"task_id": task.id, "status": "queued"}
