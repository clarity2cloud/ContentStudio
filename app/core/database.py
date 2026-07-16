"""
database.py — Appwrite database client (migrated from Supabase).

Exports the same names used throughout the codebase so no router imports break:
  db          — AppwriteClient instance (has .table() method)
  db_table    — same as db (backward compat)
  db_auth     — same as db (no separate auth client needed)
  db_admin    — same as db (no separate admin client needed)
  get_db()    — FastAPI dependency returning AppwriteClient
"""

from app.db.appwrite_client import AppwriteClient, get_appwrite_client
from app.utils.logger import logger


class Database:
    """Thin startup/shutdown wrapper used by main.py."""

    def connect(self):
        logger.info("Appwrite database client ready")

    def get_client(self) -> AppwriteClient:
        return get_appwrite_client()


db = Database()

# Shared Appwrite client — all routers use this via get_db()
_appwrite = get_appwrite_client()

db_table = _appwrite
db_auth = _appwrite
db_admin = _appwrite


def get_db() -> AppwriteClient:
    """FastAPI dependency — returns the Appwrite database client."""
    return _appwrite
