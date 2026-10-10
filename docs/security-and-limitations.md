# Security, privacy and limitations

## What this project does not do
- It does not process real payments, connect to a bank, a PSP or a gateway, or send any network request to a payment provider.
- It does not collect card numbers, CVV2, card PINs, bank credentials or real customer identity.
- It does not claim PCI DSS, banking-grade security or production readiness.

## Controls that exist (teaching value)
- **Webhook authenticity:** HMAC-SHA256 over the raw body, compared in constant time (`hmac.compare_digest`). Invalid signatures are stored as `rejected` and the body is not processed.
- **Secrets in logs:** API keys and webhook secrets are redacted from every log field. PAN-like digit sequences and `Bearer` tokens are masked. Tests check this.
- **Request bodies in logs:** never logged. Only the fixed field set is written.
- **Input validation:** typed validation with field-level 422 details. Body size is capped at 2 MB; CSV at 1 MB and 5,000 rows.
- **Safe rendering:** every value rendered in HTML goes through `html.escape`. There is no raw HTML from data.
- **No free-form SQL from the UI:** diagnostic queries are named, fixed, and bind parameters.
- **Money:** integer only, one conversion point, explicit unit.
- **Idempotency and state rules:** enforced in code and covered by tests.

## Controls that are training only (would not be enough in production)
- A static demo API key and webhook secret that live in `.env.example`.
- No user login, roles, MFA, session management or audit of who clicked what (the audit actor is a fixed label).
- No TLS. The server binds to `127.0.0.1` and is meant for one person on one machine.
- No rate limits, no request signing for merchant calls, no key rotation.
- No backup, retention policy, encryption at rest or PII handling policy.
- No separation of duties for manual resolutions.

## What production would need
Secrets in a vault, per-merchant keys, TLS and a reverse proxy, real authentication and authorization, rate limits, durable queues for webhooks, a database server with backups, log shipping with redaction at the source, a retention policy, and a formal review of every control above.

## Known limitations
- Single process, SQLite, one writer at a time.
- The simulator's behaviour is a model of how one provider could behave. It is not a description of any real provider, and Iranian gateways do not all behave the same way.
- Webhook retries are manual (`redeliver-webhook`), capped at `LAB_MAX_WEBHOOK_ATTEMPTS`. There is no background retry loop.
- Refunds are instant in the simulator; real refunds are asynchronous.
- The Persian text was written for a support audience and has not been reviewed by a native-speaking support lead.
