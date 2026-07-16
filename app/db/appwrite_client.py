# app/db/appwrite_client.py
"""
Appwrite REST-based database client.

Uses requests directly to avoid Appwrite SDK v17 pydantic bug with $sequence field.
Exposes a Supabase-compatible .table() interface so existing router code needs
only minimal changes (get_current_user_id auth method).

Connection pooling: a shared requests.Session with HTTPAdapter (pool_connections=4,
pool_maxsize=20) is used for all requests, reducing TCP handshake overhead.
"""

import os
import json
import uuid
import re as _re
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from app.utils.logger import logger

# Matches bare ISO datetime strings without milliseconds:
# "2026-05-01T00:00:00+00:00"  or  "2026-05-01T00:00:00Z"
_ISO_NO_MS = _re.compile(
    r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(Z|[+-]\d{2}:\d{2})$'
)

# ── Configuration ───────────────────────────────────────────────────────
APPWRITE_ENDPOINT = os.getenv("APPWRITE_ENDPOINT", "https://db.thq.digital/v1")
APPWRITE_PROJECT_ID = os.getenv("APPWRITE_PROJECT_ID", "")
APPWRITE_API_KEY = os.getenv("APPWRITE_API_KEY", "")
DATABASE_ID = "database-contentstudio"

# ── Shared HTTP session with connection pooling ─────────────────────────
# pool_connections : number of host connection pools kept alive
# pool_maxsize     : max connections per pool (reused across requests)
# Retry on transient 5xx / connection errors (max 2 retries, no redirect retry)
_retry = Retry(
    total=2,
    backoff_factor=0.3,
    status_forcelist=(500, 502, 503, 504),
    allowed_methods=["GET", "POST", "PATCH", "DELETE"],
    raise_on_status=False,
)
_adapter = HTTPAdapter(
    pool_connections=4,
    pool_maxsize=20,
    max_retries=_retry,
)
_session = requests.Session()
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)


# ── Response wrapper (mimics Supabase response) ─────────────────────────
class QueryResult:
    def __init__(self, data: List[Dict], count: int = 0):
        self.data = data
        self.count = count


# ── In-memory fallback store ─────────────────────────────────────────────
# Shared by TableQuery and AppwriteDB below. Used whenever Appwrite is
# unreachable/unconfigured, so a document created in one request (e.g. a
# campaign) is still there for the next request in the same process — not
# just a one-off fake response. This is what lets the whole app (brands,
# campaigns, content, generation) work end-to-end without a database
# configured; persistence is just process-local instead of durable.
_mock_docs: Dict[str, Dict[str, Dict]] = {}


def _mock_get(collection_id: str, doc_id: str) -> Optional[Dict]:
    return _mock_docs.get(collection_id, {}).get(doc_id)


def _mock_put(collection_id: str, doc_id: str, data: Dict) -> Dict:
    existing = _mock_docs.get(collection_id, {}).get(doc_id, {})
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "$id": doc_id,
        "$createdAt": existing.get("$createdAt") or now,
        "$updatedAt": now,
        **{k: v for k, v in existing.items() if k not in ("$id", "$createdAt", "$updatedAt")},
        **data,
    }
    _mock_docs.setdefault(collection_id, {})[doc_id] = doc
    return doc


def _mock_matches(doc: Dict, queries: List[dict]) -> bool:
    """Apply a list of Appwrite-style query filters to a mock document."""
    for q in queries:
        method = q.get("method")
        attr = q.get("attribute")
        values = q.get("values") or []
        if method in ("orderAsc", "orderDesc"):
            continue
        val = doc.get(attr)
        if method == "equal":
            if val not in values:
                return False
        elif method == "notEqual":
            if val in values:
                return False
        elif method == "search":
            needle = str(values[0]).lower() if values else ""
            if needle not in str(val or "").lower():
                return False
        elif method == "greaterThanEqual":
            if val is None or not values or not (val >= values[0]):
                return False
        elif method == "lessThanEqual":
            if val is None or not values or not (val <= values[0]):
                return False
        elif method == "greaterThan":
            if val is None or not values or not (val > values[0]):
                return False
    return True


def _mock_query(collection_id: str, queries: List[dict],
                limit: Optional[int] = None, offset: int = 0) -> List[Dict]:
    docs = [d for d in _mock_docs.get(collection_id, {}).values()
            if _mock_matches(d, queries)]
    order_field = None
    order_desc = False
    for q in queries:
        if q.get("method") in ("orderAsc", "orderDesc"):
            order_field = q.get("attribute")
            order_desc = q.get("method") == "orderDesc"
            break
    if order_field:
        docs.sort(key=lambda d: d.get(order_field) or "", reverse=order_desc)
    if limit is not None:
        docs = docs[offset:offset + limit]
    return docs


# ── Field normalization ─────────────────────────────────────────────────
def _norm(doc: dict) -> dict:
    """Normalize Appwrite doc fields to match Supabase-style row."""
    d = {}
    for k, v in doc.items():
        if k.startswith("$"):
            continue
        if isinstance(v, str) and len(v) >= 2 and v[0] in (
                "{", "[") and v[-1] in ("}", "]"):
            try:
                v = json.loads(v)
            except Exception:
                pass
        d[k] = v
    d["id"] = doc.get("$id", "")
    d["created_at"] = doc.get("$createdAt", "")
    d["updated_at"] = doc.get("$updatedAt", "")
    return d


# ── Query builder ───────────────────────────────────────────────────────
class TableQuery:
    """Chainable query builder that translates Supabase-style calls to Appwrite REST."""

    def __init__(self, collection_id: str):
        self._col = collection_id
        self._queries: List[dict] = []
        self._limit_val = 100
        self._offset_val = 0
        self._operation = "select"
        self._insert_data: Optional[Dict] = None
        self._update_data: Optional[Dict] = None

    def _headers(self) -> Dict:
        return {
            "X-Appwrite-Project": APPWRITE_PROJECT_ID,
            "X-Appwrite-Key": APPWRITE_API_KEY,
            "Content-Type": "application/json",
        }

    def _base_url(self) -> str:
        return f"{APPWRITE_ENDPOINT}/databases/{DATABASE_ID}/collections/{self._col}/documents"

    def _build_query_params(self) -> List[str]:
        params = []
        for q in self._queries:
            params.append(f'queries[]={json.dumps(q)}')
        params.append(
            f'queries[]={json.dumps({"method": "limit",  "values": [self._limit_val]})}')
        params.append(
            f'queries[]={json.dumps({"method": "offset", "values": [self._offset_val]})}')
        return params

    # ── Filter methods ──────────────────────────────────────────────────────
    def select(self, *args, **kwargs):
        return self

    @staticmethod
    def _field(f: str) -> str:
        _MAP = {
            "id": "$id",
            "created_at": "$createdAt",
            "updated_at": "$updatedAt"}
        return _MAP.get(f, f)

    def eq(self, field: str, value: Any):
        self._queries.append(
            {"method": "equal", "attribute": self._field(field), "values": [value]})
        return self

    def neq(self, field: str, value: Any):
        self._queries.append(
            {"method": "notEqual", "attribute": self._field(field), "values": [value]})
        return self

    def ilike(self, field: str, value: str):
        self._queries.append({"method": "search",
                              "attribute": self._field(field),
                              "values": [value.strip("%")]})
        return self

    def gte(self, field: str, value: Any):
        self._queries.append({"method": "greaterThanEqual",
                             "attribute": self._field(field), "values": [value]})
        return self

    def lte(self, field: str, value: Any):
        self._queries.append(
            {"method": "lessThanEqual", "attribute": self._field(field), "values": [value]})
        return self

    def gt(self, field: str, value: Any):
        self._queries.append(
            {"method": "greaterThan", "attribute": self._field(field), "values": [value]})
        return self

    def order(self, field: str, desc: bool = False):
        method = "orderDesc" if desc else "orderAsc"
        self._queries.append(
            {"method": method, "attribute": self._field(field)})
        return self

    def limit(self, n: int):
        self._limit_val = n
        return self

    def range(self, start: int, end: int):
        self._offset_val = start
        self._limit_val = end - start + 1
        return self

    def insert(self, data: Dict):
        self._operation = "insert"
        self._insert_data = data
        return self

    def update(self, data: Dict):
        self._operation = "update"
        self._update_data = data
        return self

    def delete(self):
        self._operation = "delete"
        return self

    # ── Execute ─────────────────────────────────────────────────────────────
    def execute(self) -> QueryResult:
        try:
            if self._operation == "select":
                return self._exec_select()
            if self._operation == "insert":
                return self._exec_insert()
            if self._operation == "update":
                return self._exec_update()
            if self._operation == "delete":
                return self._exec_delete()
        except Exception as e:
            logger.error(f"DB error [{self._col}.{self._operation}]: {e}")
            raise

    def _exec_select(self) -> QueryResult:
        params = self._build_query_params()
        url = f"{self._base_url()}?{'&'.join(params)}"
        try:
            r = _session.get(url, headers=self._headers(), timeout=5)
            r.raise_for_status()
            data = r.json()
            docs = [_norm(d) for d in data.get("documents", [])]
            return QueryResult(data=docs, count=int(data.get("total", len(docs))))
        except Exception as e:
            # Appwrite unavailable — serve from the in-memory store so
            # documents created earlier in this process are still found
            logger.debug(f"[APPWRITE] SELECT failed ({type(e).__name__}), using in-memory store")
            docs = _mock_query(self._col, self._queries, self._limit_val, self._offset_val)
            return QueryResult(data=[_norm(d) for d in docs], count=len(docs))

    def _exec_insert(self) -> QueryResult:
        raw = dict(self._insert_data)
        doc_id = raw.pop("id", str(uuid.uuid4()).replace("-", "")[:20])
        clean = _serialize(raw)
        payload = {"documentId": str(doc_id), "data": clean}
        try:
            r = _session.post(
                self._base_url(),
                headers=self._headers(),
                json=payload,
                timeout=5)
            if not r.ok:
                try:
                    body = r.json()
                except Exception:
                    body = r.text[:600]
                logger.error(f"Appwrite [{self._col}] insert 400 detail: {body}")
            r.raise_for_status()
            return QueryResult(data=[_norm(r.json())])
        except Exception as e:
            # Appwrite unavailable — persist to the in-memory store so this
            # document is still there for later selects/updates in this process
            logger.debug(f"[APPWRITE] INSERT failed ({type(e).__name__}), using in-memory store")
            mock_doc = _mock_put(self._col, str(doc_id), clean)
            return QueryResult(data=[_norm(mock_doc)])

    def _exec_update(self) -> QueryResult:
        try:
            sel_params = [f'queries[]={json.dumps(q)}' for q in self._queries]
            sel_params.append(
                f'queries[]={json.dumps({"method": "limit", "values": [100]})}')
            url = f"{self._base_url()}?{'&'.join(sel_params)}"
            r = _session.get(url, headers=self._headers(), timeout=5)
            r.raise_for_status()
            docs = r.json().get("documents", [])
            clean = _serialize(self._update_data)

            updated = []
            for doc in docs:
                patch_url = f"{self._base_url()}/{doc['$id']}"
                resp = _session.patch(
                    patch_url, headers=self._headers(), json={
                        "data": clean}, timeout=5)
                if not resp.ok:
                    try:
                        body = resp.json()
                    except Exception:
                        body = resp.text[:600]
                    logger.error(
                        f"Appwrite [{self._col}] update 400 detail: {body}")
                resp.raise_for_status()
                updated.append(_norm(resp.json()))
            return QueryResult(data=updated)
        except Exception as e:
            logger.debug(f"[APPWRITE] UPDATE failed ({type(e).__name__}), using in-memory store")
            clean = _serialize(self._update_data)
            matches = _mock_query(self._col, self._queries)
            updated = [_mock_put(self._col, m["$id"], clean) for m in matches]
            return QueryResult(data=[_norm(d) for d in updated])

    def _exec_delete(self) -> QueryResult:
        try:
            sel_params = [f'queries[]={json.dumps(q)}' for q in self._queries]
            sel_params.append(
                f'queries[]={json.dumps({"method": "limit", "values": [100]})}')
            url = f"{self._base_url()}?{'&'.join(sel_params)}"
            r = _session.get(url, headers=self._headers(), timeout=5)
            r.raise_for_status()
            docs = r.json().get("documents", [])
            for doc in docs:
                _session.delete(
                    f"{self._base_url()}/{doc['$id']}",
                    headers=self._headers(), timeout=5)
            return QueryResult(data=[])
        except Exception as e:
            logger.debug(f"[APPWRITE] DELETE failed ({type(e).__name__}), using in-memory store")
            matches = _mock_query(self._col, self._queries)
            for m in matches:
                _mock_docs.get(self._col, {}).pop(m["$id"], None)
            return QueryResult(data=[])


def _serialize(data: dict) -> dict:
    """Convert Python values to Appwrite-safe types.

    Appwrite datetime fields require milliseconds in the ISO string,
    e.g. "2026-05-01T00:00:00.000+00:00". Python's isoformat() omits
    milliseconds when they are zero, causing a 400 Bad Request. This
    function normalises all datetime values (objects and strings) to
    always include the 3-digit millisecond component.
    """
    clean = {}
    for k, v in (data or {}).items():
        if v is None:
            continue
        if isinstance(v, dict):
            clean[k] = json.dumps(v)
        elif isinstance(v, datetime):
            ms = v.microsecond // 1000
            tz = "+00:00"
            if v.tzinfo:
                offset = v.utcoffset()
                if offset is not None:
                    total = int(offset.total_seconds())
                    sign = "+" if total >= 0 else "-"
                    h, m = divmod(abs(total) // 60, 60)
                    tz = f"{sign}{h:02d}:{m:02d}"
            clean[k] = v.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms:03d}" + tz
        elif isinstance(v, str):
            # Normalise bare ISO strings that have no millisecond component
            m = _ISO_NO_MS.match(v)
            if m:
                dt_part, tz_part = m.group(1), m.group(2)
                tz_part = "+00:00" if tz_part == "Z" else tz_part
                clean[k] = f"{dt_part}.000{tz_part}"
            else:
                clean[k] = v
        else:
            clean[k] = v
    return clean


# ── File Storage ────────────────────────────────────────────────────────
class FileStorage:
    """Handle file uploads to Appwrite file storage buckets."""

    def __init__(self, bucket_id: str = "media"):
        self.bucket_id = bucket_id
        self._endpoint = APPWRITE_ENDPOINT
        self._project = APPWRITE_PROJECT_ID
        self._api_key = APPWRITE_API_KEY

    def _headers(self) -> Dict:
        return {
            "X-Appwrite-Project": self._project,
            "X-Appwrite-Key": self._api_key}

    def _files_url(self) -> str:
        return f"{self._endpoint}/storage/buckets/{self.bucket_id}/files"

    def upload_file(self, file_content: bytes, file_name: str,
                    mime_type: str = "application/octet-stream") -> Dict[str, Any]:
        try:
            files = {"file": (file_name, file_content, mime_type)}
            # Appwrite requires:
            #   • fileId — mandatory; "unique()" lets Appwrite auto-generate it
            #   • permissions[] — must be repeated form fields (not a JSON-stringified list)
            data = [
                ("fileId", "unique()"),
                ("permissions[]", 'read("any")'),
            ]
            r = _session.post(
                self._files_url(),
                headers=self._headers(),
                data=data,
                files=files)
            if not r.ok:
                logger.error(
                    f"❌ Appwrite upload error {r.status_code}: {r.text[:300]}")
            r.raise_for_status()
            response = r.json()
            logger.info(
                f"✅ File uploaded: {file_name} ({response.get('$id', 'unknown')})")
            return response
        except Exception as e:
            logger.error(f"❌ File upload failed: {str(e)}")
            raise

    def get_file_url(self, file_id: str) -> str:
        # project query param is required for public Appwrite file access
        return f"{self._endpoint}/storage/buckets/{self.bucket_id}/files/{file_id}/view?project={self._project}"

    def delete_file(self, file_id: str) -> bool:
        try:
            r = _session.delete(
                f"{self._files_url()}/{file_id}",
                headers=self._headers())
            r.raise_for_status()
            logger.info(f"✅ File deleted: {file_id}")
            return True
        except Exception as e:
            logger.error(f"❌ File delete failed: {str(e)}")
            return False


# ── Supabase-compatible client ──────────────────────────────────────────
class AppwriteClient:
    """Drop-in replacement for Supabase client — exposes .table() method."""

    def table(self, collection_id: str) -> TableQuery:
        return TableQuery(collection_id)

    def storage(self, bucket_id: str = "media") -> FileStorage:
        return FileStorage(bucket_id)


# ── Singleton helpers ───────────────────────────────────────────────────
_client = AppwriteClient()


def get_appwrite_client() -> AppwriteClient:
    return _client


# ── Direct document helpers (for code that imports AppwriteDB) ──────────
class AppwriteDB:
    """Simple helper class — used by app/core/database.py compat layer.

    Every method below falls back to an in-memory mock document on any
    Appwrite failure (unreachable, unconfigured, HTTP error) instead of
    raising — so AI generation and content creation never break just
    because a database isn't configured.
    """

    def _headers(self) -> Dict:
        return {
            "X-Appwrite-Project": APPWRITE_PROJECT_ID,
            "X-Appwrite-Key": APPWRITE_API_KEY,
            "Content-Type": "application/json",
        }

    def _col_url(self, collection_id: str) -> str:
        return f"{APPWRITE_ENDPOINT}/databases/{DATABASE_ID}/collections/{collection_id}/documents"

    def create_document(
            self,
            collection_id: str,
            data: Dict,
            document_id: Optional[str] = None) -> Dict:
        doc_id = document_id or str(uuid.uuid4()).replace("-", "")[:20]
        clean = _serialize({k: v for k, v in data.items() if v is not None})
        url = self._col_url(collection_id)

        try:
            r = _session.post(
                url, headers=self._headers(), json={
                    "documentId": doc_id, "data": clean})

            logger.debug(
                f"[APPWRITE] POST {collection_id}: status={r.status_code}, doc_id={doc_id}")

            if r.status_code >= 400:
                try:
                    error_data = r.json()
                    logger.warning(
                        f"[APPWRITE] Error creating document in {collection_id}: "
                        f"status={r.status_code} message={error_data.get('message', 'unknown')} "
                        f"code={error_data.get('code', 'unknown')} — falling back to in-memory")
                except BaseException:
                    logger.warning(
                        f"[APPWRITE] Error creating document in {collection_id}: "
                        f"status={r.status_code} — falling back to in-memory")

            r.raise_for_status()

            response_json = r.json()
            created_id = response_json.get('$id', doc_id)
            logger.debug(
                f"[APPWRITE] ✓ Document created: {collection_id}/{created_id}")

            return _norm(response_json)

        except Exception as e:
            logger.warning(
                f"[APPWRITE] create_document({collection_id}) unavailable "
                f"({type(e).__name__}) — storing in-memory instead")
            return _norm(_mock_put(collection_id, doc_id, clean))

    def get_document(self, collection_id: str, document_id: str) -> Dict:
        url = f"{self._col_url(collection_id)}/{document_id}"
        try:
            r = _session.get(url, headers=self._headers())

            logger.debug(
                f"[APPWRITE] GET {collection_id}/{document_id}: status={r.status_code}")

            r.raise_for_status()
            logger.debug(
                f"[APPWRITE] ✓ Document retrieved: {collection_id}/{document_id}")
            return _norm(r.json())

        except Exception as e:
            mock = _mock_get(collection_id, document_id)
            if mock is not None:
                logger.debug(
                    f"[APPWRITE] get_document({collection_id}/{document_id}) "
                    f"unavailable ({type(e).__name__}) — returning in-memory copy")
                return _norm(mock)
            logger.warning(
                f"[APPWRITE] get_document({collection_id}/{document_id}) "
                f"unavailable ({type(e).__name__}) and no in-memory copy — "
                f"returning minimal stub")
            return _norm({"$id": document_id})

    def update_document(
            self,
            collection_id: str,
            document_id: str,
            data: Dict) -> Dict:
        clean = _serialize({k: v for k, v in data.items() if v is not None})
        try:
            r = _session.patch(
                f"{self._col_url(collection_id)}/{document_id}",
                headers=self._headers(),
                json={"data": clean})
            r.raise_for_status()
            return _norm(r.json())
        except Exception as e:
            logger.warning(
                f"[APPWRITE] update_document({collection_id}/{document_id}) "
                f"unavailable ({type(e).__name__}) — updating in-memory instead")
            existing = _mock_get(collection_id, document_id) or {}
            merged = {**existing, **clean}
            return _norm(_mock_put(collection_id, document_id, merged))

    def delete_document(self, collection_id: str, document_id: str) -> None:
        try:
            r = _session.delete(
                f"{self._col_url(collection_id)}/{document_id}",
                headers=self._headers())
            r.raise_for_status()
        except Exception as e:
            logger.debug(
                f"[APPWRITE] delete_document({collection_id}/{document_id}) "
                f"unavailable ({type(e).__name__}) — removing in-memory copy if present")
            _mock_docs.get(collection_id, {}).pop(document_id, None)

    def list_documents(
            self,
            collection_id: str,
            queries: Optional[List] = None) -> Dict:
        params = [f'queries[]={json.dumps(q)}' for q in (queries or [])]
        url = self._col_url(collection_id)
        if params:
            url += "?" + "&".join(params)
        try:
            r = _session.get(url, headers=self._headers())
            r.raise_for_status()
            data = r.json()
            return {
                "total": data.get(
                    "total", 0), "documents": [
                    _norm(d) for d in data.get(
                        "documents", [])]}
        except Exception as e:
            logger.debug(
                f"[APPWRITE] list_documents({collection_id}) unavailable "
                f"({type(e).__name__}) — returning matching in-memory documents")
            docs = [_norm(d) for d in _mock_query(collection_id, queries or [])]
            return {"total": len(docs), "documents": docs}


# Export instances used by other modules
db = AppwriteDB()
databases = db  # backward compat alias
