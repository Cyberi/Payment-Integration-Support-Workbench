"""NavaPay Simulator: a FAKE payment provider that lives inside the lab.

It never talks to any bank, PSP or network service. Contract (documented in
docs/api-troubleshooting.md):
  * amounts are integers in IRR (rial); responses carry an explicit `currency`
  * webhooks are signed with HMAC-SHA256 over the raw body:
      X-NavaPay-Signature: sha256=<hex>
  * inquiry by merchant_reference is authoritative; "not found" means the
    create request never reached NavaPay.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json

from ..database import row_to_dict
from ..ids import new_id, new_provider_reference, now_iso, tehran_date

CONTRACT_CURRENCY = "IRR"
STALE_SECRET_FOR_LAB = "stale-secret-from-before-rotation-lab-only"
REPORT_COLUMNS = ["provider_reference", "merchant_reference", "transaction_date", "amount",
                  "currency", "status", "refunded_amount"]


class ProviderTimeout(Exception):
    """The request may or may not have been processed by the provider."""


class SimError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def sign(raw_body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()


def _txn(conn, *, provider_reference=None, merchant_reference=None):
    if provider_reference:
        row = conn.execute("SELECT * FROM sim_provider_transactions WHERE provider_reference = ?",
                           (provider_reference,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM sim_provider_transactions WHERE merchant_reference = ?",
                           (merchant_reference,)).fetchone()
    return row_to_dict(row)


def public_view(txn: dict | None) -> dict | None:
    if txn is None:
        return None
    return {k: txn[k] for k in ("provider_reference", "merchant_reference", "amount", "currency",
                                "status", "refunded_amount", "webhook_attempts", "last_event_id",
                                "created_at", "updated_at")}


def create_transaction(conn, *, merchant_id, merchant_reference, amount, correlation_id,
                       simulate=None) -> dict:
    if not isinstance(amount, int) or amount <= 0:
        raise SimError(422, "INVALID_AMOUNT", "amount must be a positive integer in IRR")
    if _txn(conn, merchant_reference=merchant_reference):
        raise SimError(409, "DUPLICATE_MERCHANT_REFERENCE", "merchant_reference already used")
    ref = new_provider_reference()
    now = now_iso()
    conn.execute(
        "INSERT INTO sim_provider_transactions (provider_reference, merchant_id, merchant_reference,"
        " amount, currency, status, metadata_correlation_id, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (ref, merchant_id, merchant_reference, amount, CONTRACT_CURRENCY, "PENDING", correlation_id,
         now, now))
    if simulate == "provider_timeout":
        # The provider DID create the transaction; only the response is lost.
        raise ProviderTimeout("NavaPay did not answer within the client timeout")
    return {"provider_reference": ref, "status": "PENDING", "amount": amount,
            "currency": CONTRACT_CURRENCY, "payment_url": f"/simulator/pay/{ref}"}


def inquire(conn, *, merchant_reference, simulate=None) -> dict | None:
    if simulate == "provider_timeout":
        raise ProviderTimeout("NavaPay inquiry timed out")
    return public_view(_txn(conn, merchant_reference=merchant_reference))


def get_by_merchant_reference(conn, merchant_reference) -> dict | None:
    return public_view(_txn(conn, merchant_reference=merchant_reference))


def _build_event(txn: dict, event_type: str) -> tuple[str, bytes]:
    event_id = new_id("evt")
    payload = {
        "event_id": event_id,
        "event_type": event_type,
        "provider_reference": txn["provider_reference"],
        "merchant_reference": txn["merchant_reference"],
        "amount": txn["amount"],
        "currency": txn["currency"],
        "status": txn["status"],
        "occurred_at": now_iso(),
        "metadata": {"correlation_id": txn["metadata_correlation_id"]},
    }
    return event_id, json.dumps(payload, separators=(",", ":")).encode("utf-8")


def complete(conn, *, merchant_reference, outcome) -> tuple[dict, str, bytes]:
    """Customer finishes (or abandons) the payment on the provider side."""
    txn = _txn(conn, merchant_reference=merchant_reference)
    if txn is None:
        raise SimError(404, "TRANSACTION_NOT_FOUND", "no NavaPay transaction for this merchant_reference")
    if txn["status"] != "PENDING":
        raise SimError(409, "TRANSACTION_FINAL", f"transaction already {txn['status']}")
    status = {"succeeded": "SUCCEEDED", "failed": "FAILED", "expired": "EXPIRED"}[outcome]
    txn["status"] = status
    event_id, raw = _build_event(txn, f"payment.{outcome}")
    conn.execute(
        "UPDATE sim_provider_transactions SET status = ?, last_event_id = ?, last_event_body = ?,"
        " updated_at = ? WHERE provider_reference = ?",
        (status, event_id, raw.decode("utf-8"), now_iso(), txn["provider_reference"]))
    return public_view(_txn(conn, merchant_reference=merchant_reference)), event_id, raw


def last_event(conn, merchant_reference) -> tuple[dict, str, bytes]:
    txn = _txn(conn, merchant_reference=merchant_reference)
    if txn is None:
        raise SimError(404, "TRANSACTION_NOT_FOUND", "no NavaPay transaction for this merchant_reference")
    if not txn["last_event_id"]:
        raise SimError(409, "NO_EVENT", "transaction has no webhook event yet")
    return public_view(txn), txn["last_event_id"], txn["last_event_body"].encode("utf-8")


def count_attempt(conn, provider_reference) -> int:
    conn.execute("UPDATE sim_provider_transactions SET webhook_attempts = webhook_attempts + 1"
                 " WHERE provider_reference = ?", (provider_reference,))
    return conn.execute("SELECT webhook_attempts FROM sim_provider_transactions WHERE provider_reference = ?",
                        (provider_reference,)).fetchone()[0]


def refund(conn, *, provider_reference, amount) -> dict:
    txn = _txn(conn, provider_reference=provider_reference)
    if txn is None:
        raise SimError(404, "TRANSACTION_NOT_FOUND", "unknown provider_reference")
    if txn["status"] != "SUCCEEDED":
        raise SimError(409, "NOT_REFUNDABLE", "only SUCCEEDED transactions can be refunded")
    if txn["refunded_amount"] + amount > txn["amount"]:
        raise SimError(422, "REFUND_EXCEEDS_AMOUNT", "refund total would exceed transaction amount")
    conn.execute("UPDATE sim_provider_transactions SET refunded_amount = refunded_amount + ?,"
                 " updated_at = ? WHERE provider_reference = ?", (amount, now_iso(), provider_reference))
    return {"refund_reference": "NPR-" + new_provider_reference()[3:], "status": "SUCCEEDED",
            "amount": amount, "currency": CONTRACT_CURRENCY}


def export_report_rows(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM sim_provider_transactions WHERE status != 'PENDING'"
                        " ORDER BY created_at, provider_reference").fetchall()
    return [{"provider_reference": r["provider_reference"], "merchant_reference": r["merchant_reference"],
             "transaction_date": tehran_date(r["updated_at"]), "amount": r["amount"],
             "currency": r["currency"], "status": r["status"], "refunded_amount": r["refunded_amount"]}
            for r in rows]


def rows_to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=REPORT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()
