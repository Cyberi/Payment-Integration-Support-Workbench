"""Money rules: integers only, one internal unit, one conversion point.

Internal unit: IRR (rial). The NavaPay Simulator contract is also IRR, but each
merchant adapter has an explicit `provider_amount_unit` setting. A wrong
setting (IRT/toman) is the lab's configuration-error scenario. This is a lab
configuration test; it says nothing about how any real gateway behaves.
"""
from __future__ import annotations

INTERNAL_UNIT = "IRR"
RIAL_PER_UNIT = {"IRR": 1, "IRT": 10}
UNIT_LABEL_FA = {"IRR": "ریال", "IRT": "تومان"}


class MoneyError(ValueError):
    pass


def _check_int(amount) -> int:
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise MoneyError("amount must be an integer (floats are not accepted for money)")
    return amount


def to_unit(amount_irr: int, unit: str) -> int:
    """Convert an internal IRR amount into `unit`. Exact or error, never rounding."""
    _check_int(amount_irr)
    factor = RIAL_PER_UNIT.get(unit)
    if factor is None:
        raise MoneyError(f"unsupported unit {unit!r}")
    if amount_irr % factor:
        raise MoneyError(f"{amount_irr} IRR is not an exact amount in {unit}")
    return amount_irr // factor


def to_irr(amount: int, unit: str) -> int:
    """Convert an amount expressed in `unit` into internal IRR."""
    _check_int(amount)
    factor = RIAL_PER_UNIT.get(unit)
    if factor is None:
        raise MoneyError(f"unsupported unit {unit!r}")
    return amount * factor


def format_amount(amount: int | None, unit: str = INTERNAL_UNIT) -> str:
    if amount is None:
        return "-"
    return f"{amount:,} {UNIT_LABEL_FA.get(unit, unit)}"
