"""Merchant-side NavaPay adapter. The ONLY place where amounts are converted
between the internal unit (IRR) and what is sent to / received from NavaPay.

Outbound: uses the merchant's configured `provider_amount_unit`.
Inbound:  uses the `currency` the provider explicitly returns. That is why a
          wrong outbound setting becomes visible as an amount mismatch.
"""
from __future__ import annotations

from ..domain import money
from ..errors import ApiError
from . import provider_sim


def outbound_amount(amount_irr: int, merchant: dict) -> tuple[int, str]:
    unit = merchant["provider_amount_unit"]
    try:
        return money.to_unit(amount_irr, unit), unit
    except money.MoneyError as exc:
        raise ApiError(422, "AMOUNT_NOT_CONVERTIBLE", str(exc),
                       "مبلغ با واحد تنظیم‌شده درگاه قابل تبدیل دقیق نیست") from exc


def inbound_amount_irr(amount: int, currency: str) -> int:
    return money.to_irr(amount, currency)


def _sim_error(exc: provider_sim.SimError) -> ApiError:
    return ApiError(502, "PROVIDER_ERROR", f"NavaPay simulator: {exc.code}: {exc.message}",
                    "ارائه‌دهنده پرداخت (شبیه‌ساز) خطا برگرداند", [{"provider_code": exc.code}])


def create_transaction(ctx, payment: dict, merchant: dict, simulate=None) -> dict:
    amount, unit = outbound_amount(payment["amount_irr"], merchant)
    ctx.conn.execute("UPDATE payments SET sent_amount = ?, sent_amount_unit = ? WHERE id = ?",
                     (amount, unit, payment["id"]))
    try:
        return provider_sim.create_transaction(
            ctx.conn, merchant_id=merchant["id"], merchant_reference=payment["id"], amount=amount,
            correlation_id=payment["correlation_id"], simulate=simulate)
    except provider_sim.SimError as exc:
        raise _sim_error(exc) from exc


def inquire(ctx, payment: dict, simulate=None) -> dict | None:
    try:
        return provider_sim.inquire(ctx.conn, merchant_reference=payment["id"], simulate=simulate)
    except provider_sim.SimError as exc:
        raise _sim_error(exc) from exc


def refund(ctx, payment: dict, merchant: dict, amount_irr: int) -> dict:
    amount, _unit = outbound_amount(amount_irr, merchant)
    try:
        return provider_sim.refund(ctx.conn, provider_reference=payment["provider_reference"], amount=amount)
    except provider_sim.SimError as exc:
        raise _sim_error(exc) from exc
