"""Writes postman/payment-integration-support.postman_collection.json (Postman v2.1)."""
import json
from pathlib import Path

HDR_KEY = {"key": "X-API-Key", "value": "{{apiKey}}"}


def req(name, method, path, body=None, headers=None, tests=None, raw=False, pre=None):
    h = [HDR_KEY] + (headers or [])
    item = {"name": name, "request": {"method": method, "header": h,
            "url": {"raw": "{{baseUrl}}" + path, "host": ["{{baseUrl}}"], "path": [p for p in path.strip("/").split("/")]}}}
    if body is not None:
        if raw:
            item["request"]["body"] = {"mode": "raw", "raw": body, "options": {"raw": {"language": "text"}}}
        else:
            item["request"]["body"] = {"mode": "raw", "raw": json.dumps(body, ensure_ascii=False, indent=2),
                                       "options": {"raw": {"language": "json"}}}
            item["request"]["header"].append({"key": "Content-Type", "value": "application/json"})
    events = list(pre or [])
    if tests:
        events.append({"listen": "test", "script": {"type": "text/javascript", "exec": tests}})
    if events:
        item["event"] = events
    return item


def idem(name="Idempotency-Key"):
    return {"key": name, "value": "{{idemKey}}"}


PRE_IDEM = [{"listen": "prerequest", "script": {"type": "text/javascript",
             "exec": ["pm.collectionVariables.set('idemKey', 'idem_' + Date.now() + '_' + Math.floor(Math.random()*1e6));"]}}]

items = [
    {"name": "0 · Health", "item": [
        req("Health", "GET", "/api/v1/health", tests=[
            "pm.test('200', () => pm.response.to.have.status(200));",
            "pm.test('simulation mode', () => pm.expect(pm.response.json().mode).to.eql('simulation-only'));"])]},
    {"name": "1 · Happy path", "item": [
        req("Create order", "POST", "/api/v1/orders", {"merchant_id": "mer_sefid", "amount_irr": 250000,
             "description": "سفارش نمونه"}, tests=[
             "pm.test('201', () => pm.response.to.have.status(201));",
             "pm.collectionVariables.set('orderId', pm.response.json().order.id);"], pre=None),
        req("Create payment", "POST", "/api/v1/payments", {"order_id": "{{orderId}}", "amount_irr": 250000},
             headers=[idem()], tests=[
             "pm.test('201', () => pm.response.to.have.status(201));",
             "pm.collectionVariables.set('paymentId', pm.response.json().payment.id);"], pre=PRE_IDEM),
        req("Simulator: provider succeeds (webhook delivered)", "POST", "/api/v1/simulator/payments/{{paymentId}}/complete",
            {"outcome": "succeeded", "webhook": "deliver"}, tests=[
            "pm.test('webhook processed', () => pm.expect(pm.response.json().deliveries[0].response.result).to.eql('processed'));"]),
        req("Get payment (expect succeeded / paid)", "GET", "/api/v1/payments/{{paymentId}}", tests=[
            "pm.test('succeeded', () => pm.expect(pm.response.json().payment.status).to.eql('succeeded'));",
            "pm.test('order paid', () => pm.expect(pm.response.json().order.status).to.eql('paid'));"]),
    ]},
    {"name": "2 · Idempotency", "item": [
        req("Same key, same body (replay)", "POST", "/api/v1/payments", {"order_id": "{{orderId}}", "amount_irr": 250000},
             headers=[{"key": "Idempotency-Key", "value": "{{idemKey}}"}], tests=[
             "pm.test('replayed flag', () => pm.expect(pm.response.headers.get('Idempotent-Replayed')).to.eql('true'));"], pre=None),
        req("Same key, different body (409)", "POST", "/api/v1/payments", {"order_id": "{{orderId}}", "amount_irr": 250000, "note": "x"},
            headers=[{"key": "Idempotency-Key", "value": "{{idemKey}}"}], tests=[
            "pm.test('409', () => pm.response.to.have.status(409));",
            "pm.test('code', () => pm.expect(pm.response.json().error.code).to.eql('IDEMPOTENCY_KEY_REUSED'));"]),
    ]},
    {"name": "3 · Webhook: invalid signature and duplicate", "item": [
        req("Simulator: invalid signature (expect 401 from merchant)", "POST", "/api/v1/simulator/payments/{{paymentId}}/complete",
            {"outcome": "succeeded", "webhook": "invalid_signature"}),
        req("Raw webhook without signature (expect 401)", "POST", "/api/v1/webhooks/navapay", '{"event_id":"evt_missing_sig"}',
            tests=["pm.test('401', () => pm.response.to.have.status(401));"], raw=True),
    ]},
    {"name": "4 · Timeout and verify", "item": [
        req("Create order for timeout test", "POST", "/api/v1/orders", {"merchant_id": "mer_sefid", "amount_irr": 250000,
             "description": "آزمایش timeout"}, tests=[
             "pm.test('201', () => pm.response.to.have.status(201));",
             "pm.collectionVariables.set('timeoutOrderId', pm.response.json().order.id);"]),
        req("Create payment with provider timeout (expect 202 unknown)", "POST", "/api/v1/payments",
             {"order_id": "{{timeoutOrderId}}", "amount_irr": 250000}, headers=[idem(), {"key": "X-Lab-Simulate", "value": "provider_timeout"}],
             tests=[
             "pm.test('202 unknown', () => { pm.response.to.have.status(202); pm.expect(pm.response.json().payment.status).to.eql('unknown'); });",
             "pm.collectionVariables.set('timeoutPaymentId', pm.response.json().payment.id);"], pre=PRE_IDEM),
        req("Verify timeout payment status", "POST", "/api/v1/payments/{{timeoutPaymentId}}/verify", tests=[
            "pm.test('verify returns after', () => pm.expect(pm.response.json()).to.have.property('after'));",
            "pm.test('provider status resolved', () => pm.expect(pm.response.json().after.payment_status).to.eql('pending'));"]),
    ]},
    {"name": "5 · Refunds", "item": [
        req("Refund 100,000 IRR", "POST", "/api/v1/payments/{{paymentId}}/refunds", {"amount_irr": 100000, "reason": "test"},
             headers=[idem()], pre=PRE_IDEM),
        req("Refund over remainder (expect 422)", "POST", "/api/v1/payments/{{paymentId}}/refunds", {"amount_irr": 999999999},
             headers=[idem()], tests=["pm.test('422', () => pm.response.to.have.status(422));"], pre=PRE_IDEM),
    ]},
    {"name": "6 · Evidence", "item": [
        req("Timeline by payment", "GET", "/api/v1/timeline?payment_id={{paymentId}}", tests=[
            "pm.test('has items', () => pm.expect(pm.response.json().count).to.be.above(0));"]),
        req("Logs by payment (warnings and errors)", "GET", "/api/v1/logs?payment_id={{paymentId}}&errors_only=true"),
        req("Diagnostic query: webhook events", "GET", "/api/v1/support/queries/webhook_events_for_payment?payment_id={{paymentId}}"),
    ]},
    {"name": "7 · Support & reconciliation", "item": [
        req("List support cases (open)", "GET", "/api/v1/support/cases?open=true"),
        req("Run reconciliation on demo CSV", "POST", "/api/v1/reconciliation/runs/demo", tests=[
            "pm.test('201', () => pm.response.to.have.status(201));"]),
        req("List reconciliation exceptions", "GET", "/api/v1/reconciliation/exceptions?status=open"),
        req("Dashboard metrics", "GET", "/api/v1/dashboard/metrics"),
    ]},
]
# Postman expects 'event' only where set; drop the helper kwarg leftovers
def clean(node):
    if isinstance(node, dict):
        return {k: clean(v) for k, v in node.items() if k != "event" or v is not None}
    if isinstance(node, list):
        return [clean(x) for x in node]
    return node

collection = {
    "info": {"_postman_id": "payment-integration-support-lab-v1",
             "name": "Payment Integration Support Workbench (simulation)",
             "description": "Local lab only. All merchants, orders, payments and webhooks are synthetic. Set baseUrl and apiKey in the environment.",
             "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
    "variable": [{"key": "baseUrl", "value": "http://127.0.0.1:8000"},
                 {"key": "apiKey", "value": "lab_demo_api_key_change_me"},
                 {"key": "orderId", "value": ""}, {"key": "paymentId", "value": ""}, {"key": "idemKey", "value": ""},
                 {"key": "timeoutOrderId", "value": ""}, {"key": "timeoutPaymentId", "value": ""}],
    "item": clean(items),
}
out = Path(__file__).with_name("payment-integration-support.postman_collection.json")
out.write_text(json.dumps(collection, ensure_ascii=False, indent=2), encoding="utf-8")
print("wrote", out)
