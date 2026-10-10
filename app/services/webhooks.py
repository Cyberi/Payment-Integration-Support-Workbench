"""Merchant webhook endpoint for NavaPay events.

Order of checks (each one is visible in provider_events + logs):
 1. HMAC signature over the raw body       -> 401, event 'rejected'
 2. JSON shape                              -> 422, event 'failed'
 3. event_id already processed              -> 200, event 'duplicate' (no side effect)
 4. transaction known to the merchant       -> 404, event 'failed' (unknown_transaction)
 5. business checks (amount, transitions)   -> 200 acknowledged, event 'failed' or 'processed'
Business problems are acknowledged with 200 so the provider does not retry
forever; they are flagged for a human through payments.review_reason.
"""
from __future__ import annotations

import hmac
import json
import sqlite3

from ..ids import now_iso
from ..validation import Validator
from ..errors import ApiError
from . import payments, provider_sim

EVENT_TYPES = ("payment.succeeded", "payment.failed", "payment.expired")


def _record(ctx, *, event_id=None, event_type=None, provider_reference=None, payment_id=None,
            status, reason=None, signature_valid, amount=None, currency=None) -> int:
    cur = ctx.conn.execute(
        "INSERT INTO provider_events (event_id, event_type, provider_reference, payment_id, status,"
        " reason, signature_valid, amount, currency, correlation_id, received_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (event_id, event_type, provider_reference, payment_id, status, reason, int(signature_valid),
         amount, currency, ctx.correlation_id, now_iso()))
    return cur.lastrowid


def _safe_peek(raw: bytes) -> dict:
    """Read only routing identifiers from an untrusted body (never stored whole)."""
    try:
        data = json.loads(raw.decode("utf-8"))
        return {k: data.get(k) for k in ("event_id", "event_type", "provider_reference", "merchant_reference")
                if isinstance(data.get(k), str)} if isinstance(data, dict) else {}
    except (ValueError, UnicodeDecodeError):
        return {}


def handle_navapay(ctx, raw_body: bytes, signature_header: str | None) -> tuple[int, dict]:
    peek = _safe_peek(raw_body)
    known = payments.find_payment_by_reference(ctx, peek.get("provider_reference"), peek.get("merchant_reference"))
    if known:
        ctx.adopt_correlation(known["correlation_id"])
        ctx.note_ids(payment_id=known["id"], order_id=known["order_id"])
    ctx.note_ids(provider_reference=peek.get("provider_reference"))

    expected = provider_sim.sign(raw_body, ctx.settings.webhook_secret)
    if not signature_header or not hmac.compare_digest(expected, signature_header.strip()):
        _record(ctx, event_id=peek.get("event_id"), event_type=peek.get("event_type"),
                provider_reference=peek.get("provider_reference"), payment_id=known and known["id"],
                status="rejected", reason="invalid_signature" if signature_header else "missing_signature",
                signature_valid=False)
        ctx.log("WARNING", "webhook.rejected", "webhook signature invalid or missing; body ignored",
                payment_id=known and known["id"], provider_reference=peek.get("provider_reference"))
        return 401, {"error": {"code": "INVALID_SIGNATURE", "message": "webhook signature is invalid",
                               "message_fa": "امضای webhook معتبر نیست", "details": []}}

    try:
        data = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        data = None
    try:
        v = Validator(data)
        event_id = v.str("event_id", max_len=64)
        event_type = v.str("event_type", choices=EVENT_TYPES)
        provider_reference = v.str("provider_reference", max_len=40)
        merchant_reference = v.str("merchant_reference", required=False, max_len=64)
        amount = v.int("amount", min_value=1)
        currency = v.str("currency", choices=("IRR", "IRT"))
        provider_status = v.str("status", choices=("SUCCEEDED", "FAILED", "EXPIRED"))
        v.done()
    except ApiError as exc:
        _record(ctx, event_id=peek.get("event_id"), status="failed", reason="invalid_payload",
                signature_valid=True)
        ctx.log("WARNING", "webhook.invalid_payload", "signed webhook with invalid payload")
        return 422, {"error": {"code": "VALIDATION_ERROR", "message": exc.message,
                               "message_fa": exc.message_fa, "details": exc.details}}

    dup = ctx.conn.execute("SELECT id FROM provider_events WHERE event_id = ? AND status = 'processed'",
                           (event_id,)).fetchone()
    if dup:
        _record(ctx, event_id=event_id, event_type=event_type, provider_reference=provider_reference,
                payment_id=known and known["id"], status="duplicate", reason=f"already processed as #{dup['id']}",
                signature_valid=True, amount=amount, currency=currency)
        ctx.log("INFO", "webhook.duplicate", f"event {event_id} already processed; no side effect",
                payment_id=known and known["id"], provider_reference=provider_reference)
        return 200, {"result": "duplicate", "event_id": event_id}

    payment = payments.find_payment_by_reference(ctx, provider_reference, merchant_reference)
    if payment is None:
        _record(ctx, event_id=event_id, event_type=event_type, provider_reference=provider_reference,
                status="failed", reason="unknown_transaction", signature_valid=True, amount=amount,
                currency=currency)
        ctx.log("ERROR", "webhook.unknown_transaction", "no payment matches provider_reference/merchant_reference",
                provider_reference=provider_reference)
        return 404, {"error": {"code": "UNKNOWN_TRANSACTION", "message": "no payment for this reference",
                               "message_fa": "پرداختی با این شناسه در سامانه پذیرنده پیدا نشد", "details": []}}

    result = payments.apply_provider_state(ctx, payment, provider_status=provider_status, amount=amount,
                                           currency=currency, provider_reference=provider_reference,
                                           source="webhook")
    blocking = [d for d in result["discrepancies"] if d["type"] in ("amount_mismatch", "reference_mismatch")]
    status = "failed" if blocking else "processed"
    reason = ",".join(d["type"] for d in result["discrepancies"]) or None
    try:
        _record(ctx, event_id=event_id, event_type=event_type, provider_reference=provider_reference,
                payment_id=payment["id"], status=status, reason=reason, signature_valid=True,
                amount=amount, currency=currency)
    except sqlite3.IntegrityError:
        # Unique partial index caught a concurrent duplicate.
        raise ApiError(409, "DUPLICATE_EVENT", "event already processed", "این رویداد قبلاً پردازش شده است")
    ctx.log("ERROR" if blocking else "INFO", f"webhook.{status}",
            f"{event_type} {status}; actions={','.join(result['actions']) or '-'}",
            payment_id=payment["id"], order_id=payment["order_id"], provider_reference=provider_reference)
    return 200, {"result": status, "event_id": event_id, "payment_id": payment["id"],
                 "actions": result["actions"], "discrepancies": result["discrepancies"]}
