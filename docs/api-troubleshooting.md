# API and troubleshooting reference

Base URL: `http://127.0.0.1:8000`. Every `/api/v1/*` call except `health` and the webhook needs `X-API-Key`. The lab key is a demo value from `.env.example`.

## Headers
| Header | Used on | Meaning |
|---|---|---|
| `X-API-Key` | all merchant calls | Demo key for this environment. A wrong key returns 401 `API_KEY_INVALID`. |
| `X-Correlation-ID` | any call | Optional, 8–64 safe characters. Returned on every response and in every error body. |
| `Idempotency-Key` | `POST /payments`, `POST /payments/{id}/refunds` | Required. Same key and same body replays the stored response. Same key with another body returns 409. |
| `Idempotent-Replayed` | response | `true` when a stored response was returned. |
| `X-Lab-Simulate: provider_timeout` | `POST /payments`, `POST /payments/{id}/verify` | Makes the simulator time out for this call only. |
| `X-NavaPay-Signature` | `POST /webhooks/navapay` | `sha256=<hex>` HMAC-SHA256 of the raw body with the webhook secret. |

## Endpoints
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/health` | Liveness, mode, currency |
| GET | `/api/v1/merchants` | Synthetic merchants and their adapter unit |
| POST | `/api/v1/orders` | Create order (`merchant_id`, `amount_irr`, `description`) |
| GET | `/api/v1/orders/{order_id}` | Order and its payments |
| POST | `/api/v1/orders/{order_id}/cancel` | Cancel order (warns if a payment is still open) |
| POST | `/api/v1/payments` | Start payment attempt (201 pending, 202 unknown on timeout, 409 if another attempt is open) |
| GET | `/api/v1/payments` | List and filter (`q`, `status`, `from`, `to`) |
| GET | `/api/v1/payments/{id}` | Payment, order and refunds |
| POST | `/api/v1/payments/{id}/verify` | Inquiry to provider; updates state only from its answer |
| POST | `/api/v1/payments/{id}/refunds` | Test refund (`amount_irr`, `reason`) |
| POST | `/api/v1/webhooks/navapay` | Merchant webhook endpoint (signed) |
| POST | `/api/v1/simulator/payments/{id}/complete` | Simulator: decide outcome; `webhook` = deliver, skip, duplicate, invalid_signature |
| POST | `/api/v1/simulator/payments/{id}/redeliver-webhook` | Send the last event again (max attempts apply) |
| GET | `/api/v1/simulator/payments/{id}` | Simulator's view of the transaction |
| GET | `/api/v1/simulator/reports/transactions.csv` | Synthetic settlement report |
| GET | `/api/v1/timeline?payment_id=` or `correlation_id=` | Merged evidence timeline |
| GET | `/api/v1/logs` | Filter: `correlation_id`, `payment_id`, `order_id`, `provider_reference`, `level`, `event_type`, `status_code`, `errors_only` |
| GET/POST | `/api/v1/support/cases` | List / create case |
| GET/PATCH | `/api/v1/support/cases/{id_or_number}` | Read / update (status rules enforced) |
| POST | `/api/v1/support/cases/{id}/notes` | Add note |
| GET | `/api/v1/support/queries` | List named diagnostic queries |
| GET | `/api/v1/support/queries/{name}?…` | Run one (fixed SQL, bound parameters, read-only) |
| GET | `/api/v1/support/runbooks` | Runbooks |
| POST | `/api/v1/reconciliation/runs` | Upload CSV as raw body (`Content-Type: text/csv`) |
| POST | `/api/v1/reconciliation/runs/demo` | Run on the committed demo CSV |
| GET | `/api/v1/reconciliation/runs/{id}` | Run and exceptions |
| GET | `/api/v1/reconciliation/exceptions` | Filter by `run_id`, `status`, `exception_type` |
| POST | `/api/v1/reconciliation/exceptions/{id}/case` | Open a case from an exception |
| POST | `/api/v1/reconciliation/exceptions/{id}/resolve` | Record resolution note (no money changes) |
| GET | `/api/v1/diagnostics/scenarios` | List scenarios |
| POST | `/api/v1/diagnostics/scenarios/{name}` | Run a scenario and return its steps |
| GET | `/api/v1/dashboard/metrics` | Live counters |

## Error shape (same for every error)
```json
{"error": {"code": "IDEMPOTENCY_KEY_REUSED", "message": "...", "message_fa": "...", "details": []},
 "correlation_id": "corr_..."}
```

| HTTP | Code examples | Meaning |
|---|---|---|
| 400 | `BODY_REQUIRED`, `INVALID_JSON`, `IDEMPOTENCY_KEY_REQUIRED` | Request is malformed or a required header is missing |
| 401 | `API_KEY_MISSING`, `API_KEY_INVALID`, `INVALID_SIGNATURE` | Authentication or signature failed |
| 404 | `NOT_FOUND`, `ROUTE_NOT_FOUND` | Object or route does not exist |
| 405 | `METHOD_NOT_ALLOWED` | Wrong method for the path |
| 409 | `ACTIVE_PAYMENT_EXISTS`, `IDEMPOTENCY_KEY_REUSED`, `ORDER_CANCELLED`, `INVALID_CASE_TRANSITION` | State or conflict |
| 413 | `BODY_TOO_LARGE`, `CSV_TOO_LARGE` | Over the size limit |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | CSV sent with the wrong content type |
| 422 | `VALIDATION_ERROR` (with `details` per field), `REFUND_*` limit | Input or business rule rejected |
| 502 | `PROVIDER_ERROR` | Simulator refused the request |
| 504 | `PROVIDER_TIMEOUT` | Verify timed out; state unchanged |

## Troubleshooting order (what to check first)
1. Take the `correlation_id` from the response or the error body.
2. `GET /api/v1/timeline?correlation_id=…` to see every step in order.
3. `GET /api/v1/logs?correlation_id=…&errors_only=true` for warnings and errors.
4. `GET /api/v1/payments/{id}` to compare payment, order and provider state.
5. Only then decide whether the merchant, the provider or the lab is at fault.

## Log fields
`timestamp` (UTC with offset), `level`, `event_type`, `correlation_id`, `order_id`, `payment_id`, `provider_reference`, `endpoint`, `status_code`, `duration_ms`, `short_message`. Bodies and secrets are never logged.
