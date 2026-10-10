"""One merged, time-ordered view of everything known about a payment or a
correlation ID: audit trail, received webhooks, refunds, logs and case notes."""
from __future__ import annotations

from ..errors import bad_request


def build_timeline(ctx, payment_id: str | None = None, correlation_id: str | None = None) -> list[dict]:
    if not payment_id and not correlation_id:
        raise bad_request("FILTER_REQUIRED", "payment_id or correlation_id is required",
                          "برای خط زمانی، payment_id یا correlation_id لازم است")
    c = ctx.conn
    if payment_id:
        cond, arg = "payment_id = ?", payment_id
    else:
        cond, arg = "correlation_id = ?", correlation_id
    items: list[dict] = []
    for r in c.execute(f"SELECT * FROM audit_events WHERE {cond}", (arg,)):
        change = f"{r['from_status'] or '∅'} → {r['to_status']}" if r["to_status"] else ""
        items.append({"at": r["occurred_at"], "source": "audit", "type": r["event_type"],
                      "status": r["to_status"], "summary": " ".join(x for x in (change, r["detail"] or "") if x),
                      "correlation_id": r["correlation_id"], "payment_id": r["payment_id"],
                      "order_id": r["order_id"], "status_code": None})
    for r in c.execute(f"SELECT * FROM provider_events WHERE {cond}", (arg,)):
        items.append({"at": r["received_at"], "source": "webhook", "type": r["event_type"] or "webhook",
                      "status": r["status"],
                      "summary": f"event_id={r['event_id'] or '-'} signature_valid={bool(r['signature_valid'])}"
                                 f" reason={r['reason'] or '-'}",
                      "correlation_id": r["correlation_id"], "payment_id": r["payment_id"], "order_id": None,
                      "status_code": None})
    for r in c.execute(f"SELECT * FROM refunds WHERE {cond}", (arg,)):
        items.append({"at": r["created_at"], "source": "refund", "type": "refund", "status": r["status"],
                      "summary": f"{r['id']} amount_irr={r['amount_irr']:,}", "correlation_id": r["correlation_id"],
                      "payment_id": r["payment_id"], "order_id": None, "status_code": None})
    for r in c.execute(f"SELECT * FROM app_logs WHERE {cond}", (arg,)):
        items.append({"at": r["timestamp"], "source": "log", "type": r["event_type"], "status": r["level"],
                      "summary": r["short_message"], "correlation_id": r["correlation_id"],
                      "payment_id": r["payment_id"], "order_id": r["order_id"], "status_code": r["status_code"],
                      "endpoint": r["endpoint"]})
    case_cond = "payment_id = ?" if payment_id else "correlation_id = ?"
    for r in c.execute(f"SELECT n.*, s.case_number FROM case_notes n JOIN support_cases s ON s.id = n.case_id"
                       f" WHERE s.{case_cond}", (arg,)):
        items.append({"at": r["created_at"], "source": "support", "type": f"case.{r['note_type']}",
                      "status": r["case_number"], "summary": r["body"][:200], "correlation_id": correlation_id,
                      "payment_id": payment_id, "order_id": None, "status_code": None})
    items.sort(key=lambda i: (i["at"], i["source"]))
    return items
