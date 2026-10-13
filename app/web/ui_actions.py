"""Form actions behind the console buttons. Each POST does real work through
the same services the API uses, then redirects with a flash message."""
from __future__ import annotations

from urllib.parse import parse_qs

from ..errors import ApiError
from ..ids import new_id
from ..services import cases, payments, reconciliation, refunds
from ..support import scenarios
from .framework import LabClient, Request, Router, redirect
from .ui import page_redirect

router = Router()


def _form(req: Request) -> dict:
    return {k: v[0] for k, v in parse_qs(req.body.decode("utf-8"), keep_blank_values=True).items()}


def _api(ctx, method, path, json_body=None, headers=None):
    h = {"X-API-Key": ctx.settings.api_key, "X-Correlation-ID": ctx.correlation_id}
    h.update(headers or {})
    return LabClient(ctx.app).request(method, path, json_body=json_body, headers=h)


@router.route("POST", "/ui/payments/{payment_id}/verify")
def ui_verify(req, ctx):
    pid = req.params["payment_id"]
    r = _api(ctx, "POST", f"/api/v1/payments/{pid}/verify")
    if r.status_code >= 400:
        return page_redirect(f"/payments/{pid}", err=r.json()["error"]["message_fa"])
    data = r.json()
    return page_redirect(f"/payments/{pid}", msg=f"verify انجام شد؛ وضعیت فعلی: {data['after']['payment_status']}")


@router.route("POST", "/ui/payments/{payment_id}/simulate")
def ui_simulate(req, ctx):
    pid = req.params["payment_id"]
    f = _form(req)
    r = _api(ctx, "POST", f"/api/v1/simulator/payments/{pid}/complete",
             json_body={"outcome": f.get("outcome", "succeeded"), "webhook": f.get("webhook", "deliver")})
    if r.status_code >= 400:
        return page_redirect(f"/payments/{pid}", err=r.json()["error"]["message_fa"])
    return page_redirect(f"/payments/{pid}", msg=f"شبیه‌ساز اجرا شد؛ webhook: {f.get('webhook', 'deliver')}")


@router.route("POST", "/ui/payments/{payment_id}/refund")
def ui_refund(req, ctx):
    pid = req.params["payment_id"]
    f = _form(req)
    try:
        amount = int(f.get("amount_irr", "").strip())
    except ValueError:
        return page_redirect(f"/payments/{pid}", err="مبلغ استرداد باید عدد صحیح ریال باشد")
    r = _api(ctx, "POST", f"/api/v1/payments/{pid}/refunds", json_body={"amount_irr": amount, "reason": f.get("reason") or None},
             headers={"Idempotency-Key": new_id("idem")})
    if r.status_code >= 400:
        return page_redirect(f"/payments/{pid}", err=r.json()["error"]["message_fa"])
    return page_redirect(f"/payments/{pid}", msg="استرداد آزمایشی ثبت و موفق شد")


@router.route("POST", "/ui/cases/{ident}/status")
def ui_case_status(req, ctx):
    ident = req.params["ident"]
    f = _form(req)
    body = {k: f.get(k, "") for k in ("resolution", "validation", "root_cause", "closure_summary", "merchant_message")}
    if "root_cause_status" in f:
        body["root_cause_status"] = f["root_cause_status"]
    body["status"] = f.get("status")
    try:
        cases.update_case(ctx, ident, body)
    except ApiError as exc:
        return page_redirect(f"/cases/{ident}", err=exc.message_fa)
    return page_redirect(f"/cases/{ident}", msg="پرونده ذخیره شد")


@router.route("POST", "/ui/cases/{ident}/notes")
def ui_case_note(req, ctx):
    ident = req.params["ident"]
    f = _form(req)
    try:
        cases.add_note(ctx, ident, {"body": f.get("body", ""), "author": "support", "note_type": "note"})
    except ApiError as exc:
        return page_redirect(f"/cases/{ident}", err=exc.message_fa)
    return page_redirect(f"/cases/{ident}", msg="یادداشت ثبت شد")


@router.route("POST", "/ui/diagnostics/{name}")
def ui_scenario(req, ctx):
    name = req.params["name"]
    if name not in scenarios.SCENARIOS:
        return page_redirect("/diagnostics", err="سناریو پیدا نشد")
    result = scenarios.run(ctx.app, name)
    msg = ("✔ " if result["ok"] else "✖ ") + result["summary"]
    target = f"/payments/{result['payment_id']}" if result.get("payment_id") else "/diagnostics"
    return page_redirect(target, msg=msg)


@router.route("POST", "/ui/reconciliation/demo")
def ui_recon_demo(req, ctx):
    from ..web.api import DEMO_CSV
    out = reconciliation.run_reconciliation(ctx, DEMO_CSV.read_text(encoding="utf-8"), DEMO_CSV.name)
    return page_redirect(f"/reconciliation/runs/{out['run']['id']}", msg="تطبیق اجرا شد")


@router.route("POST", "/ui/reconciliation/upload")
def ui_recon_upload(req, ctx):
    f = _form(req)
    text = f.get("csv", "")
    if not text.strip():
        return page_redirect("/reconciliation", err="متن CSV خالی است")
    try:
        out = reconciliation.run_reconciliation(ctx, text, "pasted.csv")
    except ApiError as exc:
        return page_redirect("/reconciliation", err=exc.message_fa)
    return page_redirect(f"/reconciliation/runs/{out['run']['id']}", msg="تطبیق اجرا شد")


@router.route("POST", "/ui/recon-exceptions/{exc_id}/case")
def ui_recon_case(req, ctx):
    exc = reconciliation.get_exception(ctx, req.params["exc_id"])
    try:
        case = cases.case_from_exception(ctx, exc)
        reconciliation.link_case(ctx, exc["id"], case["id"])
    except ApiError as exc2:
        return page_redirect(f"/reconciliation/runs/{exc['run_id']}", err=exc2.message_fa)
    return page_redirect(f"/cases/{case['case_number']}", msg="پرونده از مغایرت ساخته شد")


@router.route("POST", "/ui/recon-exceptions/{exc_id}/resolve")
def ui_recon_resolve(req, ctx):
    exc = reconciliation.get_exception(ctx, req.params["exc_id"])
    f = _form(req)
    try:
        reconciliation.resolve_exception(ctx, exc["id"], {"resolution_note": f.get("resolution_note", "")})
    except ApiError as e2:
        return page_redirect(f"/reconciliation/runs/{exc['run_id']}", err=e2.message_fa)
    return page_redirect(f"/reconciliation/runs/{exc['run_id']}", msg="مغایرت ثبت‌شده حل شد (داده مالی تغییر نکرد)")
