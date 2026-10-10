"""Support case workflow.

Closure rules (enforced, not just documented):
 * resolved -> needs resolution + validation, and root cause either confirmed
   (with text) or explicitly marked 'unknown'.
 * closed   -> needs to be resolved first, plus closure_summary and merchant_message.
"""
from __future__ import annotations

import json

from ..database import rows_to_dicts
from ..domain.states import CASE, OPEN_CASE_STATUSES, InvalidTransition, ensure_transition
from ..errors import ApiError, not_found, validation
from ..ids import new_id, now_iso
from ..validation import Validator

SEVERITIES = ("P1", "P2", "P3", "P4")
PROBLEM_TYPES = ("webhook_missing", "webhook_duplicate", "provider_timeout", "amount_mismatch",
                 "webhook_signature", "idempotency_conflict", "state_inconsistency", "reconciliation",
                 "refund", "api_error", "other")
TEXT_FIELDS = ("title", "business_impact", "merchant_report", "reported_at", "required_data",
               "data_not_to_request", "priority_reason", "initial_checks", "root_cause", "resolution",
               "authority_boundary", "validation", "merchant_message", "closure_summary", "payment_id",
               "order_id", "correlation_id", "runbook_slug")
EVIDENCE_KINDS = ("api_response", "timeline", "logs", "query", "webhook_event", "payment",
                  "reconciliation_exception", "provider_view")


def _decode(case: dict) -> dict:
    case = dict(case)
    case["evidence"] = json.loads(case.pop("evidence_json") or "[]")
    case["hypotheses"] = json.loads(case.pop("hypotheses_json") or "[]")
    return case


def get_case(ctx, ident: str) -> dict:
    row = ctx.conn.execute("SELECT * FROM support_cases WHERE id = ? OR case_number = ?", (ident, ident)).fetchone()
    if row is None:
        raise not_found("support case", ident)
    case = _decode(dict(row))
    case["notes"] = rows_to_dicts(ctx.conn.execute(
        "SELECT * FROM case_notes WHERE case_id = ? ORDER BY created_at, id", (case["id"],)).fetchall())
    case["reconciliation_exceptions"] = rows_to_dicts(ctx.conn.execute(
        "SELECT id, exception_type, provider_reference, status FROM reconciliation_exceptions WHERE case_id = ?",
        (case["id"],)).fetchall())
    return case


def list_cases(ctx, status=None, severity=None, open_only=False, q=None) -> list[dict]:
    sql, args = ["SELECT id, case_number, title, problem_type, severity, status, business_impact, payment_id,"
                 " correlation_id, created_at, updated_at FROM support_cases WHERE 1=1"], []
    if status:
        sql.append("AND status = ?"); args.append(status)
    if severity:
        sql.append("AND severity = ?"); args.append(severity)
    if open_only:
        sql.append(f"AND status IN ({','.join('?' * len(OPEN_CASE_STATUSES))})"); args += OPEN_CASE_STATUSES
    if q:
        sql.append("AND (case_number = ? OR payment_id = ? OR correlation_id = ? OR title LIKE ?)")
        args += [q, q, q, f"%{q}%"]
    sql.append("ORDER BY CASE severity WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 WHEN 'P3' THEN 3 ELSE 4 END, created_at DESC")
    return rows_to_dicts(ctx.conn.execute(" ".join(sql), args).fetchall())


def _validate_lists(v: Validator, data: dict):
    ev = data.get("evidence")
    if ev is not None:
        if not isinstance(ev, list) or not all(isinstance(e, dict) and e.get("kind") in EVIDENCE_KINDS
                                               and isinstance(e.get("label"), str) for e in ev):
            v._err("evidence", f"list of {{kind, label, ref}}; kind in {EVIDENCE_KINDS}")
    hy = data.get("hypotheses")
    if hy is not None:
        if not isinstance(hy, list) or not all(isinstance(h, dict) and isinstance(h.get("hypothesis"), str) for h in hy):
            v._err("hypotheses", "list of {hypothesis, confirm_if, reject_if, result}")


def _next_number(ctx) -> str:
    row = ctx.conn.execute("SELECT MAX(CAST(SUBSTR(case_number, 6) AS INTEGER)) FROM support_cases").fetchone()
    return f"CASE-{(row[0] or 1000) + 1}"


def create_case(ctx, body, actor="support") -> dict:
    v = Validator(body)
    v.str("title", max_len=200)
    v.str("problem_type", choices=PROBLEM_TYPES)
    v.str("severity", choices=SEVERITIES)
    v.str("business_impact", max_len=1000)
    v.str("merchant_report", max_len=3000)
    for f in TEXT_FIELDS:
        if f not in ("title", "business_impact", "merchant_report"):
            v.str(f, required=False, max_len=4000)
    v.str("root_cause_status", required=False, choices=("not_determined", "confirmed", "unknown"))
    _validate_lists(v, body)
    v.done()
    now = now_iso()
    case_id = new_id("case")
    fields = {f: body.get(f) for f in TEXT_FIELDS}
    ctx.conn.execute(
        f"INSERT INTO support_cases (id, case_number, problem_type, severity, status, root_cause_status,"
        f" evidence_json, hypotheses_json, created_at, updated_at, {', '.join(TEXT_FIELDS)})"
        f" VALUES (?,?,?,?,?,?,?,?,?,?,{','.join('?' * len(TEXT_FIELDS))})",
        (case_id, _next_number(ctx), body["problem_type"], body["severity"], "new",
         body.get("root_cause_status") or "not_determined",
         json.dumps(body.get("evidence") or [], ensure_ascii=False),
         json.dumps(body.get("hypotheses") or [], ensure_ascii=False), now, now,
         *[fields[f] for f in TEXT_FIELDS]))
    ctx.audit("case.created", "support_case", case_id, to_status="new", payment_id=body.get("payment_id"),
              order_id=body.get("order_id"))
    ctx.log("INFO", "case.created", f"support case created severity={body['severity']}",
            payment_id=body.get("payment_id"))
    return get_case(ctx, case_id)


def _blank(x) -> bool:
    return x is None or (isinstance(x, str) and not x.strip())


def update_case(ctx, ident: str, body, actor="support") -> dict:
    case = get_case(ctx, ident)
    v = Validator(body)
    for f in TEXT_FIELDS:
        v.str(f, required=False, max_len=4000)
    v.str("root_cause_status", required=False, choices=("not_determined", "confirmed", "unknown"))
    v.str("severity", required=False, choices=SEVERITIES)
    target = v.str("status", required=False, choices=tuple(CASE))
    _validate_lists(v, body)
    v.done()

    merged = dict(case)
    for f in TEXT_FIELDS + ("root_cause_status", "severity"):
        if f in body:
            merged[f] = body[f]
    if "evidence" in body:
        merged["evidence"] = body["evidence"]
    if "hypotheses" in body:
        merged["hypotheses"] = body["hypotheses"]

    if target and target != case["status"]:
        try:
            ensure_transition("case", case["status"], target)
        except InvalidTransition:
            raise ApiError(409, "INVALID_CASE_TRANSITION", f"case cannot move {case['status']} -> {target}",
                           f"تغییر وضعیت پرونده از {case['status']} به {target} مجاز نیست")
        missing = []
        if target in ("resolved", "closed"):
            for f in ("resolution", "validation"):
                if _blank(merged.get(f)):
                    missing.append(f)
            if merged.get("root_cause_status") == "not_determined":
                missing.append("root_cause_status (confirmed یا unknown)")
            if merged.get("root_cause_status") == "confirmed" and _blank(merged.get("root_cause")):
                missing.append("root_cause")
        if target == "closed":
            for f in ("closure_summary", "merchant_message"):
                if _blank(merged.get(f)):
                    missing.append(f)
        if missing:
            raise validation([{"field": m, "issue": "required before this status"} for m in missing],
                             f"case cannot be {target} yet",
                             "برای این وضعیت ابتدا این موارد را کامل کنید: " + "، ".join(missing))
    sets = {f: merged.get(f) for f in TEXT_FIELDS + ("root_cause_status", "severity")}
    sets["evidence_json"] = json.dumps(merged["evidence"], ensure_ascii=False)
    sets["hypotheses_json"] = json.dumps(merged["hypotheses"], ensure_ascii=False)
    sets["status"] = target or case["status"]
    sets["updated_at"] = now_iso()
    ctx.conn.execute(f"UPDATE support_cases SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                     (*sets.values(), case["id"]))
    if target and target != case["status"]:
        _note(ctx, case["id"], actor, "status_change", f"{case['status']} → {target}")
        ctx.audit("case.status_changed", "support_case", case["id"], from_status=case["status"], to_status=target,
                  payment_id=case["payment_id"], order_id=case["order_id"])
        ctx.log("INFO", "case.status_changed", f"{case['case_number']} {case['status']} -> {target}",
                payment_id=case["payment_id"])
    if "validation" in body and body.get("validation") and body.get("validation") != case.get("validation"):
        _note(ctx, case["id"], actor, "validation", body["validation"])
    return get_case(ctx, case["id"])


def _note(ctx, case_id, author, note_type, text):
    ctx.conn.execute("INSERT INTO case_notes (case_id, author, note_type, body, created_at) VALUES (?,?,?,?,?)",
                     (case_id, author, note_type, text, now_iso()))


def add_note(ctx, ident: str, body) -> dict:
    case = get_case(ctx, ident)
    v = Validator(body)
    author = v.str("author", required=False, max_len=60) or "support"
    note_type = v.str("note_type", required=False, choices=("note", "evidence", "validation", "merchant_message")) or "note"
    text = v.str("body", max_len=4000)
    v.done()
    _note(ctx, case["id"], author, note_type, text)
    ctx.conn.execute("UPDATE support_cases SET updated_at = ? WHERE id = ?", (now_iso(), case["id"]))
    return get_case(ctx, case["id"])


def case_from_exception(ctx, exc: dict) -> dict:
    labels = {"internal_only": "پرداخت موفق داخلی در گزارش ارائه‌دهنده نیست",
              "provider_only": "ردیف گزارش ارائه‌دهنده بدون پرداخت داخلی",
              "amount_mismatch": "مغایرت مبلغ بین گزارش و پرداخت داخلی",
              "status_mismatch": "مغایرت وضعیت بین گزارش و پرداخت داخلی",
              "duplicate_provider_reference": "ردیف تکراری در گزارش ارائه‌دهنده"}
    case = create_case(ctx, {
        "title": f"تطبیق: {labels[exc['exception_type']]} ({exc['provider_reference'] or exc['payment_id']})",
        "problem_type": "reconciliation", "severity": "P3",
        "business_impact": "اختلاف در گزارش تراکنش؛ تا روشن شدن علت، تسویه این ردیف قابل تأیید نیست.",
        "merchant_report": "مغایرت به‌صورت خودکار در اجرای تطبیق ایجاد شده است (گزارش پذیرنده ندارد).",
        "payment_id": exc["payment_id"], "runbook_slug": "reconciliation-exceptions",
        "initial_checks": exc["suggestion"],
        "evidence": [{"kind": "reconciliation_exception", "label": f"{exc['exception_type']} ردیف {exc['csv_row'] or '-'}",
                      "ref": {"exception_id": exc["id"], "run_id": exc["run_id"]}}],
    })
    return case
