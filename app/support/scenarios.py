"""Reproducible incident scenarios. Each one drives the REAL API (in-process)
through the same code paths a merchant or the simulator would use, then returns
the evidence it produced. Nothing here is hard-coded output: every ID and status
shown in the UI comes from the database after the scenario ran."""
from __future__ import annotations

from ..ids import new_id
from ..web.framework import LabClient

MERCHANT_OK = "mer_sefid"
MERCHANT_IRT = "mer_daneshyar_irt"
AMOUNT = 250_000  # IRR


class Harness:
    def __init__(self, app, api_key: str):
        self.app = app
        self.corr = new_id("corr")
        self.client = LabClient(app, {"X-API-Key": api_key, "X-Correlation-ID": self.corr})
        self.steps: list[dict] = []
        self.payment_id = None
        self.order_id = None

    def call(self, label, method, path, *, body=None, headers=None, **kw):
        r = self.client.request(method, path, json_body=body, headers=headers, **kw)
        try:
            data = r.json()
        except ValueError:
            data = {}
        self.steps.append({"step": label, "method": method, "path": path, "http_status": r.status_code,
                           "correlation_id": self.corr, "response": data})
        return r.status_code, data

    def order(self, merchant=MERCHANT_OK, amount=AMOUNT):
        status, data = self.call("create order", "POST", "/api/v1/orders",
                                 body={"merchant_id": merchant, "amount_irr": amount,
                                       "description": "سفارش نمونه"})
        self.order_id = data["order"]["id"]
        return self.order_id

    def pay(self, label="create payment", simulate=None, key=None):
        headers = {"Idempotency-Key": key or new_id("idem")}
        if simulate:
            headers["X-Lab-Simulate"] = simulate
        status, data = self.call(label, "POST", "/api/v1/payments",
                                 body={"order_id": self.order_id, "amount_irr": AMOUNT}, headers=headers)
        self.payment_id = (data.get("payment") or {}).get("id") or self.payment_id
        return status, data

    def complete(self, outcome="succeeded", webhook="deliver"):
        return self.call(f"NavaPay: {outcome} (webhook {webhook})", "POST",
                         f"/api/v1/simulator/payments/{self.payment_id}/complete",
                         body={"outcome": outcome, "webhook": webhook})

    def verify(self):
        return self.call("verify payment", "POST", f"/api/v1/payments/{self.payment_id}/verify")

    def get_payment(self):
        return self.call("payment state", "GET", f"/api/v1/payments/{self.payment_id}")

    def result(self, name, summary, ok_: bool, **extra):
        return {"scenario": name, "correlation_id": self.corr, "order_id": self.order_id,
                "payment_id": self.payment_id, "ok": ok_, "summary": summary, "steps": self.steps, **extra}


def _state(h: Harness) -> dict:
    _, data = h.get_payment()
    return {"payment_status": data["payment"]["status"], "order_status": data["order"]["status"],
            "review_reason": data["payment"]["review_reason"]}


def webhook_missing(app, key):
    h = Harness(app, key)
    h.order(); h.pay(); h.complete("succeeded", webhook="skip")
    before = _state(h)
    _, v = h.verify()
    after = _state(h)
    ok = before["payment_status"] == "pending" and after == {"payment_status": "succeeded", "order_status": "paid",
                                                             "review_reason": None}
    return h.result("webhook-missing", f"قبل از verify: payment={before['payment_status']}, order={before['order_status']}؛ "
                    f"بعد از verify: payment={after['payment_status']}, order={after['order_status']}", ok,
                    before=before, after=after, actions=v.get("actions"))


def duplicate_webhook(app, key):
    h = Harness(app, key)
    h.order(); h.pay(); _, c = h.complete("succeeded", webhook="duplicate")
    deliveries = c.get("deliveries", [])
    results = [d["response"].get("result") for d in deliveries]
    state = _state(h)
    ok = results == ["processed", "duplicate"] and state["order_status"] == "paid"
    return h.result("duplicate-webhook", f"دو تحویل webhook: نتیجه‌ها = {results}؛ order={state['order_status']} (یک بار paid)", ok,
                    deliveries_results=results, state=state)


def timeout_unknown(app, key):
    h = Harness(app, key)
    h.order()
    s1, d1 = h.pay(simulate="provider_timeout")
    before = _state(h)
    s2, d2 = h.pay(key=None, simulate=None)  # second attempt must be blocked while first is open
    blocked = s2 == 409 and d2.get("error", {}).get("code") == "ACTIVE_PAYMENT_EXISTS"
    _, v = h.verify()
    after = _state(h)
    ok = s1 == 202 and before["payment_status"] == "unknown" and blocked and after["payment_status"] == "pending" \
        and after["review_reason"] is None
    return h.result("timeout-unknown", f"بعد از timeout: payment={before['payment_status']} (failed نشد)؛ "
                    f"تلاش دوم با {s2} بلاک شد؛ verify: payment={after['payment_status']}", ok, before=before, after=after)


def amount_unit_mismatch(app, key):
    h = Harness(app, key)
    h.order(merchant=MERCHANT_IRT); h.pay(); _, c = h.complete("succeeded", webhook="deliver")
    d = (c.get("deliveries") or [{}])[0]
    state = _state(h)
    ok = state["payment_status"] == "pending" and state["review_reason"] == "amount_mismatch" \
        and d.get("response", {}).get("result") == "failed"
    return h.result("amount-unit-mismatch", f"adapter با واحد تومان ارسال کرد؛ ارائه‌دهنده مبلغ را IRR خواند و "
                    f"پرداخت به‌جای موفق شدن، برای بررسی علامت خورد (review={state['review_reason']})", ok, state=state)


def invalid_signature(app, key):
    h = Harness(app, key)
    h.order(); h.pay(); _, c = h.complete("succeeded", webhook="invalid_signature")
    rejected = (c.get("deliveries") or [{}])[0].get("http_status")
    mid = _state(h)
    _, r = h.call("redeliver webhook (correct secret)", "POST", f"/api/v1/simulator/payments/{h.payment_id}/redeliver-webhook")
    after = _state(h)
    ok = rejected == 401 and mid["payment_status"] == "pending" and after["payment_status"] == "succeeded"
    return h.result("invalid-signature", f"webhook با امضای ساختگی HTTP {rejected} گرفت؛ پس از ارسال مجدد با امضای درست: "
                    f"payment={after['payment_status']}", ok, before=mid, after=after)


def idempotency_conflict(app, key):
    h = Harness(app, key)
    h.order()
    idem = new_id("idem")
    s1, d1 = h.pay(key=idem)
    s2, d2 = h.pay(key=idem)  # same key, same body -> replay
    same = s2 == 201 and d2["payment"]["id"] == d1["payment"]["id"]
    s3, d3 = h.call("same key, different body", "POST", "/api/v1/payments",
                    body={"order_id": h.order_id, "amount_irr": AMOUNT, "note": "changed"},
                    headers={"Idempotency-Key": idem})
    conflict = s3 == 409 and d3.get("error", {}).get("code") == "IDEMPOTENCY_KEY_REUSED"
    _, cnt = h.call("payments on this order", "GET", f"/api/v1/orders/{h.order_id}")
    ok = same and conflict and len(cnt["payments"]) == 1
    return h.result("idempotency-conflict", f"کلید تکراری با بدنه یکسان: همان پرداخت ({d1['payment']['id']}) برگشت؛ "
                    f"با بدنه متفاوت HTTP {s3} و تعداد پرداخت‌ها = {len(cnt['payments'])}", ok)


def order_payment_conflict(app, key):
    h = Harness(app, key)
    h.order(); h.pay()
    h.call("merchant cancels order while payment is open", "POST", f"/api/v1/orders/{h.order_id}/cancel")
    _, c = h.complete("succeeded", webhook="deliver")
    state = _state(h)
    ok = state["order_status"] == "cancelled" and state["payment_status"] == "succeeded" \
        and state["review_reason"] == "order_state_conflict"
    return h.result("order-payment-conflict", "سفارش لغو شد؛ بعد پرداخت موفق رسید. سفارش تغییر نکرد "
                    "و پرداخت برای تصمیم انسانی علامت خورد.", ok, state=state)


def refund_limit(app, key):
    h = Harness(app, key)
    h.order(); h.pay(); h.complete("succeeded", webhook="deliver")
    s1, _ = h.call("refund 100,000 IRR", "POST", f"/api/v1/payments/{h.payment_id}/refunds",
                   body={"amount_irr": 100_000, "reason": "درخواست پذیرنده"}, headers={"Idempotency-Key": new_id("idem")})
    s2, d2 = h.call("refund 200,000 IRR (over remainder)", "POST", f"/api/v1/payments/{h.payment_id}/refunds",
                    body={"amount_irr": 200_000}, headers={"Idempotency-Key": new_id("idem")})
    state = _state(h)
    ok = s1 == 201 and s2 == 422 and state["payment_status"] == "partially_refunded"
    return h.result("refund-limit", f"استرداد 100,000 ریال: {s1}؛ استرداد 200,000 ریال (بیش از مانده): {s2}", ok, state=state)


def reconciliation_demo(app, key):
    h = Harness(app, key)
    s, data = h.call("reconciliation demo run", "POST", "/api/v1/reconciliation/runs/demo")
    run = data.get("run", {})
    ok = s == 201 and run.get("exception_count", 0) >= 4
    return h.result("reconciliation", f"تطبیق نمونه: {run.get('row_count')} ردیف، {run.get('matched_count')} تطبیق، "
                    f"{run.get('exception_count')} مغایرت", ok, run_id=run.get("id"))


SCENARIOS = {
    "webhook-missing": {"title": "Webhook نرسیده", "runbook": "webhook-missing", "fn": webhook_missing},
    "duplicate-webhook": {"title": "Webhook تکراری", "runbook": "duplicate-webhook", "fn": duplicate_webhook},
    "timeout-unknown": {"title": "Timeout با نتیجه نامشخص", "runbook": "timeout-unknown", "fn": timeout_unknown},
    "amount-unit-mismatch": {"title": "مغایرت مبلغ / واحد پول", "runbook": "amount-unit-mismatch", "fn": amount_unit_mismatch},
    "invalid-signature": {"title": "امضای webhook نامعتبر", "runbook": "invalid-signature", "fn": invalid_signature},
    "idempotency-conflict": {"title": "تعارض Idempotency-Key", "runbook": "idempotency-conflict", "fn": idempotency_conflict},
    "order-payment-conflict": {"title": "ناسازگاری سفارش و پرداخت", "runbook": "order-payment-conflict", "fn": order_payment_conflict},
    "refund-limit": {"title": "استرداد بیش از مانده", "runbook": "refund-limit", "fn": refund_limit},
    "reconciliation": {"title": "تطبیق گزارش ارائه‌دهنده", "runbook": "reconciliation-exceptions", "fn": reconciliation_demo},
}


def run(app, name: str, api_key: str | None = None) -> dict:
    if api_key is None:
        api_key = app.settings.api_key
    return SCENARIOS[name]["fn"](app, api_key)


def run_all(app, api_key=None) -> list[dict]:
    return [run(app, n, api_key) for n in SCENARIOS]
