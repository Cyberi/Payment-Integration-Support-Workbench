# Changelog

## 1.0.0
- Persian RTL support console (dashboard, payments and timeline, support cases, diagnostics, reconciliation, runbooks).
- REST API under `/api/v1` with uniform error shape and correlation IDs.
- NavaPay Simulator (fake provider) with signed webhooks, timeout, duplicate and invalid-signature modes.
- Idempotency-Key handling, refund limit, unit-aware adapter, reconciliation with five exception types.
- Nine reproducible incident scenarios, eight seeded support cases, runbooks, Postman collection, docs.
- Tests: `python -m unittest discover -s tests -t .` (standard library only).
