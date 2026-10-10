-- Payment Integration Support Workbench: SQLite schema
-- Money: INTEGER only. Internal unit is IRR (rial). Timestamps: ISO-8601 UTC with offset.

CREATE TABLE IF NOT EXISTS merchants (
    id                   TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    provider_amount_unit TEXT NOT NULL CHECK (provider_amount_unit IN ('IRR', 'IRT')),
    note                 TEXT,
    created_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id             TEXT PRIMARY KEY,
    merchant_id    TEXT NOT NULL REFERENCES merchants(id),
    amount_irr     INTEGER NOT NULL CHECK (amount_irr > 0),
    description    TEXT NOT NULL,
    status         TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id                   TEXT PRIMARY KEY,
    order_id             TEXT NOT NULL REFERENCES orders(id),
    merchant_id          TEXT NOT NULL REFERENCES merchants(id),
    amount_irr           INTEGER NOT NULL CHECK (amount_irr > 0),
    status               TEXT NOT NULL,
    provider_reference   TEXT UNIQUE,
    sent_amount          INTEGER,           -- amount as sent to provider, in sent_amount_unit
    sent_amount_unit     TEXT,
    refunded_irr         INTEGER NOT NULL DEFAULT 0 CHECK (refunded_irr >= 0),
    review_reason        TEXT,              -- why a human should look at it (NULL = nothing pending)
    idempotency_key      TEXT,
    correlation_id       TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    CHECK (refunded_irr <= amount_irr)
);
CREATE INDEX IF NOT EXISTS ix_payments_order ON payments(order_id);
CREATE INDEX IF NOT EXISTS ix_payments_corr ON payments(correlation_id);

-- Webhook events as RECEIVED by the merchant system (one row per delivery).
CREATE TABLE IF NOT EXISTS provider_events (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id           TEXT,
    event_type         TEXT,
    provider_reference TEXT,
    payment_id         TEXT,
    status             TEXT NOT NULL CHECK (status IN ('received','processed','duplicate','rejected','failed')),
    reason             TEXT,
    signature_valid    INTEGER NOT NULL,
    amount             INTEGER,
    currency           TEXT,
    correlation_id     TEXT,
    received_at        TEXT NOT NULL
);
-- Database-level guard: an event_id can be *processed* only once.
CREATE UNIQUE INDEX IF NOT EXISTS ux_provider_events_processed
    ON provider_events(event_id) WHERE status = 'processed';
CREATE INDEX IF NOT EXISTS ix_provider_events_payment ON provider_events(payment_id);

CREATE TABLE IF NOT EXISTS refunds (
    id                 TEXT PRIMARY KEY,
    payment_id         TEXT NOT NULL REFERENCES payments(id),
    amount_irr         INTEGER NOT NULL CHECK (amount_irr > 0),
    status             TEXT NOT NULL,
    reason             TEXT,
    provider_refund_reference TEXT,
    idempotency_key    TEXT,
    correlation_id     TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

-- Stored responses for Idempotency-Key replays (scope = operation name).
CREATE TABLE IF NOT EXISTS idempotency_records (
    scope           TEXT NOT NULL,
    idem_key        TEXT NOT NULL,
    request_hash    TEXT NOT NULL,
    response_status INTEGER NOT NULL,
    response_body   TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (scope, idem_key)
);

CREATE TABLE IF NOT EXISTS reconciliation_runs (
    id              TEXT PRIMARY KEY,
    source_filename TEXT NOT NULL,
    row_count       INTEGER NOT NULL,
    matched_count   INTEGER NOT NULL,
    exception_count INTEGER NOT NULL,
    correlation_id  TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reconciliation_exceptions (
    id                 TEXT PRIMARY KEY,
    run_id             TEXT NOT NULL REFERENCES reconciliation_runs(id),
    exception_type     TEXT NOT NULL CHECK (exception_type IN
        ('internal_only','provider_only','amount_mismatch','status_mismatch','duplicate_provider_reference')),
    provider_reference TEXT,
    payment_id         TEXT,
    csv_row            INTEGER,
    expected           TEXT,
    actual             TEXT,
    suggestion         TEXT NOT NULL,
    verification       TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','linked','resolved')),
    case_id            TEXT,
    resolution_note    TEXT,
    created_at         TEXT NOT NULL,
    resolved_at        TEXT
);

CREATE TABLE IF NOT EXISTS support_cases (
    id                  TEXT PRIMARY KEY,
    case_number         TEXT NOT NULL UNIQUE,
    title               TEXT NOT NULL,
    problem_type        TEXT NOT NULL,
    severity            TEXT NOT NULL CHECK (severity IN ('P1','P2','P3','P4')),
    business_impact     TEXT NOT NULL,
    status              TEXT NOT NULL,
    merchant_report     TEXT NOT NULL,
    reported_at         TEXT,
    required_data       TEXT,
    data_not_to_request TEXT,
    priority_reason     TEXT,
    initial_checks      TEXT,
    evidence_json       TEXT NOT NULL DEFAULT '[]',
    hypotheses_json     TEXT NOT NULL DEFAULT '[]',
    root_cause          TEXT,
    root_cause_status   TEXT NOT NULL DEFAULT 'not_determined'
                        CHECK (root_cause_status IN ('not_determined','confirmed','unknown')),
    resolution          TEXT,
    authority_boundary  TEXT,
    validation          TEXT,
    merchant_message    TEXT,
    closure_summary     TEXT,
    payment_id          TEXT,
    order_id            TEXT,
    correlation_id      TEXT,
    runbook_slug        TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS case_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id    TEXT NOT NULL REFERENCES support_cases(id),
    author     TEXT NOT NULL,
    note_type  TEXT NOT NULL CHECK (note_type IN ('note','evidence','validation','status_change','merchant_message')),
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Business audit trail: state transitions and notable decisions.
CREATE TABLE IF NOT EXISTS audit_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at    TEXT NOT NULL,
    event_type     TEXT NOT NULL,
    entity_type    TEXT NOT NULL,
    entity_id      TEXT NOT NULL,
    order_id       TEXT,
    payment_id     TEXT,
    correlation_id TEXT,
    from_status    TEXT,
    to_status      TEXT,
    detail         TEXT,
    actor          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_payment ON audit_events(payment_id);
CREATE INDEX IF NOT EXISTS ix_audit_corr ON audit_events(correlation_id);

-- Structured application logs (also written as JSON lines to var/logs/app.jsonl).
CREATE TABLE IF NOT EXISTS app_logs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp          TEXT NOT NULL,
    level              TEXT NOT NULL,
    event_type         TEXT NOT NULL,
    correlation_id     TEXT,
    order_id           TEXT,
    payment_id         TEXT,
    provider_reference TEXT,
    endpoint           TEXT,
    status_code        INTEGER,
    duration_ms        INTEGER,
    short_message      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_logs_corr ON app_logs(correlation_id);
CREATE INDEX IF NOT EXISTS ix_logs_payment ON app_logs(payment_id);

-- ---------------------------------------------------------------------------
-- NavaPay Simulator ledger. This is the FAKE provider's own data, kept in the
-- same file only for convenience. The merchant code reads it ONLY through the
-- simulator interface (app/services/provider_sim.py), never directly.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sim_provider_transactions (
    provider_reference   TEXT PRIMARY KEY,
    merchant_id          TEXT NOT NULL,
    merchant_reference   TEXT NOT NULL UNIQUE,
    amount               INTEGER NOT NULL,      -- NavaPay contract unit: IRR
    currency             TEXT NOT NULL DEFAULT 'IRR',
    status               TEXT NOT NULL CHECK (status IN ('PENDING','SUCCEEDED','FAILED','EXPIRED')),
    refunded_amount      INTEGER NOT NULL DEFAULT 0,
    metadata_correlation_id TEXT,
    last_event_id        TEXT,
    last_event_body      TEXT,              -- exact signed body, re-sent on redelivery
    webhook_attempts     INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
