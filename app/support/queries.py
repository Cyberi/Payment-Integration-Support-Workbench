"""Named, read-only diagnostic queries.

These are the same queries a support analyst would run in the sqlite3 CLI or
DB Browser for SQLite. The app only runs these fixed statements with bound
parameters; it never executes free-form SQL from the UI.
"""
from __future__ import annotations

from ..errors import ApiError, not_found

QUERIES: dict[str, dict] = {
    "payment_overview": {
        "title_fa": "نمای کامل پرداخت، سفارش و پذیرنده",
        "params": ["payment_id"],
        "sql": """SELECT p.id AS payment_id, p.status AS payment_status, p.amount_irr, p.sent_amount,
       p.sent_amount_unit, p.refunded_irr, p.review_reason, p.provider_reference, p.correlation_id,
       o.id AS order_id, o.status AS order_status, m.name AS merchant, m.provider_amount_unit,
       p.created_at, p.updated_at
FROM payments p
JOIN orders o ON o.id = p.order_id
JOIN merchants m ON m.id = p.merchant_id
WHERE p.id = :payment_id""",
    },
    "webhook_events_for_payment": {
        "title_fa": "همه webhookهای دریافتی برای یک پرداخت",
        "params": ["payment_id"],
        "sql": """SELECT id, event_id, event_type, status, reason, signature_valid, amount, currency, received_at
FROM provider_events
WHERE payment_id = :payment_id
ORDER BY received_at, id""",
    },
    "duplicate_webhook_events": {
        "title_fa": "event_idهایی که بیش از یک بار رسیده‌اند",
        "params": [],
        "sql": """SELECT event_id, COUNT(*) AS deliveries,
       SUM(status = 'processed') AS processed, SUM(status = 'duplicate') AS duplicates,
       MIN(received_at) AS first_seen, MAX(received_at) AS last_seen
FROM provider_events
WHERE event_id IS NOT NULL
GROUP BY event_id
HAVING COUNT(*) > 1
ORDER BY last_seen DESC""",
    },
    "rejected_webhooks": {
        "title_fa": "webhookهای ردشده یا ناموفق",
        "params": [],
        "sql": """SELECT id, event_id, event_type, provider_reference, payment_id, status, reason, received_at
FROM provider_events
WHERE status IN ('rejected', 'failed')
ORDER BY received_at DESC""",
    },
    "open_payments_vs_provider": {
        "title_fa": "پرداخت‌های باز داخلی در برابر وضعیت شبیه‌ساز (فقط در lab)",
        "params": [],
        "sql": """-- LAB ONLY: in real life you would ask the provider (inquiry API / panel),
-- you would not have their table. Here the simulator table stands in for that answer.
SELECT p.id AS payment_id, p.status AS internal_status, p.review_reason, t.status AS provider_status,
       t.provider_reference, t.webhook_attempts, p.created_at
FROM payments p
LEFT JOIN sim_provider_transactions t ON t.merchant_reference = p.id
WHERE p.status IN ('created', 'pending', 'unknown')
ORDER BY p.created_at""",
    },
    "amount_unit_check": {
        "title_fa": "مقایسه مبلغ داخلی، مبلغ ارسالی و واحد تنظیم‌شده",
        "params": [],
        "sql": """SELECT p.id AS payment_id, m.name AS merchant, m.provider_amount_unit, p.amount_irr,
       p.sent_amount, p.sent_amount_unit, p.review_reason
FROM payments p JOIN merchants m ON m.id = p.merchant_id
WHERE p.sent_amount IS NOT NULL AND p.sent_amount <> p.amount_irr
ORDER BY p.created_at""",
    },
    "order_payment_conflicts": {
        "title_fa": "پرداخت موفق روی سفارش لغوشده",
        "params": [],
        "sql": """SELECT p.id AS payment_id, p.status AS payment_status, o.id AS order_id, o.status AS order_status,
       p.review_reason, p.updated_at
FROM payments p JOIN orders o ON o.id = p.order_id
WHERE p.status IN ('succeeded', 'partially_refunded', 'refunded') AND o.status = 'cancelled'""",
    },
    "idempotency_key_usage": {
        "title_fa": "استفاده از یک Idempotency-Key",
        "params": ["idem_key"],
        "sql": """SELECT scope, idem_key, request_hash, response_status, created_at
FROM idempotency_records
WHERE idem_key = :idem_key""",
    },
    "status_transitions_for_payment": {
        "title_fa": "تاریخچه تغییر وضعیت پرداخت و سفارش",
        "params": ["payment_id"],
        "sql": """SELECT occurred_at, entity_type, entity_id, event_type, from_status, to_status, detail
FROM audit_events
WHERE payment_id = :payment_id
ORDER BY occurred_at, id""",
    },
    "reconciliation_open_exceptions": {
        "title_fa": "مغایرت‌های باز تطبیق",
        "params": [],
        "sql": """SELECT e.id, e.run_id, e.exception_type, e.provider_reference, e.payment_id, e.csv_row,
       e.expected, e.actual, e.status, c.case_number
FROM reconciliation_exceptions e
LEFT JOIN support_cases c ON c.id = e.case_id
WHERE e.status <> 'resolved'
ORDER BY e.created_at, e.csv_row""",
    },
    "errors_by_correlation": {
        "title_fa": "خطاهای HTTP و لاگ‌های ERROR برای یک correlation_id",
        "params": ["correlation_id"],
        "sql": """SELECT timestamp, level, event_type, endpoint, status_code, duration_ms, short_message
FROM app_logs
WHERE correlation_id = :correlation_id AND (level IN ('ERROR', 'WARNING') OR status_code >= 400)
ORDER BY timestamp, id""",
    },
}


def run_query(ctx, name: str, params: dict) -> dict:
    q = QUERIES.get(name)
    if q is None:
        raise not_found("query", name)
    missing = [p for p in q["params"] if not params.get(p)]
    if missing:
        raise ApiError(400, "QUERY_PARAMS_REQUIRED", f"missing params: {missing}",
                       "پارامترهای query کامل نیست: " + ", ".join(missing))
    cur = ctx.conn.execute(q["sql"], {p: params[p] for p in q["params"]})
    columns = [d[0] for d in cur.description]
    rows = [list(r) for r in cur.fetchmany(200)]
    return {"name": name, "title_fa": q["title_fa"], "sql": q["sql"], "params": {p: params[p] for p in q["params"]},
            "columns": columns, "rows": rows}
