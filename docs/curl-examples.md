# curl examples

Start the lab first: `python -m app.main`. Set these once in your shell (demo values from `.env.example`):

```bash
B=http://127.0.0.1:8000
K=lab_demo_api_key_change_me
```

## 1. Happy path
```bash
OID=$(curl -s -X POST $B/api/v1/orders -H "X-API-Key: $K" -H "Content-Type: application/json" \
  -d '{"merchant_id":"mer_sefid","amount_irr":250000,"description":"سفارش نمونه"}' | python -c "import sys,json;print(json.load(sys.stdin)['order']['id'])")

PID=$(curl -s -X POST $B/api/v1/payments -H "X-API-Key: $K" -H "Idempotency-Key: demo-key-$(date +%s)" \
  -H "Content-Type: application/json" -d "{\"order_id\":\"$OID\",\"amount_irr\":250000}" \
  | python -c "import sys,json;print(json.load(sys.stdin)['payment']['id'])")

curl -s -X POST $B/api/v1/simulator/payments/$PID/complete -H "X-API-Key: $K" \
  -H "Content-Type: application/json" -d '{"outcome":"succeeded","webhook":"deliver"}'
curl -s $B/api/v1/payments/$PID -H "X-API-Key: $K"
```

## 2. Duplicate webhook (expect `processed`, then `duplicate`)
```bash
curl -s -X POST $B/api/v1/simulator/payments/$PID/complete -H "X-API-Key: $K" \
  -H "Content-Type: application/json" -d '{"outcome":"succeeded","webhook":"duplicate"}'
```

## 3. Invalid webhook signature (expect 401 from the merchant endpoint)
```bash
curl -s -X POST $B/api/v1/simulator/payments/$PID/complete -H "X-API-Key: $K" \
  -H "Content-Type: application/json" -d '{"outcome":"succeeded","webhook":"invalid_signature"}'
```

## 4. Raw webhook with no signature (expect 401 `INVALID_SIGNATURE`)
```bash
curl -i -X POST $B/api/v1/webhooks/navapay -H "Content-Type: application/json" -d '{"event_id":"evt_demo"}'
```

## 5. Timeout, then verify
```bash
OID2=$(curl -s -X POST $B/api/v1/orders -H "X-API-Key: $K" -H "Content-Type: application/json" \
  -d '{"merchant_id":"mer_sefid","amount_irr":250000}' | python -c "import sys,json;print(json.load(sys.stdin)['order']['id'])")
curl -i -X POST $B/api/v1/payments -H "X-API-Key: $K" -H "Idempotency-Key: demo-timeout-$(date +%s)" \
  -H "X-Lab-Simulate: provider_timeout" -H "Content-Type: application/json" -d "{\"order_id\":\"$OID2\",\"amount_irr\":250000}"
# use the payment id printed in the 202 response
curl -s -X POST $B/api/v1/payments/<payment_id>/verify -H "X-API-Key: $K"
```

## 6. Idempotency: same key and body, then a different body (409)
```bash
KEY=demo-idem-$(date +%s)
curl -s -X POST $B/api/v1/payments -H "X-API-Key: $K" -H "Idempotency-Key: $KEY" -H "Content-Type: application/json" \
  -d "{\"order_id\":\"$OID2\",\"amount_irr\":250000}"
curl -i -X POST $B/api/v1/payments -H "X-API-Key: $K" -H "Idempotency-Key: $KEY" -H "Content-Type: application/json" \
  -d "{\"order_id\":\"$OID2\",\"amount_irr\":250000,\"note\":\"changed\"}"
```

## 7. Refund over the limit (expect 422)
```bash
curl -i -X POST $B/api/v1/payments/$PID/refunds -H "X-API-Key: $K" -H "Idempotency-Key: demo-refund-$(date +%s)" \
  -H "Content-Type: application/json" -d '{"amount_irr":999999999}'
```

## 8. Timeline and logs by correlation ID
```bash
CORR=$(curl -s -D - -o /dev/null $B/api/v1/health | tr -d '\r' | awk -F': ' 'tolower($1)=="x-correlation-id"{print $2}')
curl -s "$B/api/v1/timeline?payment_id=$PID"
curl -s "$B/api/v1/logs?payment_id=$PID&errors_only=true" -H "X-API-Key: $K"
```

## 9. Reconciliation with the demo CSV, then with your own file
```bash
curl -s -X POST $B/api/v1/reconciliation/runs/demo -H "X-API-Key: $K"
curl -s -X POST "$B/api/v1/reconciliation/runs?filename=mine.csv" -H "X-API-Key: $K" \
  -H "Content-Type: text/csv" --data-binary @data/examples/provider-transactions-demo.csv
```

## 10. Diagnostics
```bash
curl -s -X POST $B/api/v1/diagnostics/scenarios/webhook-missing -H "X-API-Key: $K"
curl -s "$B/api/v1/support/queries/duplicate_webhook_events" -H "X-API-Key: $K"
```
