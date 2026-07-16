# app/utils/encryption.py
#
# Fernet symmetric encryption for sensitive fields stored in Appwrite.
# Used for: social_accounts.access_token, social_accounts.refresh_token
#
# Key derivation: ENCRYPTION_KEY env var (must be 32 url-safe base64 bytes).
# If not set, falls back to deriving one from SECRET_KEY so existing deployments
# keep working without a new env var.

import base64
import hashlib
from typing import Optional
from cryptography.fernet import Fernet, InvalidToken


def _build_fernet() -> Fernet:
    from app.config import settings
    raw = getattr(settings, "ENCRYPTION_KEY", "") or ""
    if raw:
        key = raw.encode() if isinstance(raw, str) else raw
    else:
        # Derive 32-byte key from SECRET_KEY using SHA-256
        digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


_fernet: Optional[Fernet] = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = _build_fernet()
    return _fernet


def encrypt(plaintext: str) -> str:
    """Encrypt a string. Returns a base64-encoded ciphertext string."""
    if not plaintext:
        return plaintext
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a previously encrypted string. Returns plaintext.
    Returns the original value unchanged if it was never encrypted
    (handles migration of unencrypted legacy tokens).
    """
    if not ciphertext:
        return ciphertext
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, Exception):
        # Not encrypted (legacy plain token) — return as-is
        return ciphertext
