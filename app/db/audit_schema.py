#!/usr/bin/env python3
"""
Audit all Appwrite collections against expected schema from setup_database.py.
Reports any missing or extra attributes.
"""

import importlib.util
import sys
import os
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ENDPOINT = os.getenv("APPWRITE_ENDPOINT", "http://168.144.74.72/v1")
PROJECT_ID = os.getenv("APPWRITE_PROJECT_ID", "")
API_KEY = os.getenv("APPWRITE_API_KEY", "")
DATABASE_ID = "database-contentstudio"

HEADERS = {"X-Appwrite-Project": PROJECT_ID, "X-Appwrite-Key": API_KEY}

spec = importlib.util.spec_from_file_location("setup_db", os.path.join(
    os.path.dirname(__file__), "..", "..", "setup_database.py"))
mod = importlib.util.module_from_spec(spec)
# Don't execute main, just load the dict
spec.loader.exec_module(mod)
colls = mod.COLLECTIONS

print("=" * 90)
print("  FULL SCHEMA AUDIT: Expected vs Actual Appwrite Attributes")
print("=" * 90)

all_missing = {}
all_extra = {}
ok_count = 0
missing_count = 0

for col_id, schema in colls.items():
    expected = {a["key"]: a["type"] for a in schema["attributes"]}
    url = f"{ENDPOINT}/databases/{DATABASE_ID}/collections/{col_id}"
    r = requests.get(url, headers=HEADERS, timeout=10)
    if r.status_code != 200:
        print(f"  MISSING  {col_id} — collection not found")
        continue

    attr_url = f"{url}/attributes"
    ar = requests.get(attr_url, headers=HEADERS, timeout=10)
    if ar.status_code != 200:
        print(
            f"  ERROR    {col_id} — cannot read attributes: {ar.status_code}")
        continue

    actual = {a["key"]: a["type"] for a in ar.json().get("attributes", [])}
    missing = [f"{k}({expected[k]})" for k in expected if k not in actual]
    extra = [k for k in actual if k not in expected]

    if missing:
        print(f"  ** {col_id}: MISSING {missing}")
        all_missing[col_id] = missing
        missing_count += 1
    elif extra:
        print(f"  .. {col_id}: OK (extra: {extra})")
    else:
        print(f"     {col_id}: OK ({len(expected)} attributes match)")
        ok_count += 1

print(f"\n{'='*90}")
print(f"  Summary: {ok_count} OK, {missing_count} with missing attributes")
if all_missing:
    print("\n  Missing attributes to add:")
    for col, attrs in all_missing.items():
        print(f"    {col}: {attrs}")
print(f"{'='*90}")
