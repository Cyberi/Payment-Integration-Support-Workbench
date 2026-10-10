"""Explicit state machines. Anything not listed here is an invalid transition."""
from __future__ import annotations

ORDER = {
    "created": {"pending_payment", "cancelled"},
    "pending_payment": {"paid", "cancelled"},
    "paid": {"partially_refunded", "refunded"},
    "partially_refunded": {"partially_refunded", "refunded"},
    "cancelled": set(),
    "refunded": set(),
}

# 'unknown' is a real state: the outcome is not known yet. It is NEVER turned
# into 'failed' because of a timeout alone; only provider evidence moves it.
PAYMENT = {
    "created": {"pending", "unknown", "failed"},
    "pending": {"succeeded", "failed", "unknown", "expired"},
    "unknown": {"pending", "succeeded", "failed", "expired"},
    "succeeded": {"partially_refunded", "refunded"},
    "partially_refunded": {"partially_refunded", "refunded"},
    "failed": set(),
    "expired": set(),
    "refunded": set(),
}

WEBHOOK_EVENT = {"received", "processed", "duplicate", "rejected", "failed"}

REFUND = {
    "requested": {"pending", "failed"},
    "pending": {"succeeded", "failed"},
    "succeeded": set(),
    "failed": set(),
}

CASE = {
    "new": {"investigating", "waiting_for_merchant", "escalated"},
    "investigating": {"waiting_for_merchant", "escalated", "resolved"},
    "waiting_for_merchant": {"investigating", "escalated", "resolved"},
    "escalated": {"investigating", "resolved"},
    "resolved": {"closed", "investigating"},
    "closed": set(),
}

MACHINES = {"order": ORDER, "payment": PAYMENT, "refund": REFUND, "case": CASE}

OPEN_CASE_STATUSES = ("new", "investigating", "waiting_for_merchant", "escalated")


class InvalidTransition(Exception):
    def __init__(self, entity: str, current: str, target: str):
        self.entity, self.current, self.target = entity, current, target
        super().__init__(f"invalid {entity} transition {current} -> {target}")


def can_transition(entity: str, current: str, target: str) -> bool:
    return target in MACHINES[entity].get(current, set())


def ensure_transition(entity: str, current: str, target: str) -> None:
    if not can_transition(entity, current, target):
        raise InvalidTransition(entity, current, target)
