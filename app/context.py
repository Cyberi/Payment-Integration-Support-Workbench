"""Per-request context: DB connection, settings, correlation ID, audit/log helpers."""
from __future__ import annotations

import json

from .ids import now_iso
from .logs import log_event


class Ctx:
    def __init__(self, conn, settings, correlation_id: str, client_supplied: bool,
                 actor: str = "api", app=None):
        self.conn = conn
        self.settings = settings
        self.correlation_id = correlation_id
        self.client_supplied = client_supplied
        self.actor = actor
        self.app = app
        # Fields copied into the request-level log line.
        self.log_fields: dict = {}

    def adopt_correlation(self, correlation_id: str | None) -> None:
        """Payment-scoped calls inherit the payment's correlation ID unless the
        caller explicitly sent its own X-Correlation-ID."""
        if correlation_id and not self.client_supplied:
            self.correlation_id = correlation_id

    def note_ids(self, **ids) -> None:
        for k, v in ids.items():
            if v:
                self.log_fields[k] = v

    def audit(self, event_type: str, entity_type: str, entity_id: str, *, from_status=None,
              to_status=None, detail=None, order_id=None, payment_id=None) -> None:
        if isinstance(detail, (dict, list)):
            detail = json.dumps(detail, ensure_ascii=False)
        self.conn.execute(
            "INSERT INTO audit_events (occurred_at, event_type, entity_type, entity_id, order_id,"
            " payment_id, correlation_id, from_status, to_status, detail, actor)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (now_iso(), event_type, entity_type, entity_id, order_id, payment_id,
             self.correlation_id, from_status, to_status, detail, self.actor),
        )

    def log(self, level: str, event_type: str, message: str, **fields) -> None:
        fields.setdefault("correlation_id", self.correlation_id)
        log_event(self.conn, self.settings, level, event_type, message, **fields)
