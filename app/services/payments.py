from __future__ import annotations

from ..database import row_to_dict, rows_to_dicts
from ..domain.states import can_transition, ensure_transition
from ..errors import ApiError, conflict, not_found, validation
from ..ids import new_id, now_iso
from ..validation import Validator
from . import idempotency, navapay_adapter, orders
from .provider_sim import ProviderTimeout

SUCCESS_FAMILY = ("succeeded", "partially_refunded", "refunded")
OPEN_STATUSES = ("created", "pending", "unknown")
PROVIDER_TO_PAYMENT = {"SUCCEEDED": "succeeded", "FAILED": "failed", "EXPIRED": "expired",
                       "PENDING": "pending"}
# review reasons that a consistent provider answer is allowed to clear
CLEARABLE_REVIEW = ("provider_timeout", "provider_record_missing")


def get_payment(ctx, payment_id: str) -> dict:
    row = row_to_dict(ctx.conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone())
    if row is None:
        raise not_found("payment", payment_id)
    return row


def find_payment_by_reference(ctx, provider_reference=None, merchant_reference=None) -> dict | None:
    row = None
    if provider_reference:
        row = ctx.conn.execute("SELECT * FROM payments WHERE provider_reference = ?",
                               (provider_reference,)).fetchone()
    if row is None and merchant_reference:
        row = ctx.conn.execute("SELECT * FROM payments WHERE id = ?", (merchant_reference,)).fetchone()
    return row_to_dict(row)


def payment_view(p: dict) -> dict:
    view = {k: p[k] for k in ("id", "order_id", "merchant_id", "amount_irr", "status",
                              "provider_reference", "refunded_irr", "review_reason",
                              "correlation_id", "created_at", "updated_at")}
    view["amount_unit"] = "IRR"
    return view


def list_payments(ctx, q=None, status=None, date_from=None, date_to=None, merchant_id=None,
                  review_only=False, limit=200) -> list[dict]:
    sql = ["SELECT p.*, o.status AS order_status FROM payments p JOIN orders o ON o.id = p.order_id WHERE 1=1"]
    args: list = []
    if q:
        sql.append("AND (p.id = ? OR p.order_id = ? OR p.provider_reference = ? OR p.correlation_id = ?)")
        args += [q.strip()] * 4
    if status:
        sql.append("AND p.status = ?")
        args.append(status)
    if merchant_id:
        sql.append("AND p.merchant_id = ?")
        args.append(merchant_id)
    if date_from:
        sql.append("AND p.created_at >= ?")
        args.append(date_from)
    if date_to:
        sql.append("AND p.created_at < ?")
        args.append(date_to + "T99")  # inclusive end-of-day for ISO text comparison
    if review_only:
        sql.append("AND (p.review_reason IS NOT NULL OR p.status = 'unknown')")
    sql.append("ORDER BY p.created_at DESC LIMIT ?")
    args.append(limit)
    return rows_to_dicts(ctx.conn.execute(" ".join(sql), args).fetchall())


def set_payment_status(ctx, payment: dict, target: str, reason: str) -> dict:
    ensure_transition("payment", payment["status"], target)
    ctx.conn.execute("UPDATE payments SET status = ?, updated_at = ? WHERE id = ?",
                     (target, now_iso(), payment["id"]))
    ctx.audit("payment.status_changed", "payment", payment["id"], from_status=payment["status"],
              to_status=target, detail=reason, order_id=payment["order_id"], payment_id=payment["id"])
    return dict(payment, status=target)


def set_review_reason(ctx, payment: dict, reason: str | None) -> dict:
    if payment["review_reason"] == reason:
        return payment
    ctx.conn.execute("UPDATE payments SET review_reason = ?, updated_at = ? WHERE id = ?",
                     (reason, now_iso(), payment["id"]))
    ctx.audit("payment.review_flag", "payment", payment["id"],
              detail={"from": payment["review_reason"], "to": reason},
              order_id=payment["order_id"], payment_id=payment["id"])
    return dict(payment, review_reason=reason)


def create_payment(ctx, body, idem_key: str | None, simulate: str | None = None):
    key = idempotency.require_key(idem_key)
    req_hash = idempotency.request_hash(body if isinstance(body, dict) else {"_": body})
    replay = idempotency.lookup(ctx, "payments.create", key, req_hash)
    if replay:
        status, stored = replay
        ctx.adopt_correlation(stored.get("payment", {}).get("correlation_id"))
        ctx.note_ids(payment_id=stored.get("payment", {}).get("id"),
                     order_id=stored.get("payment", {}).get("order_id"))
        return status, stored, True

    v = Validator(body)
    order_id = v.str("order_id", max_len=40)
    amount = v.int("amount_irr", min_value=1)
    v.done()
    order = orders.get_order(ctx, order_id)
    ctx.adopt_correlation(order["correlation_id"])
    ctx.note_ids(order_id=order_id)
    if amount != order["amount_irr"]:
        raise validation([{"field": "amount_irr", "issue": f"must equal order amount {order['amount_irr']}"}],
                         "Payment amount does not match the order", "مبلغ پرداخت با مبلغ سفارش برابر نیست")
    if order["status"] == "cancelled":
        raise conflict("ORDER_CANCELLED", "order is cancelled", "سفارش لغو شده است")
    if order["status"] not in ("created", "pending_payment"):
        raise conflict("ORDER_ALREADY_PAID", f"order status is {order['status']}", "این سفارش قبلاً پرداخت شده است")
    active = ctx.conn.execute(
        f"SELECT id, status FROM payments WHERE order_id = ? AND status IN ({','.join('?'*len(OPEN_STATUSES))})",
        (order_id, *OPEN_STATUSES)).fetchone()
    if active:
        raise conflict("ACTIVE_PAYMENT_EXISTS", "order already has an open payment attempt",
                       "برای این سفارش یک تلاش پرداخت باز وجود دارد؛ ابتدا وضعیت آن را verify کنید",
                       [{"payment_id": active["id"], "status": active["status"]}])
    merchant = orders.get_merchant(ctx, order["merchant_id"])

    now = now_iso()
    payment_id = new_id("pay")
    ctx.conn.execute(
        "INSERT INTO payments (id, order_id, merchant_id, amount_irr, status, idempotency_key,"
        " correlation_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (payment_id, order_id, order["merchant_id"], amount, "created", key, ctx.correlation_id, now, now))
    ctx.audit("payment.created", "payment", payment_id, to_status="created", order_id=order_id,
              payment_id=payment_id, detail={"amount_irr": amount})
    ctx.note_ids(payment_id=payment_id)
    if order["status"] == "created":
        orders.set_order_status(ctx, order, "pending_payment", "payment attempt started", payment_id)
    payment = get_payment(ctx, payment_id)

    try:
        resp = navapay_adapter.create_transaction(ctx, payment, merchant, simulate)
    except ProviderTimeout:
        payment = set_payment_status(ctx, payment, "unknown", "provider timeout: outcome not known")
        payment = set_review_reason(ctx, payment, "provider_timeout")
        ctx.log("WARNING", "provider.timeout",
                "create transaction timed out; status set to unknown, verify required",
                payment_id=payment_id, order_id=order_id)
        body_out = {"payment": payment_view(get_payment(ctx, payment_id)),
                    "next_action": "POST /api/v1/payments/{id}/verify".replace("{id}", payment_id),
                    "message_fa": "پاسخ ارائه‌دهنده دریافت نشد؛ وضعیت نامشخص است و باید verify شود."}
        idempotency.store(ctx, "payments.create", key, req_hash, 202, body_out)
        return 202, body_out, False

    ctx.conn.execute("UPDATE payments SET provider_reference = ? WHERE id = ?",
                     (resp["provider_reference"], payment_id))
    payment = set_payment_status(ctx, get_payment(ctx, payment_id), "pending", "provider accepted transaction")
    ctx.note_ids(provider_reference=resp["provider_reference"])
    ctx.log("INFO", "provider.transaction_created", "NavaPay transaction created",
            payment_id=payment_id, order_id=order_id, provider_reference=resp["provider_reference"])
    body_out = {"payment": payment_view(get_payment(ctx, payment_id)), "payment_url": resp["payment_url"]}
    idempotency.store(ctx, "payments.create", key, req_hash, 201, body_out)
    return 201, body_out, False


def apply_provider_state(ctx, payment: dict, *, provider_status: str, amount: int, currency: str,
                         provider_reference: str | None, source: str) -> dict:
    """Shared by webhook processing and verify. Returns actions/discrepancies."""
    actions, discrepancies = [], []
    if provider_reference and payment["provider_reference"] is None:
        ctx.conn.execute("UPDATE payments SET provider_reference = ?, updated_at = ? WHERE id = ?",
                         (provider_reference, now_iso(), payment["id"]))
        payment = dict(payment, provider_reference=provider_reference)
        ctx.audit("payment.reference_attached", "payment", payment["id"], detail=provider_reference,
                  order_id=payment["order_id"], payment_id=payment["id"])
        actions.append("provider_reference_attached")
    elif provider_reference and payment["provider_reference"] != provider_reference:
        discrepancies.append({"type": "reference_mismatch", "internal": payment["provider_reference"],
                              "provider": provider_reference})
        return {"payment": payment, "actions": actions, "discrepancies": discrepancies}

    provider_irr = navapay_adapter.inbound_amount_irr(amount, currency)
    if provider_irr != payment["amount_irr"]:
        payment = set_review_reason(ctx, payment, "amount_mismatch")
        discrepancies.append({"type": "amount_mismatch", "internal_irr": payment["amount_irr"],
                              "provider_amount": amount, "provider_currency": currency,
                              "provider_irr": provider_irr})
        ctx.log("ERROR", f"{source}.amount_mismatch",
                f"provider amount {amount} {currency} (= {provider_irr} IRR) != internal {payment['amount_irr']} IRR;"
                " status NOT changed", payment_id=payment["id"], order_id=payment["order_id"],
                provider_reference=payment["provider_reference"])
        return {"payment": payment, "actions": actions, "discrepancies": discrepancies}

    target = PROVIDER_TO_PAYMENT[provider_status]
    already = payment["status"] == target or (target == "succeeded" and payment["status"] in SUCCESS_FAMILY)
    if already:
        actions.append("no_status_change_needed")
    elif can_transition("payment", payment["status"], target):
        payment = set_payment_status(ctx, payment, target, f"{source}: provider status {provider_status}")
        actions.append(f"payment_{target}")
    else:
        discrepancies.append({"type": "invalid_payment_transition", "from": payment["status"], "to": target})
        ctx.audit("payment.invalid_transition", "payment", payment["id"], from_status=payment["status"],
                  to_status=target, detail=f"{source} rejected transition", order_id=payment["order_id"],
                  payment_id=payment["id"])
        payment = set_review_reason(ctx, payment, "state_conflict")

    if payment["status"] == "succeeded":
        order = orders.get_order(ctx, payment["order_id"])
        if order["status"] == "pending_payment":
            orders.set_order_status(ctx, order, "paid", f"{source}: payment succeeded", payment["id"])
            actions.append("order_paid")
        elif order["status"] == "cancelled":
            discrepancies.append({"type": "order_state_conflict", "order_status": "cancelled",
                                  "payment_status": "succeeded"})
            ctx.audit("order.invalid_transition", "order", order["id"], from_status="cancelled",
                      to_status="paid", detail=f"{source}: payment succeeded after order was cancelled",
                      order_id=order["id"], payment_id=payment["id"])
            payment = set_review_reason(ctx, payment, "order_state_conflict")
            ctx.log("ERROR", f"{source}.order_state_conflict",
                    "payment succeeded but order is cancelled; order NOT changed, manual decision needed",
                    payment_id=payment["id"], order_id=order["id"],
                    provider_reference=payment["provider_reference"])

    if not discrepancies and payment["review_reason"] in CLEARABLE_REVIEW and payment["status"] != "unknown":
        payment = set_review_reason(ctx, payment, None)
        actions.append("review_flag_cleared")
    return {"payment": payment, "actions": actions, "discrepancies": discrepancies}


def _snapshot(ctx, payment_id):
    p = get_payment(ctx, payment_id)
    o = orders.get_order(ctx, p["order_id"])
    return {"payment_status": p["status"], "order_status": o["status"],
            "provider_reference": p["provider_reference"], "review_reason": p["review_reason"]}


def verify_payment(ctx, payment_id: str, simulate: str | None = None) -> dict:
    payment = get_payment(ctx, payment_id)
    ctx.adopt_correlation(payment["correlation_id"])
    ctx.note_ids(payment_id=payment_id, order_id=payment["order_id"],
                 provider_reference=payment["provider_reference"])
    before = _snapshot(ctx, payment_id)
    try:
        txn = navapay_adapter.inquire(ctx, payment, simulate)
    except ProviderTimeout as exc:
        raise ApiError(504, "PROVIDER_TIMEOUT", "NavaPay inquiry timed out; payment status unchanged",
                       "استعلام از ارائه‌دهنده timeout شد؛ وضعیت پرداخت تغییری نکرد. چند دقیقه بعد دوباره verify کنید") from exc

    if txn is None:
        result = {"actions": [], "discrepancies": []}
        if payment["status"] in ("created", "unknown"):
            # Contract: inquiry is authoritative; no record = request never reached NavaPay.
            set_payment_status(ctx, payment, "failed", "verify: provider has no record of this payment")
            set_review_reason(ctx, get_payment(ctx, payment_id), None)
            result["actions"].append("payment_failed_no_provider_record")
        else:
            set_review_reason(ctx, payment, "provider_record_missing")
            result["discrepancies"].append({"type": "provider_record_missing"})
        provider = None
    else:
        result = apply_provider_state(ctx, payment, provider_status=txn["status"], amount=txn["amount"],
                                      currency=txn["currency"], provider_reference=txn["provider_reference"],
                                      source="verify")
        provider = {k: txn[k] for k in ("provider_reference", "status", "amount", "currency", "refunded_amount")}
    after = _snapshot(ctx, payment_id)
    ctx.audit("payment.verified", "payment", payment_id, detail={"actions": result["actions"],
              "discrepancies": [d["type"] for d in result["discrepancies"]]},
              order_id=payment["order_id"], payment_id=payment_id)
    ctx.log("WARNING" if result["discrepancies"] else "INFO", "payment.verified",
            f"verify done actions={','.join(result['actions']) or '-'}"
            f" discrepancies={','.join(d['type'] for d in result['discrepancies']) or '-'}",
            payment_id=payment_id, order_id=payment["order_id"],
            provider_reference=after["provider_reference"])
    return {"payment_id": payment_id, "verified_at": now_iso(), "before": before, "provider": provider,
            "after": after, "actions": result["actions"], "discrepancies": result["discrepancies"],
            "correlation_id": ctx.correlation_id}
