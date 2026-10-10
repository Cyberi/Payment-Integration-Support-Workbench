"""REST API: /api/v1/...  (contract mirrored in Postman + docs/curl-examples.md)"""
from __future__ import annotations

import hmac

from ..config import BASE_DIR
from ..database import rows_to_dicts
from ..errors import ApiError, bad_request, not_found
from ..support import queries as q
from ..support import scenarios
from ..support.runbooks import RUNBOOKS
from ..services import (cases, dashboard, orders, payments, provider_sim, reconciliation, refunds,
                        timeline, webhooks)
from .framework import LabClient, Response, Router, json_response

DEMO_CSV = BASE_DIR / "data" / "examples" / "provider-transactions-demo.csv"
router = Router()


def require_api_key(req, ctx):
    key = req.header("x-api-key")
    if not key:
        raise ApiError(401, "API_KEY_MISSING", "X-API-Key header is required", "هدر X-API-Key ارسال نشده است")
    if not hmac.compare_digest(key, ctx.settings.api_key):
        raise ApiError(401, "API_KEY_INVALID", "X-API-Key is not valid for this environment",
                       "کلید API برای این محیط معتبر نیست؛ تنظیمات محیط (lab/test) را بررسی کنید")


def ok(ctx, data, status=200, headers=None):
    if isinstance(data, dict) and "correlation_id" not in data:
        data = dict(data, correlation_id=ctx.correlation_id)
    return json_response(data, status, headers)


# --- health & reference data ------------------------------------------------
@router.route("GET", "/api/v1/health")
def health(req, ctx):
    ctx.conn.execute("SELECT 1").fetchone()
    return ok(ctx, {"status": "ok", "mode": "simulation-only", "internal_currency": "IRR"})


@router.route("GET", "/api/v1/merchants")
def merchants(req, ctx):
    return ok(ctx, {"merchants": rows_to_dicts(ctx.conn.execute("SELECT * FROM merchants ORDER BY id"))})


# --- orders --------------------------------------------------------------------
@router.route("POST", "/api/v1/orders")
def create_order(req, ctx):
    require_api_key(req, ctx)
    return ok(ctx, {"order": orders.order_view(orders.create_order(ctx, req.json()))}, 201)


@router.route("GET", "/api/v1/orders/{order_id}")
def get_order(req, ctx):
    require_api_key(req, ctx)
    order = orders.get_order(ctx, req.params["order_id"])
    ctx.adopt_correlation(order["correlation_id"])
    ctx.note_ids(order_id=order["id"])
    pays = [payments.payment_view(p) for p in rows_to_dicts(
        ctx.conn.execute("SELECT * FROM payments WHERE order_id = ? ORDER BY created_at", (order["id"],)))]
    return ok(ctx, {"order": orders.order_view(order), "payments": pays})


@router.route("POST", "/api/v1/orders/{order_id}/cancel")
def cancel_order(req, ctx):
    require_api_key(req, ctx)
    return ok(ctx, {"order": orders.order_view(orders.cancel_order(ctx, req.params["order_id"]))})


# --- payments ------------------------------------------------------------------
@router.route("POST", "/api/v1/payments")
def create_payment(req, ctx):
    require_api_key(req, ctx)
    simulate = req.header("x-lab-simulate")
    if simulate not in (None, "", "provider_timeout"):
        raise bad_request("UNKNOWN_SIMULATION", "X-Lab-Simulate supports only 'provider_timeout'",
                          "مقدار X-Lab-Simulate معتبر نیست")
    status, body, replayed = payments.create_payment(ctx, req.json(), req.header("idempotency-key"), simulate or None)
    return ok(ctx, body, status, [("Idempotent-Replayed", "true" if replayed else "false")])


@router.route("GET", "/api/v1/payments")
def list_payments(req, ctx):
    require_api_key(req, ctx)
    rows = payments.list_payments(ctx, q=req.query.get("q"), status=req.query.get("status"),
                                  date_from=req.query.get("from"), date_to=req.query.get("to"))
    return ok(ctx, {"payments": [payments.payment_view(r) | {"order_status": r["order_status"]} for r in rows]})


@router.route("GET", "/api/v1/payments/{payment_id}")
def get_payment(req, ctx):
    require_api_key(req, ctx)
    p = payments.get_payment(ctx, req.params["payment_id"])
    ctx.adopt_correlation(p["correlation_id"])
    ctx.note_ids(payment_id=p["id"], order_id=p["order_id"], provider_reference=p["provider_reference"])
    order = orders.get_order(ctx, p["order_id"])
    return ok(ctx, {"payment": payments.payment_view(p), "order": orders.order_view(order),
                    "refunds": refunds.list_refunds(ctx, p["id"])})


@router.route("POST", "/api/v1/payments/{payment_id}/verify")
def verify(req, ctx):
    require_api_key(req, ctx)
    simulate = req.header("x-lab-simulate")
    return ok(ctx, payments.verify_payment(ctx, req.params["payment_id"],
                                           "provider_timeout" if simulate == "provider_timeout" else None))


@router.route("POST", "/api/v1/payments/{payment_id}/refunds")
def create_refund(req, ctx):
    require_api_key(req, ctx)
    status, body, replayed = refunds.create_refund(ctx, req.params["payment_id"], req.json(),
                                                   req.header("idempotency-key"))
    return ok(ctx, body, status, [("Idempotent-Replayed", "true" if replayed else "false")])


# --- webhook (merchant endpoint called by NavaPay) ------------------------------
@router.route("POST", "/api/v1/webhooks/navapay")
def navapay_webhook(req, ctx):
    status, body = webhooks.handle_navapay(ctx, req.body, req.header("x-navapay-signature"))
    return ok(ctx, body, status)


# --- NavaPay simulator controls -------------------------------------------------
WEBHOOK_MODES = ("deliver", "skip", "duplicate", "invalid_signature")


def _sim_err(exc: provider_sim.SimError) -> ApiError:
    return ApiError(exc.status, f"SIM_{exc.code}", exc.message, "خطای شبیه‌ساز ارائه‌دهنده: " + exc.message)


def _deliver(ctx, txn: dict, raw: bytes, secret: str) -> dict:
    """Send one webhook to the merchant endpoint (in-process HTTP call)."""
    ctx.conn.commit()  # release the write lock before the nested request
    attempts = provider_sim.count_attempt(ctx.conn, txn["provider_reference"])
    ctx.conn.commit()
    headers = {"Content-Type": "application/json", "X-NavaPay-Signature": provider_sim.sign(raw, secret)}
    corr = (txn.get("metadata_correlation_id") or "")
    if corr:
        headers["X-Correlation-ID"] = corr
    r = LabClient(ctx.app).post("/api/v1/webhooks/navapay", data=raw, headers=headers)
    return {"attempt": attempts, "http_status": r.status_code, "response": r.json()}


@router.route("POST", "/api/v1/simulator/payments/{payment_id}/complete")
def sim_complete(req, ctx):
    from ..validation import Validator
    body = req.json()
    v = Validator(body)
    outcome = v.str("outcome", choices=("succeeded", "failed", "expired"))
    mode = v.str("webhook", required=False, choices=WEBHOOK_MODES) or "deliver"
    v.done()
    pid = req.params["payment_id"]
    ctx.note_ids(payment_id=pid)
    try:
        txn, event_id, raw = provider_sim.complete(ctx.conn, merchant_reference=pid, outcome=outcome)
    except provider_sim.SimError as exc:
        raise _sim_err(exc)
    full = ctx.conn.execute("SELECT * FROM sim_provider_transactions WHERE merchant_reference = ?", (pid,)).fetchone()
    ctx.adopt_correlation(full["metadata_correlation_id"])
    ctx.note_ids(provider_reference=txn["provider_reference"])
    deliveries = []
    if mode in ("deliver", "duplicate"):
        for _ in range(2 if mode == "duplicate" else 1):
            deliveries.append(_deliver(ctx, dict(full), raw, ctx.settings.webhook_secret))
    elif mode == "invalid_signature":
        deliveries.append(_deliver(ctx, dict(full), raw, provider_sim.STALE_SECRET_FOR_LAB))
    ctx.log("INFO", "simulator.completed", f"NavaPay sim: {outcome}, webhook={mode}, deliveries={len(deliveries)}",
            payment_id=pid, provider_reference=txn["provider_reference"])
    return ok(ctx, {"provider_transaction": provider_sim.get_by_merchant_reference(ctx.conn, pid),
                    "event_id": event_id, "webhook_mode": mode, "deliveries": deliveries})


@router.route("POST", "/api/v1/simulator/payments/{payment_id}/redeliver-webhook")
def sim_redeliver(req, ctx):
    pid = req.params["payment_id"]
    ctx.note_ids(payment_id=pid)
    try:
        txn, event_id, raw = provider_sim.last_event(ctx.conn, pid)
    except provider_sim.SimError as exc:
        raise _sim_err(exc)
    full = dict(ctx.conn.execute("SELECT * FROM sim_provider_transactions WHERE merchant_reference = ?", (pid,)).fetchone())
    ctx.adopt_correlation(full["metadata_correlation_id"])
    if full["webhook_attempts"] >= ctx.settings.max_webhook_delivery_attempts:
        ctx.log("WARNING", "simulator.max_attempts", f"redelivery refused after {full['webhook_attempts']} attempts",
                payment_id=pid, provider_reference=full["provider_reference"])
        raise ApiError(409, "MAX_DELIVERY_ATTEMPTS_REACHED",
                       f"webhook already attempted {full['webhook_attempts']} times (limit {ctx.settings.max_webhook_delivery_attempts})",
                       "سقف تعداد ارسال webhook پر شده است؛ وضعیت را با verify بررسی کنید")
    delivery = _deliver(ctx, full, raw, ctx.settings.webhook_secret)
    return ok(ctx, {"event_id": event_id, "delivery": delivery,
                    "provider_transaction": provider_sim.get_by_merchant_reference(ctx.conn, pid)})


@router.route("GET", "/api/v1/simulator/payments/{payment_id}")
def sim_view(req, ctx):
    txn = provider_sim.get_by_merchant_reference(ctx.conn, req.params["payment_id"])
    if txn is None:
        raise not_found("NavaPay transaction", req.params["payment_id"])
    return ok(ctx, {"provider_transaction": txn})


@router.route("GET", "/api/v1/simulator/reports/transactions.csv")
def sim_report(req, ctx):
    text = provider_sim.rows_to_csv(provider_sim.export_report_rows(ctx.conn))
    return Response(200, text, "text/csv; charset=utf-8",
                    [("Content-Disposition", 'attachment; filename="navapay-report.csv"')])


# --- evidence: timeline & logs ---------------------------------------------------
@router.route("GET", "/api/v1/timeline")
def get_timeline(req, ctx):
    pid, corr = req.query.get("payment_id"), req.query.get("correlation_id")
    if pid:
        payments.get_payment(ctx, pid)
    items = timeline.build_timeline(ctx, pid, corr)
    return ok(ctx, {"filter": {"payment_id": pid, "correlation_id": corr}, "count": len(items), "items": items})


LOG_FILTERS = ("correlation_id", "payment_id", "order_id", "provider_reference", "level", "event_type", "status_code")


def query_logs(ctx, filters: dict, limit=200) -> list[dict]:
    sql, args = ["SELECT * FROM app_logs WHERE 1=1"], []
    for f in LOG_FILTERS:
        val = filters.get(f)
        if val:
            if f == "status_code":
                if not str(val).isdigit():
                    raise bad_request("INVALID_FILTER", "status_code must be numeric", "status_code باید عدد باشد")
                sql.append("AND status_code = ?"); args.append(int(val))
            else:
                sql.append(f"AND {f} = ?"); args.append(val)
    if filters.get("errors_only"):
        sql.append("AND (level IN ('ERROR','WARNING') OR status_code >= 400)")
    sql.append("ORDER BY id DESC LIMIT ?"); args.append(limit)
    return rows_to_dicts(ctx.conn.execute(" ".join(sql), args).fetchall())


@router.route("GET", "/api/v1/logs")
def get_logs(req, ctx):
    limit = req.query.get("limit", "200")
    limit = int(limit) if limit.isdigit() and 0 < int(limit) <= 1000 else 200
    return ok(ctx, {"logs": query_logs(ctx, req.query, limit)})


# --- support cases -------------------------------------------------------------
@router.route("GET", "/api/v1/support/cases")
def list_cases(req, ctx):
    return ok(ctx, {"cases": cases.list_cases(ctx, req.query.get("status"), req.query.get("severity"),
                                              req.query.get("open") == "true", req.query.get("q"))})


@router.route("POST", "/api/v1/support/cases")
def create_case(req, ctx):
    return ok(ctx, {"case": cases.create_case(ctx, req.json())}, 201)


@router.route("GET", "/api/v1/support/cases/{case_id}")
def get_case(req, ctx):
    return ok(ctx, {"case": cases.get_case(ctx, req.params["case_id"])})


@router.route("PATCH", "/api/v1/support/cases/{case_id}")
def patch_case(req, ctx):
    return ok(ctx, {"case": cases.update_case(ctx, req.params["case_id"], req.json())})


@router.route("POST", "/api/v1/support/cases/{case_id}/notes")
def add_note(req, ctx):
    return ok(ctx, {"case": cases.add_note(ctx, req.params["case_id"], req.json())}, 201)


@router.route("GET", "/api/v1/support/queries")
def list_queries(req, ctx):
    return ok(ctx, {"queries": [{"name": k, "title_fa": v["title_fa"], "params": v["params"]} for k, v in q.QUERIES.items()]})


@router.route("GET", "/api/v1/support/queries/{name}")
def run_query(req, ctx):
    return ok(ctx, q.run_query(ctx, req.params["name"], req.query))


@router.route("GET", "/api/v1/support/runbooks")
def list_runbooks(req, ctx):
    return ok(ctx, {"runbooks": RUNBOOKS})


# --- reconciliation ------------------------------------------------------------
@router.route("POST", "/api/v1/reconciliation/runs")
def recon_run(req, ctx):
    ctype = (req.header("content-type") or "").split(";")[0].strip()
    if ctype not in ("text/csv", "text/plain"):
        raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "send the CSV as the raw body with Content-Type: text/csv",
                       "فایل CSV را با Content-Type: text/csv ارسال کنید")
    try:
        text = req.body.decode("utf-8")
    except UnicodeDecodeError:
        raise bad_request("CSV_ENCODING", "CSV must be UTF-8", "فایل CSV باید با encoding UTF-8 باشد")
    return ok(ctx, reconciliation.run_reconciliation(ctx, text, req.query.get("filename", "upload.csv")), 201)


@router.route("POST", "/api/v1/reconciliation/runs/demo")
def recon_demo(req, ctx):
    return ok(ctx, reconciliation.run_reconciliation(ctx, DEMO_CSV.read_text(encoding="utf-8"), DEMO_CSV.name), 201)


@router.route("GET", "/api/v1/reconciliation/runs")
def recon_runs(req, ctx):
    return ok(ctx, {"runs": reconciliation.list_runs(ctx)})


@router.route("GET", "/api/v1/reconciliation/runs/{run_id}")
def recon_run_detail(req, ctx):
    run = reconciliation.get_run(ctx, req.params["run_id"])
    return ok(ctx, {"run": run, "exceptions": reconciliation.list_exceptions(ctx, run_id=run["id"])})


@router.route("GET", "/api/v1/reconciliation/exceptions")
def recon_exceptions(req, ctx):
    return ok(ctx, {"exceptions": reconciliation.list_exceptions(ctx, req.query.get("run_id"), req.query.get("status"),
                                                                 req.query.get("exception_type"))})


@router.route("POST", "/api/v1/reconciliation/exceptions/{exception_id}/case")
def recon_case(req, ctx):
    exc = reconciliation.get_exception(ctx, req.params["exception_id"])
    if exc["case_id"]:
        raise ApiError(409, "CASE_ALREADY_LINKED", "exception already linked to a case", "این مغایرت قبلاً به پرونده وصل شده است",
                       [{"case_id": exc["case_id"]}])
    case = cases.case_from_exception(ctx, exc)
    reconciliation.link_case(ctx, exc["id"], case["id"])
    return ok(ctx, {"case": cases.get_case(ctx, case["id"])}, 201)


@router.route("POST", "/api/v1/reconciliation/exceptions/{exception_id}/resolve")
def recon_resolve(req, ctx):
    return ok(ctx, {"exception": reconciliation.resolve_exception(ctx, req.params["exception_id"], req.json())})


# --- diagnostics & dashboard -------------------------------------------------------
@router.route("GET", "/api/v1/diagnostics/scenarios")
def list_scenarios(req, ctx):
    return ok(ctx, {"scenarios": [{"name": k, "title_fa": v["title"], "runbook": v["runbook"]}
                                  for k, v in scenarios.SCENARIOS.items()]})


@router.route("POST", "/api/v1/diagnostics/scenarios/{name}")
def run_scenario(req, ctx):
    name = req.params["name"]
    if name not in scenarios.SCENARIOS:
        raise not_found("scenario", name)
    ctx.conn.commit()
    result = scenarios.run(ctx.app, name)
    ctx.adopt_correlation(result["correlation_id"])
    ctx.note_ids(payment_id=result.get("payment_id"), order_id=result.get("order_id"))
    return ok(ctx, result, 201)


@router.route("GET", "/api/v1/dashboard/metrics")
def metrics(req, ctx):
    return ok(ctx, {"metrics": dashboard.metrics(ctx)})
