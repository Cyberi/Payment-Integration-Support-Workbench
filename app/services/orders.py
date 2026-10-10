from __future__ import annotations

from ..database import row_to_dict
from ..domain.states import ensure_transition
from ..errors import conflict, not_found
from ..ids import new_id, now_iso
from ..validation import Validator


def get_order(ctx, order_id: str) -> dict:
    row = row_to_dict(ctx.conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone())
    if row is None:
        raise not_found("order", order_id)
    return row


def get_merchant(ctx, merchant_id: str) -> dict | None:
    return row_to_dict(ctx.conn.execute("SELECT * FROM merchants WHERE id = ?", (merchant_id,)).fetchone())


def order_view(order: dict) -> dict:
    return {k: order[k] for k in ("id", "merchant_id", "amount_irr", "description", "status",
                                  "correlation_id", "created_at", "updated_at")} | {"amount_unit": "IRR"}


def create_order(ctx, body) -> dict:
    v = Validator(body)
    merchant_id = v.str("merchant_id", max_len=40)
    amount = v.int("amount_irr", min_value=10_000, max_value=100_000_000_000)
    description = v.str("description", required=False, max_len=200) or "سفارش آزمایشی"
    v.done()
    if get_merchant(ctx, merchant_id) is None:
        raise not_found("merchant", merchant_id)
    now = now_iso()
    order_id = new_id("ord")
    ctx.conn.execute(
        "INSERT INTO orders (id, merchant_id, amount_irr, description, status, correlation_id,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (order_id, merchant_id, amount, description, "created", ctx.correlation_id, now, now))
    ctx.audit("order.created", "order", order_id, to_status="created", order_id=order_id,
              detail={"amount_irr": amount})
    ctx.note_ids(order_id=order_id)
    ctx.log("INFO", "order.created", f"order created amount_irr={amount}", order_id=order_id)
    return get_order(ctx, order_id)


def set_order_status(ctx, order: dict, target: str, reason: str, payment_id: str | None = None) -> dict:
    ensure_transition("order", order["status"], target)
    ctx.conn.execute("UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
                     (target, now_iso(), order["id"]))
    ctx.audit("order.status_changed", "order", order["id"], from_status=order["status"],
              to_status=target, detail=reason, order_id=order["id"], payment_id=payment_id)
    order = dict(order, status=target)
    return order


def cancel_order(ctx, order_id: str) -> dict:
    order = get_order(ctx, order_id)
    ctx.adopt_correlation(order["correlation_id"])
    ctx.note_ids(order_id=order_id)
    if order["status"] not in ("created", "pending_payment"):
        raise conflict("ORDER_NOT_CANCELLABLE", f"order in status {order['status']} cannot be cancelled",
                       "این سفارش در وضعیت فعلی قابل لغو نیست")
    active = ctx.conn.execute(
        "SELECT id FROM payments WHERE order_id = ? AND status IN ('created','pending','unknown')",
        (order_id,)).fetchall()
    set_order_status(ctx, order, "cancelled", "merchant cancelled order")
    if active:
        # Allowed (merchants do this), but it is exactly how late-success conflicts start.
        ctx.log("WARNING", "order.cancelled_with_active_payment",
                f"order cancelled while {len(active)} payment(s) still open", order_id=order_id,
                payment_id=active[0]["id"])
    else:
        ctx.log("INFO", "order.cancelled", "order cancelled", order_id=order_id)
    return get_order(ctx, order_id)
