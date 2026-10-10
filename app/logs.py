"""Structured logging with redaction.

Each record has a fixed set of fields. Records go to the `app_logs` table (so the
UI can filter them) and, as JSON lines, to var/logs/app.jsonl via the standard
`logging` module. Request/response bodies are never logged.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from .ids import now_iso

FIELDS = ("timestamp", "level", "event_type", "correlation_id", "order_id", "payment_id",
          "provider_reference", "endpoint", "status_code", "duration_ms", "short_message")

_PATTERNS = [
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [REDACTED]"),
    (re.compile(r"(?<!\d)\d{16,19}(?!\d)"), "[REDACTED_PAN]"),
    (re.compile(r"(?i)\b(cvv2?|password|passwd|secret|api[_-]?key|signature)\b\s*[:=]\s*\S+"),
     r"\1=[REDACTED]"),
]

_logger = logging.getLogger("payment_lab")
_logger.setLevel(logging.INFO)
_logger.propagate = False
_configured_paths: set[str] = set()


def _ensure_file_handler(path: str) -> None:
    if path in _configured_paths:
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(handler)
    _configured_paths.add(path)


def redact(text: str, sensitive_values: list[str]) -> str:
    out = str(text)
    for value in sensitive_values:
        if value:
            out = out.replace(value, "[REDACTED]")
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return out[:500]


def log_event(conn, settings, level: str, event_type: str, short_message: str, **fields) -> dict:
    record = {k: None for k in FIELDS}
    record.update({k: v for k, v in fields.items() if k in FIELDS})
    record["timestamp"] = now_iso()
    record["level"] = level.upper()
    record["event_type"] = event_type
    record["short_message"] = redact(short_message, settings.sensitive_values)
    for key in ("correlation_id", "order_id", "payment_id", "provider_reference", "endpoint"):
        if record[key] is not None:
            record[key] = redact(record[key], settings.sensitive_values)
    conn.execute(
        f"INSERT INTO app_logs ({', '.join(FIELDS)}) VALUES ({', '.join('?' * len(FIELDS))})",
        [record[k] for k in FIELDS],
    )
    if settings.write_log_file:
        _ensure_file_handler(settings.log_path)
        _logger.log(getattr(logging, record["level"], logging.INFO),
                    json.dumps(record, ensure_ascii=False))
    return record
