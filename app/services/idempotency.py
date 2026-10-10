"""Idempotency-Key handling.

Same key + same payload  -> stored response is replayed, no new side effect.
Same key + other payload -> HTTP 409 IDEMPOTENCY_KEY_REUSED.
Only successful (2xx) results are stored; a rejected request can be retried
with the same key after the payload is fixed.
"""
from __future__ import annotations

import hashlib
import json
import re

from ..errors import bad_request, conflict
from ..ids import now_iso

KEY_RE = re.compile(r"^[A-Za-z0-9_\-:.]{8,100}$")


def request_hash(payload) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def require_key(key: str | None) -> str:
    if not key:
        raise bad_request("IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required",
                          "هدر Idempotency-Key الزامی است")
    if not KEY_RE.match(key):
        raise bad_request("IDEMPOTENCY_KEY_INVALID", "Idempotency-Key must be 8-100 safe characters",
                          "قالب Idempotency-Key معتبر نیست (۸ تا ۱۰۰ کاراکتر مجاز)")
    return key


def lookup(ctx, scope: str, key: str, req_hash: str):
    row = ctx.conn.execute(
        "SELECT request_hash, response_status, response_body FROM idempotency_records"
        " WHERE scope = ? AND idem_key = ?", (scope, key)).fetchone()
    if row is None:
        return None
    if row["request_hash"] != req_hash:
        ctx.log("WARNING", "idempotency.conflict", f"key reused with a different payload scope={scope}")
        raise conflict("IDEMPOTENCY_KEY_REUSED",
                       "This Idempotency-Key was already used with a different request body",
                       "این Idempotency-Key قبلاً با بدنه متفاوتی استفاده شده است؛ برای درخواست جدید کلید جدید بسازید",
                       [{"scope": scope}])
    ctx.log("INFO", "idempotency.replay", f"stored response replayed scope={scope}")
    return row["response_status"], json.loads(row["response_body"])


def store(ctx, scope: str, key: str, req_hash: str, status: int, body: dict) -> None:
    ctx.conn.execute(
        "INSERT INTO idempotency_records (scope, idem_key, request_hash, response_status,"
        " response_body, created_at) VALUES (?,?,?,?,?,?)",
        (scope, key, req_hash, status, json.dumps(body, ensure_ascii=False), now_iso()))
