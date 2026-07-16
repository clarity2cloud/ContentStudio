import resend
from typing import Optional, Dict, Any
import random
from datetime import datetime, timedelta, timezone
from app.config import settings
from app.utils.logger import logger
from app.core.database import db_table  # AppwriteClient instance


class EmailService:
    def __init__(self):
        """Initialize Resend email service"""
        self.enabled = False

        if settings.RESEND_API_KEY:
            try:
                resend.api_key = settings.RESEND_API_KEY
                self.enabled = True
                logger.info(
                    "[OK] Resend email service initialized (PRODUCTION MODE)")
            except Exception as e:
                logger.error(f"[FAIL] Resend initialization failed: {str(e)}")
        else:
            logger.error(
                "[FAIL] RESEND_API_KEY missing! Emails will NOT be sent.")

    # ---------------- OTP GENERATION ---------------- #

    def generate_otp(self, length: int = 6) -> str:
        return ''.join(str(random.randint(0, 9)) for _ in range(length))

    # ---------------- DATABASE ---------------- #

    def store_otp(
        self,
        email: str,
        otp: str,
        expires_in_minutes: int = 10,
        signup_data: Optional[Dict[str, Any]] = None
    ):
        try:
            expires_at = datetime.now(timezone.utc) + \
                timedelta(minutes=expires_in_minutes)

            client = db_table

            # DELETE any existing row first, then INSERT fresh.
            # This avoids relying on ON CONFLICT constraint names (which differ across
            # Supabase projects) while still being safe for the
            # single-row-per-email pattern.
            client.table("verification_codes").delete().eq(
                "email", email).execute()

            import json as _json
            client.table("verification_codes").insert({
                "email": email,
                "otp": str(otp).strip(),
                # Appwrite collection requires this field
                "code": str(otp).strip(),
                "expires_at": expires_at.isoformat(),
                "is_used": False,
                "signup_data": _json.dumps(signup_data) if isinstance(signup_data, dict) else (signup_data or ""),
            }).execute()

            logger.info(f"[OK] OTP stored for {email}")

        except Exception as e:
            logger.error(f"[FAIL] Failed to store OTP: {str(e)}")
            raise

    def verify_otp(self, email: str, otp: str) -> bool:
        try:
            client = db_table
            result = client.table("verification_codes").select(
                "*").eq("email", email).execute()

            if not result.data:
                logger.warning(f"OTP not found for {email}")
                return False

            stored = result.data[0]
            raw_ts = stored.get("expires_at", "")
            if isinstance(raw_ts, str):
                expires_at = datetime.fromisoformat(
                    raw_ts.replace('Z', '+00:00'))
            else:
                expires_at = raw_ts  # already a datetime

            if datetime.now(timezone.utc) > expires_at:
                logger.warning(f"OTP expired for {email}")
                self.clear_otp(email)
                return False

            # Strip whitespace to handle copy-paste artifacts
            if str(stored["otp"]).strip() != str(otp).strip():
                logger.warning(f"OTP mismatch for {email}")
                return False

            logger.info(f"[OK] OTP verified for {email}")
            return True

        except Exception as e:
            logger.error(f"[FAIL] OTP verification error: {str(e)}")
            return False

    def get_stored_otp(self, email: str) -> Optional[Dict[str, Any]]:
        """Retrieve stored OTP data including signup_data"""
        try:
            client = db_table
            result = client.table("verification_codes").select(
                "*").eq("email", email).execute()

            if not result.data:
                logger.warning(f"[WARN] No OTP record found in DB for {email}")
                return None

            stored = result.data[0]

            # Convert expires_at string → timezone-aware datetime
            raw_ts = stored.get("expires_at")
            if raw_ts and isinstance(raw_ts, str):
                stored["expires_at"] = datetime.fromisoformat(
                    raw_ts.replace('Z', '+00:00'))

            # Parse signup_data from JSON string (Appwrite stores it as string)
            import json as _json
            sd = stored.get("signup_data")
            if sd and isinstance(sd, str):
                try:
                    stored["signup_data"] = _json.loads(sd)
                except Exception:
                    pass

            return stored

        except Exception as e:
            logger.error(
                f"[FAIL] Error retrieving stored OTP for {email}: {str(e)}")
            # Re-raise so callers get a real 500 error, not a misleading "No
            # pending signup"
            raise

    def clear_otp(self, email: str):
        try:
            db_table.table("verification_codes").delete().eq(
                "email", email).execute()
        except Exception as e:
            logger.error(f"Failed to clear OTP: {str(e)}")

    # ---------------- EMAIL ---------------- #

    async def send_otp_email(
            self,
            email: str,
            otp: str,
            purpose: str = "signup") -> bool:
        """
        Send OTP email via Celery background task (non-blocking).

        Dispatches email to queue instead of blocking on Resend API.
        Returns True immediately; actual delivery happens asynchronously.
        """

        if not self.enabled:
            logger.error("Email service disabled. Check RESEND_API_KEY.")
            return False

        try:
            # Dispatch to background task
            from app.tasks.email_tasks import send_email_background

            subject_map = {
                "signup": "Verify your email - ContentStudio AI",
                "forgot_password": "Password Reset OTP"
            }

            subject = subject_map.get(purpose, "Your OTP Code")
            html_content = self._get_email_template(otp)

            # Dispatch to Celery (non-blocking)
            task = send_email_background.apply_async(
                args=(email, subject, html_content),
                kwargs={"from_addr": settings.MAIL_FROM},
                queue="default",
            )

            logger.info(
                f"[OK] OTP email queued for {email} (task_id={task.id})"
            )
            return True  # Return True immediately — email will be delivered asynchronously

        except Exception as e:
            logger.error(f"[FAIL] Failed to queue OTP email: {str(e)}")
            return False

    async def send_welcome_email(self, email: str, full_name: str) -> bool:
        """
        Send welcome email via Celery background task (non-blocking).

        Dispatches email to queue instead of blocking on Resend API.
        Returns True immediately; actual delivery happens asynchronously.
        """
        if not self.enabled:
            return False

        try:
            # Dispatch to background task
            from app.tasks.email_tasks import send_email_background

            subject = "Welcome to ContentStudio AI"
            html_content = self._get_welcome_template(full_name)

            # Dispatch to Celery (non-blocking)
            task = send_email_background.apply_async(
                args=(email, subject, html_content),
                kwargs={"from_addr": settings.MAIL_FROM},
                queue="default",
            )

            logger.info(
                f"[OK] Welcome email queued for {email} (task_id={task.id})"
            )
            return True  # Return True immediately — email will be delivered asynchronously

        except Exception as e:
            logger.error(f"Welcome email queuing failed: {str(e)}")
            return False

    # ---------------- TEMPLATES ---------------- #

    def _get_email_template(self, otp: str) -> str:
        return """
        <div style="font-family: Arial, sans-serif; max-width:600px; margin:auto; border:1px solid #e0e0e0; border-radius:8px; padding:20px;">
            <h2 style="color:#333;">Email Verification</h2>
            <p style="font-size:16px; color:#555;">Your OTP code is:</p>
            <div style="font-size:32px; font-weight:bold; letter-spacing:6px; background:#f4f4f4; padding:10px; text-align:center; border-radius:5px;">
                {otp}
            </div>
            <p style="font-size:14px; color:#777;">This code expires in 10 minutes.</p>
            <hr style="border:0; border-top:1px solid #eee; margin:20px 0;">
            <p style="font-size:14px; color:#999;">Best regards,<br>© 2026 Clarity2Cloud Technology Pvt. Ltd.</p>

        </div>
        """

    def _get_welcome_template(self, full_name: str) -> str:
        return """
        <div style="font-family: Arial, sans-serif; max-width:600px; margin:auto; border:1px solid #e0e0e0; border-radius:8px; padding:20px;">
            <h2 style="color:#333;">Welcome {full_name}!</h2>
            <p style="font-size:16px; color:#555;">Your account is ready. Start creating and scheduling your content with ContentStudio AI.</p>
            <hr style="border:0; border-top:1px solid #eee; margin:20px 0;">
            <p style="font-size:14px; color:#999;">Best regards,<br>Clarity2Cloud Technology</p>
        </div>
        """


# Global instance
email_service = EmailService()

# Backward compatibility wrappers


def generate_otp(length: int = 6) -> str:
    return email_service.generate_otp(length)


def store_otp(email: str,
              otp: str,
              signup_data: Optional[Dict[str,
                                         Any]] = None,
              expires_minutes: int = 10):
    return email_service.store_otp(
        email,
        otp,
        expires_in_minutes=expires_minutes,
        signup_data=signup_data)


def verify_otp(email: str, otp: str) -> bool:
    return email_service.verify_otp(email, otp)


def get_stored_otp(email: str) -> Optional[Dict[str, Any]]:
    return email_service.get_stored_otp(email)


def clear_otp(email: str):
    return email_service.clear_otp(email)


def store_pending_signup(email: str, otp: str, signup_data: Dict[str, Any]):
    return email_service.store_otp(email, otp, signup_data=signup_data)


async def send_otp_email(
        email: str,
        otp: str,
        purpose: str = "signup") -> bool:
    return await email_service.send_otp_email(email, otp, purpose)


async def send_welcome_email(email: str, full_name: str) -> bool:
    return await email_service.send_welcome_email(email, full_name)
