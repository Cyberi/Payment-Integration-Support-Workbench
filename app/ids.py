"""Identifier and clock helpers.

IDs are random in normal runs. The seed script switches to a deterministic
generator so that the committed demo CSV matches the seeded database.
"""
from __future__ import annotations

import random
import secrets
from datetime import datetime, timedelta, timezone

_rng: random.Random | None = None

# Iran has used a fixed UTC+03:30 offset since DST was abolished in 2022.
# A fixed offset avoids needing the tzdata package on Windows.
TEHRAN = timezone(timedelta(hours=3, minutes=30), name="Asia/Tehran")


def set_deterministic(seed: int | None) -> None:
    global _rng
    _rng = random.Random(seed) if seed is not None else None


def _bits(n: int) -> int:
    return _rng.getrandbits(n) if _rng is not None else secrets.randbits(n)


def new_id(prefix: str) -> str:
    return f"{prefix}_{_bits(48):012x}"


def new_provider_reference() -> str:
    return f"NP-{_bits(40):010X}"



def now_iso() -> str:
    """Timezone-aware UTC timestamp, millisecond precision (sortable as text)."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def to_tehran_display(value: str | None) -> str:
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TEHRAN).strftime("%Y-%m-%d %H:%M:%S")


def tehran_date(value: str) -> str:
    dt = datetime.fromisoformat(value)
    return dt.astimezone(TEHRAN).strftime("%Y-%m-%d")
