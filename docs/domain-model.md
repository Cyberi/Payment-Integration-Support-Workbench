# Domain model

Money is always an **integer in IRR (rial)**. The adapter for each merchant has an explicit `provider_amount_unit` (`IRR` or `IRT`). Converting to or from toman is exact or an error. There is no rounding and no float anywhere in the money path.

| Table | Purpose | Key columns |
|---|---|---|
| `merchants` | Synthetic merchants with the adapter's unit | `id`, `provider_amount_unit` |
| `orders` | What the customer is buying | `amount_irr`, `status`, `correlation_id` |
| `payments` | One payment attempt for an order | `amount_irr`, `status`, `provider_reference` (unique), `sent_amount` + `sent_amount_unit`, `refunded_irr`, `review_reason`, `idempotency_key` |
| `provider_events` | Every webhook **received**, including rejected ones | `event_id`, `status`, `reason`, `signature_valid`; partial unique index on processed `event_id` |
| `refunds` | Test refunds | `amount_irr`, `status`, `provider_refund_reference` |
| `idempotency_records` | Stored responses by `(scope, key)` with a request hash | `request_hash`, `response_body` |
| `reconciliation_runs` | One row per CSV run | `row_count`, `matched_count`, `exception_count` |
| `reconciliation_exceptions` | One row per mismatch | `exception_type`, `suggestion`, `verification`, `status`, `case_id` |
| `support_cases` | Incident record with the 11-step structure | `evidence_json`, `hypotheses_json`, `root_cause_status`, `status` |
| `case_notes` | Notes and status changes | `note_type`, `body` |
| `audit_events` | Business audit: transitions and decisions | `from_status`, `to_status`, `payment_id`, `correlation_id` |
| `app_logs` | Structured logs | fixed field set (see `docs/api-troubleshooting.md`) |
| `sim_provider_transactions` | **Simulator's own ledger** (fake provider) | `provider_reference`, `status` (PENDING/SUCCEEDED/FAILED/EXPIRED), `last_event_body` |

## Review flags
`payments.review_reason` tells a human why a payment needs attention: `provider_timeout`, `amount_mismatch`, `order_state_conflict`, `provider_record_missing`, `state_conflict`. A consistent provider answer clears the timeout and missing-record flags. The amount and order-conflict flags are only cleared by a person.

## Identifiers
- Payment ID `pay_…` is also the `merchant_reference` sent to the provider, so an inquiry can be made even after a timeout that lost the provider reference.
- `provider_reference` is `NP-…`, `refund_reference` is `NPR-…`, `correlation_id` is `corr_…`.
