"""Test refunds. Rule: refunded + in-flight refunds must never exceed the payment amount."""
from __future__ import annotations

from ..database import rows_to_dicts
from ..domain.states import ensure_transition
from ..errors import ApiError, conflict, validation
from ..ids import new_id, now_iso
from ..validation import Validator
from . import idempotency, navapay_adapter, orders, payments


def list_refunds(ctx, payment_id: str) -> list[dict]:
    return rows_to_dicts(ctx.conn.execute("SELECT * FROM refunds WHERE payment_id = ? ORDER BY created_at",
                                          (payment_id,)).fetchall())


def _set_refund(ctx, refund: dict, target: str, payment: dict, detail=None) -> dict:
    ensure_transition("refund", refund["status"], target)
    ctx.conn.execute("UPDATE refunds SET status = ?, updated_at = ? WHERE id = ?", (target, now_iso(), refund["id"]))
    ctx.audit("refund.status_changed", "refund", refund["id"], from_status=refund["status"], to_status=target,
              detail=detail, order_id=payment["order_id"], payment_id=payment["id"])
    return dict(refund, status=target)


def create_refund(ctx, payment_id: str, body, idem_key: str | None):
    key = idempotency.require_key(idem_key)
    payment = payments.get_payment(ctx, payment_id)
    ctx.adopt_correlation(payment["correlation_id"])
    ctx.note_ids(payment_id=payment_id, order_id=payment["order_id"], provider_reference=payment["provider_reference"])
    scope = f"refunds.create:{payment_id}"
    req_hash = idempotency.request_hash(body if isinstance(body, dict) else {"_": body})
    replay = idempotency.lookup(ctx, scope, key, req_hash)
    if replay:
        return replay[0], replay[1], True

    v = Validator(body)
    amount = v.int("amount_irr", min_value=1)
    reason = v.str("reason", required=False, max_len=200)
    v.done()
    if payment["status"] not in ("succeeded", "partially_refunded"):
        raise conflict("PAYMENT_NOT_REFUNDABLE", f"payment status is {payment['status']}",
                       "فقط پرداخت موفق قابل استرداد است")
    in_flight = ctx.conn.execute("SELECT COALESCE(SUM(amount_irr),0) FROM refunds WHERE payment_id = ?"
                                 " AND status IN ('requested','pending')", (payment_id,)).fetchone()[0]
    remaining = payment["amount_irr"] - payment["refunded_irr"] - in_flight
    if amount > remaining:
        ctx.log("WARNING", "refund.rejected_limit", f"refund {amount} > remaining {remaining}",
                payment_id=payment_id, order_id=payment["order_id"])
        raise validation([{"field": "amount_irr", "issue": f"exceeds refundable remainder {remaining} IRR"}],
                         "Refund exceeds the refundable amount",
                         f"مبلغ استرداد از مانده قابل استرداد ({remaining:,} ریال) بیشتر است")
    if amount % 10:
        raise validation([{"field": "amount_irr", "issue": "must be a multiple of 10 IRR in this lab"}])

    now = now_iso()
    refund = {"id": new_id("rfd"), "status": "requested"}
    ctx.conn.execute(
        "INSERT INTO refunds (id, payment_id, amount_irr, status, reason, idempotency_key, correlation_id,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (refund["id"], payment_id, amount, "requested", reason, key, ctx.correlation_id, now, now))
    ctx.audit("refund.requested", "refund", refund["id"], to_status="requested", order_id=payment["order_id"],
              payment_id=payment_id, detail={"amount_irr": amount})
    refund = _set_refund(ctx, refund, "pending", payment, "sent to provider")
    merchant = orders.get_merchant(ctx, payment["merchant_id"])
    try:
        resp = navapay_adapter.refund(ctx, payment, merchant, amount)
    except ApiError:
        _set_refund(ctx, refund, "failed", payment, "provider rejected refund")
        raise
    ctx.conn.execute("UPDATE refunds SET provider_refund_reference = ? WHERE id = ?",
                     (resp["refund_reference"], refund["id"]))
    _set_refund(ctx, refund, "succeeded", payment, resp["refund_reference"])
    new_total = payment["refunded_irr"] + amount
    ctx.conn.execute("UPDATE payments SET refunded_irr = ?, updated_at = ? WHERE id = ?", (new_total, now_iso(), payment_id))
    target = "refunded" if new_total == payment["amount_irr"] else "partially_refunded"
    payments.set_payment_status(ctx, payment, target, f"refund {refund['id']} succeeded")
    order = orders.get_order(ctx, payment["order_id"])
    if order["status"] in ("paid", "partially_refunded"):
        orders.set_order_status(ctx, order, target, f"refund {refund['id']}", payment_id)
    ctx.log("INFO", "refund.succeeded", f"refund {amount} IRR succeeded; total refunded {new_total}",
            payment_id=payment_id, order_id=payment["order_id"], provider_reference=payment["provider_reference"])
    row = ctx.conn.execute("SELECT * FROM refunds WHERE id = ?", (refund["id"],)).fetchone()
    out = {"refund": dict(row) | {"amount_unit": "IRR"},
           "payment": payments.payment_view(payments.get_payment(ctx, payment_id))}
    idempotency.store(ctx, scope, key, req_hash, 201, out)
    return 201, out, False
