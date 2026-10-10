"""Dashboard numbers. Every value is a live COUNT over this database."""
from __future__ import annotations

from ..domain.states import OPEN_CASE_STATUSES


def metrics(ctx) -> dict:
    c = ctx.conn

    def one(sql, args=()):
        return c.execute(sql, args).fetchone()[0]

    latest = c.execute("SELECT id FROM reconciliation_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    q = ",".join("?" * len(OPEN_CASE_STATUSES))
    return {
        "payments_needing_review": one("SELECT COUNT(*) FROM payments WHERE review_reason IS NOT NULL OR status = 'unknown'"),
        "payments_unknown": one("SELECT COUNT(*) FROM payments WHERE status = 'unknown'"),
        "payments_pending": one("SELECT COUNT(*) FROM payments WHERE status = 'pending'"),
        "open_cases": one(f"SELECT COUNT(*) FROM support_cases WHERE status IN ({q})", OPEN_CASE_STATUSES),
        "p1_p2_open_cases": one(f"SELECT COUNT(*) FROM support_cases WHERE status IN ({q}) AND severity IN ('P1','P2')",
                                OPEN_CASE_STATUSES),
        "webhooks_rejected": one("SELECT COUNT(*) FROM provider_events WHERE status = 'rejected'"),
        "webhooks_duplicate": one("SELECT COUNT(*) FROM provider_events WHERE status = 'duplicate'"),
        "webhooks_failed": one("SELECT COUNT(*) FROM provider_events WHERE status = 'failed'"),
        "latest_reconciliation_run": latest["id"] if latest else None,
        "open_reconciliation_exceptions": one(
            "SELECT COUNT(*) FROM reconciliation_exceptions WHERE status != 'resolved' AND run_id = ?",
            (latest["id"],)) if latest else 0,
        "api_errors": one("SELECT COUNT(*) FROM app_logs WHERE event_type = 'http.request' AND status_code >= 400"),
        "payments_by_status": {r[0]: r[1] for r in c.execute(
            "SELECT status, COUNT(*) FROM payments GROUP BY status ORDER BY status")},
        "review_reasons": {r[0]: r[1] for r in c.execute(
            "SELECT review_reason, COUNT(*) FROM payments WHERE review_reason IS NOT NULL GROUP BY review_reason")},
    }
