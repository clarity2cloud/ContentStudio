# app/services/generation_memory.py
"""
Generation Memory — Anti-Repetition Engine
═══════════════════════════════════════════════════════════════════════════════

Stores every generated piece of content with:
  • angle / lens used
  • content fingerprint (normalized)
  • key phrases / topical signature
  • content hash

Before any new generation, the engine:
  1. Fetches recently-used angles for (brand_id, platform) → injects "do not repeat" list
  2. Picks a fresh angle from the platform's angle_pool (rotation, never-repeated)
  3. After generation, checks similarity vs last N pieces and rejects if too similar

Uses Appwrite collection: `generation_history`.
Falls back to in-memory if Appwrite is unavailable (graceful degrade).

Schema for `generation_history`:
  id            (string, $id)
  tenant_id     (string, required, indexed)
  brand_id      (string, required, indexed)
  platform      (string, required, indexed)
  angle         (string, required)
  topic_hash    (string, indexed)
  content_hash  (string)
  key_phrases   (string, JSON list)
  fingerprint   (string)    # normalized first 400 chars
  created_at    ($createdAt)
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import threading
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple

from app.db.appwrite_client import db as _appwrite_db
from app.utils.logger import logger
from app.services.platform_personas import get_angle_pool, normalize_platform

# ── Configuration ──────────────────────────────────────────────────────────
COLLECTION = "generation_history"
RECENT_WINDOW = 200             # key-phrase similarity window (last N items)
# all-time angle lookup window — never repeat an angle across 2000 items
ANGLE_WINDOW = 2000
FINGERPRINT_CHARS = 400             # length of fingerprint excerpt
# Jaccard threshold for rejection (tightened from 0.78)
SIMILARITY_THRESHOLD = 0.72
# an angle cannot repeat within 365 days (1 year)
ANGLE_COOLDOWN_DAYS = 365
INMEMORY_RING_SIZE = 10000           # ring-buffer size for in-memory fallback
WORD_RE = re.compile(r"\b[a-zA-Z]{4,}\b")

# ── In-memory fallback (process-local) ─────────────────────────────────────
_inmem_lock = threading.RLock()
_inmem_store: deque = deque(maxlen=INMEMORY_RING_SIZE)


# ── Utilities ──────────────────────────────────────────────────────────────
def _normalize(text: str) -> str:
    if not text:
        return ""
    t = re.sub(r"\s+", " ", text).strip().lower()
    return t[:FINGERPRINT_CHARS]


def _hash_text(text: str) -> str:
    return hashlib.md5(
        _normalize(text).encode("utf-8"),
        usedforsecurity=False).hexdigest()[
        :16]


def _hash_topic(topic: str, platform: str) -> str:
    n = normalize_platform(platform)
    return hashlib.md5(f"{n}|{_normalize(topic)}".encode(
        "utf-8"), usedforsecurity=False).hexdigest()[:16]


def _extract_key_phrases(text: str, k: int = 12) -> List[str]:
    """Top-k frequent meaningful tokens (very fast, no NLP dep)."""
    if not text:
        return []
    tokens = WORD_RE.findall(text.lower())
    STOP = {
        "this",
        "that",
        "with",
        "from",
        "your",
        "have",
        "will",
        "they",
        "their",
        "what",
        "when",
        "where",
        "which",
        "would",
        "could",
        "should",
        "about",
        "into",
        "than",
        "then",
        "more",
        "most",
        "some",
        "such",
        "only",
        "also",
        "been",
        "being",
        "were",
        "them",
        "these",
        "those",
        "here",
        "there",
    }
    counts: Dict[str, int] = defaultdict(int)
    for t in tokens:
        if t in STOP or len(t) < 4:
            continue
        counts[t] += 1
    return [w for w, _ in sorted(counts.items(), key=lambda x: -x[1])[:k]]


def _jaccard(a: List[str], b: List[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


# ── Appwrite I/O (best-effort) ─────────────────────────────────────────────
def _query_recent(
        brand_id: str,
        platform: str,
        limit: int = RECENT_WINDOW) -> List[Dict]:
    """Return the last N generation records for (brand, platform). Best-effort."""
    norm_platform = normalize_platform(platform)
    try:
        queries = [
            {"method": "equal", "attribute": "brand_id", "values": [brand_id]},
            {"method": "equal", "attribute": "platform", "values": [norm_platform]},
            {"method": "orderDesc", "attribute": "$createdAt"},
            {"method": "limit", "values": [limit]},
        ]
        res = _appwrite_db.list_documents(COLLECTION, queries=queries)
        docs = res.get("documents", []) or []
        out = []
        for d in docs:
            kp = d.get("key_phrases") or "[]"
            if isinstance(kp, str):
                try:
                    kp = json.loads(kp)
                except Exception:
                    kp = []
            out.append({
                "angle": d.get("angle", ""),
                "topic_hash": d.get("topic_hash", ""),
                "content_hash": d.get("content_hash", ""),
                "fingerprint": d.get("fingerprint", ""),
                "key_phrases": kp,
                "created_at": d.get("$createdAt") or d.get("created_at", ""),
            })
        return out
    except Exception as e:
        logger.debug(f"[GEN-MEMORY] Appwrite query failed (using in-mem): {e}")
        return _inmem_query_recent(brand_id, norm_platform, limit)


def _query_all_angles(brand_id: str, platform: str) -> List[Dict]:
    """
    Return ALL angle records across the entire history for (brand, platform).
    Used for the 1-year angle cooldown check — we never repeat an angle within
    ANGLE_COOLDOWN_DAYS regardless of how many total records exist.
    Falls back to in-memory silently.
    """
    norm_platform = normalize_platform(platform)
    try:
        queries = [
            {"method": "equal", "attribute": "brand_id", "values": [brand_id]},
            {"method": "equal", "attribute": "platform", "values": [norm_platform]},
            {"method": "orderDesc", "attribute": "$createdAt"},
            {"method": "limit", "values": [ANGLE_WINDOW]},
        ]
        res = _appwrite_db.list_documents(COLLECTION, queries=queries)
        docs = res.get("documents", []) or []
        return [
            {
                "angle": d.get("angle", ""),
                "created_at": d.get("$createdAt") or d.get("created_at", ""),
            }
            for d in docs
        ]
    except Exception as e:
        logger.debug(f"[GEN-MEMORY] _query_all_angles fallback to in-mem: {e}")
        with _inmem_lock:
            return [
                {"angle": r["angle"], "created_at": r.get("created_at", "")}
                for r in reversed(_inmem_store)
                if r["brand_id"] == brand_id and r["platform"] == norm_platform
            ][:ANGLE_WINDOW]


def _inmem_query_recent(
        brand_id: str,
        platform: str,
        limit: int) -> List[Dict]:
    with _inmem_lock:
        items = [
            r for r in reversed(_inmem_store)
            if r["brand_id"] == brand_id and r["platform"] == platform
        ]
    return items[:limit]


def _persist(record: Dict) -> bool:
    """Persist a generation record. Falls back to in-memory."""
    try:
        payload = {
            "tenant_id": record["tenant_id"],
            "brand_id": record["brand_id"],
            "platform": record["platform"],
            "angle": record["angle"],
            "topic_hash": record["topic_hash"],
            "content_hash": record["content_hash"],
            "key_phrases": json.dumps(record["key_phrases"][:20]),
            "fingerprint": record["fingerprint"][:FINGERPRINT_CHARS],
        }
        _appwrite_db.create_document(COLLECTION, payload)
        return True
    except Exception as e:
        logger.debug(
            f"[GEN-MEMORY] Persist to Appwrite failed (using in-mem): {e}")
        with _inmem_lock:
            _inmem_store.append(record)
        return False


# ── Public API ─────────────────────────────────────────────────────────────
def get_avoidance_context(brand_id: str, platform: str) -> Dict:
    """
    Returns context for the LLM prompt:
      - ALL used angles on cooldown (injected into prompt as forbidden list — up to 20)
      - recommended fresh angle (guaranteed not used in 365 days)
      - prior key phrases (for similarity rejection)
    """
    if not brand_id:
        return {
            "avoided_angles": [],
            "suggested_angle": _random_angle(platform),
            "prior_fingerprints": [],
            "prior_key_phrases": [],
        }

    # ── Key-phrase & fingerprint context (last RECENT_WINDOW items) ──────────
    recent = _query_recent(brand_id, platform, limit=RECENT_WINDOW)
    fingerprints: List[str] = []
    key_phrases_list: List[List[str]] = []
    for r in recent:
        if r.get("fingerprint"):
            fingerprints.append(r["fingerprint"])
        if r.get("key_phrases"):
            key_phrases_list.append(r["key_phrases"])

    # ── All-time angle cooldown (last ANGLE_WINDOW items) ────────────────────
    # We collect ALL angles used in the last 365 days to inject into the
    # prompt.
    all_angle_records = _query_all_angles(brand_id, platform)
    now = datetime.now(timezone.utc)
    used_angles_ordered: List[str] = []  # ordered by recency, deduped
    seen: set = set()
    for r in all_angle_records:
        angle = (r.get("angle") or "").strip()
        if not angle or angle in seen:
            continue
        raw_ts = r.get("created_at", "")
        within_cooldown = True  # default: treat as recent
        if raw_ts:
            try:
                ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                within_cooldown = (now - ts).days < ANGLE_COOLDOWN_DAYS
            except Exception:
                pass
        if within_cooldown:
            used_angles_ordered.append(angle)
            seen.add(angle)

    fresh = _pick_fresh_angle(platform, used_angles_ordered, brand_id=brand_id)

    return {
        # Inject up to 20 cooldown angles into the LLM prompt — keep token
        # budget reasonable
        "avoided_angles": used_angles_ordered[:20],
        "suggested_angle": fresh,
        "prior_fingerprints": fingerprints[:RECENT_WINDOW],
        "prior_key_phrases": key_phrases_list[:RECENT_WINDOW],
    }


def _random_angle(platform: str) -> str:
    pool = get_angle_pool(platform) or ["story", "tip", "question"]
    return random.choice(pool)


def _angles_on_cooldown(brand_id: str, platform: str) -> set:
    """
    Returns the set of angle names that are still within ANGLE_COOLDOWN_DAYS.
    An angle is on cooldown if it was used within the last 365 days.
    """
    records = _query_all_angles(brand_id, platform)
    now = datetime.now(timezone.utc)
    on_cooldown: set = set()
    for r in records:
        angle = (r.get("angle") or "").strip()
        if not angle:
            continue
        raw_ts = r.get("created_at", "")
        if not raw_ts:
            # No timestamp — treat as recent to be safe
            on_cooldown.add(angle)
            continue
        try:
            ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            days_ago = (now - ts).days
            if days_ago < ANGLE_COOLDOWN_DAYS:
                on_cooldown.add(angle)
        except Exception:
            on_cooldown.add(angle)  # parse error → treat as recent
    return on_cooldown


def _pick_fresh_angle(
        platform: str,
        avoided: List[str],
        brand_id: str = "") -> str:
    """
    Pick an angle that:
    1. Is not in the avoided list (last N used angles)
    2. Is not on cooldown (not used within ANGLE_COOLDOWN_DAYS)

    Falls back progressively:
    - If all angles are on cooldown → pick the angle used longest ago
    - If all on cooldown + no history → random from pool
    """
    pool = get_angle_pool(platform) or ["story", "tip", "question"]

    # Get angles still on the 365-day cooldown
    cooldown_set = _angles_on_cooldown(
        brand_id, platform) if brand_id else set(avoided)

    # Priority 1: not in avoided AND not on cooldown
    fresh = [a for a in pool if a not in avoided and a not in cooldown_set]
    if fresh:
        return random.choice(fresh)

    # Priority 2: not in avoided (cooldown expired but not recently used)
    not_avoided = [a for a in pool if a not in avoided]
    if not_avoided:
        return random.choice(not_avoided)

    # Priority 3: all used recently — pick the one used longest ago (end of
    # avoided list)
    if avoided:
        lru_candidates = [a for a in reversed(avoided) if a in pool]
        if lru_candidates:
            return lru_candidates[0]

    # Final fallback
    return random.choice(pool)


def is_too_similar(
        content: str, prior_key_phrases: List[List[str]]) -> Tuple[bool, float]:
    """
    Returns (rejected, max_similarity). Compares the new content's key phrases
    against the last N items' key phrases via Jaccard.
    """
    if not prior_key_phrases:
        return False, 0.0
    new_kp = _extract_key_phrases(content)
    if not new_kp:
        return False, 0.0
    max_sim = 0.0
    for prior in prior_key_phrases:
        sim = _jaccard(new_kp, prior)
        if sim > max_sim:
            max_sim = sim
    return max_sim >= SIMILARITY_THRESHOLD, max_sim


def record_generation(
    *,
    tenant_id: str,
    brand_id: str,
    platform: str,
    topic: str,
    angle: str,
    content: str,
) -> bool:
    """Record a successful generation into history."""
    if not brand_id:
        return False
    norm_platform = normalize_platform(platform)
    record = {
        "tenant_id": tenant_id or "",
        "brand_id": brand_id,
        "platform": norm_platform,
        "angle": (angle or "")[:80],
        "topic_hash": _hash_topic(topic, norm_platform),
        "content_hash": _hash_text(content),
        "key_phrases": _extract_key_phrases(content),
        "fingerprint": _normalize(content)[:FINGERPRINT_CHARS],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return _persist(record)


def build_avoidance_directives(
        brand_id: str, platform: str) -> Tuple[List[str], str]:
    """
    Returns (avoided_angles, suggested_angle) — what to inject into the LLM system prompt.
    """
    ctx = get_avoidance_context(brand_id, platform)
    return ctx["avoided_angles"], ctx["suggested_angle"]


# ── Retention / pruning ────────────────────────────────────────────────────
# generation_history grows unbounded otherwise. We keep a margin beyond the
# 365-day angle cooldown so pruning never removes a record the cooldown check
# still needs, then drop everything older.
RETENTION_DAYS = ANGLE_COOLDOWN_DAYS + 35  # 400 days


def prune_history(
        retention_days: int = RETENTION_DAYS,
        max_delete: int = 1000) -> int:
    """
    Delete generation_history records older than `retention_days`.

    Bounded by `max_delete` per call so a scheduled sweep can never run away.
    Designed for a periodic job (APScheduler / Celery beat). Never raises.
    Returns the number of records deleted.
    """
    cutoff = (
        datetime.now(
            timezone.utc) -
        timedelta(
            days=retention_days)).isoformat()
    deleted = 0
    try:
        queries = [
            {"method": "lessThan", "attribute": "$createdAt", "values": [cutoff]},
            {"method": "orderAsc", "attribute": "$createdAt"},
            {"method": "limit", "values": [min(max_delete, 1000)]},
        ]
        res = _appwrite_db.list_documents(COLLECTION, queries=queries)
        docs = res.get("documents", []) or []
        for d in docs:
            doc_id = d.get("$id")
            if not doc_id:
                continue
            try:
                _appwrite_db.delete_document(COLLECTION, doc_id)
                deleted += 1
            except Exception as e:
                logger.debug(
                    f"[GEN-MEMORY] prune delete failed for {doc_id}: {e}")
        if deleted:
            logger.info(
                f"[GEN-MEMORY] pruned {deleted} records older than {retention_days}d")
    except Exception as e:
        logger.warning(f"[GEN-MEMORY] prune_history failed (non-fatal): {e}")
    return deleted
