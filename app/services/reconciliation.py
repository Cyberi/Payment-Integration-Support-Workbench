"""Provider-report reconciliation (diagnostic only).

Compares a synthetic NavaPay CSV report with internal payments. It NEVER
deletes rows or changes money; it only creates exceptions that a person reviews.
"""
from __future__ import annotations

import csv
import io
import re

from ..database import row_to_dict, rows_to_dicts
from ..domain import money
from ..errors import ApiError, conflict, not_found, validation
from ..ids import new_id, now_iso, tehran_date
from .provider_sim import REPORT_COLUMNS

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
INT_RE = re.compile(r"^\d{1,15}$")
REF_RE = re.compile(r"^[A-Za-z0-9_\-]{3,40}$")
STATUS_MAP = {"SUCCEEDED": ("succeeded", "partially_refunded", "refunded"), "FAILED": ("failed",),
              "EXPIRED": ("expired",)}

GUIDE = {
    "internal_only": (
        "پرداخت در سامانه داخلی موفق است ولی در گزارش ارائه‌دهنده نیست. ابتدا verify کنید؛ سپس تاریخ گزارش و دوره تسویه را مقایسه کنید.",
        "پس از verify وضعیت provider برای همین provider_reference باید SUCCEEDED باشد و ردیف در گزارش دوره بعد ظاهر شود."),
    "provider_only": (
        "ردیفی در گزارش هست که پرداخت داخلی متناظر ندارد. با merchant_reference در سامانه جست‌وجو کنید و از ارائه‌دهنده جزئیات تراکنش را بخواهید.",
        "query 'payment_overview' با این شناسه نتیجه‌ای ندارد؛ پاسخ مکتوب ارائه‌دهنده درباره منشأ ردیف دریافت و در پرونده ثبت شود."),
    "amount_mismatch": (
        "مبلغ گزارش با مبلغ داخلی برابر نیست. واحد مبلغ (IRR/IRT) در پیکربندی adapter پذیرنده و مبلغ ارسالی (sent_amount) را بررسی کنید.",
        "مبلغ ارسالی ثبت‌شده در payments.sent_amount و واحد آن را با قرارداد ارائه‌دهنده (IRR) مقایسه کنید؛ پس از اصلاح پیکربندی یک پرداخت آزمایشی جدید باید بدون mismatch تطبیق شود."),
    "status_mismatch": (
        "وضعیت گزارش با وضعیت داخلی سازگار نیست. verify را اجرا و timeline پرداخت را برای webhook ردشده/نرسیده بررسی کنید.",
        "بعد از verify وضعیت داخلی با وضعیت provider یکی باشد و در timeline رویداد payment.status_changed ثبت شده باشد."),
    "duplicate_provider_reference": (
        "یک provider_reference بیش از یک بار در گزارش آمده است. هیچ ردیفی را حذف نکنید؛ تکرار را با شماره ردیف به ارائه‌دهنده اطلاع دهید.",
        "ارائه‌دهنده گزارش اصلاح‌شده بدهد یا تأیید کند که مبلغ فقط یک بار تسویه شده است؛ تطبیق مجدد نباید duplicate نشان دهد."),
}


def parse_report(text: str, settings) -> list[dict]:
    if len(text.encode("utf-8")) > settings.csv_max_bytes:
        raise ApiError(413, "CSV_TOO_LARGE", f"CSV larger than {settings.csv_max_bytes} bytes",
                       f"حجم فایل بیش از حد مجاز ({settings.csv_max_bytes // 1000} کیلوبایت) است")
    text = text.lstrip("\ufeff")
    if not text.strip():
        raise validation([{"field": "file", "issue": "empty CSV"}], "CSV is empty", "فایل CSV خالی است")
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip() for h in (reader.fieldnames or [])]
    missing = [c for c in REPORT_COLUMNS if c not in header]
    extra = [c for c in header if c not in REPORT_COLUMNS]
    if missing or extra:
        raise validation([{"field": "header", "issue": f"missing={missing} unexpected={extra}"}],
                         "CSV columns do not match the NavaPay report format",
                         "ستون‌های فایل با قالب گزارش NavaPay هم‌خوان نیست: " + ", ".join(REPORT_COLUMNS))
    rows, errors = [], []
    for i, raw in enumerate(reader, start=2):
        if len(rows) >= settings.csv_max_rows:
            raise validation([{"field": "rows", "issue": f"more than {settings.csv_max_rows} rows"}],
                             "too many rows", "تعداد ردیف‌ها بیش از حد مجاز است")
        r = {k.strip(): (v or "").strip() for k, v in raw.items() if k}
        problems = []
        if not REF_RE.match(r["provider_reference"]):
            problems.append("provider_reference")
        if r["merchant_reference"] and not REF_RE.match(r["merchant_reference"]):
            problems.append("merchant_reference")
        if not DATE_RE.match(r["transaction_date"]):
            problems.append("transaction_date (YYYY-MM-DD)")
        if not INT_RE.match(r["amount"]):
            problems.append("amount (integer)")
        if not INT_RE.match(r["refunded_amount"] or "0"):
            problems.append("refunded_amount (integer)")
        if r["currency"] not in money.RIAL_PER_UNIT:
            problems.append("currency (IRR/IRT)")
        if r["status"] not in STATUS_MAP:
            problems.append("status (SUCCEEDED/FAILED/EXPIRED)")
        if problems:
            errors.append({"row": i, "issue": "invalid " + ", ".join(problems)})
            continue
        r["amount"] = int(r["amount"])
        r["refunded_amount"] = int(r["refunded_amount"] or 0)
        r["csv_row"] = i
        rows.append(r)
    if errors:
        raise validation(errors[:20], f"{len(errors)} invalid CSV row(s)",
                         f"{len(errors)} ردیف نامعتبر در فایل وجود دارد؛ شماره ردیف‌ها در جزئیات آمده است")
    if not rows:
        raise validation([{"field": "rows", "issue": "no data rows"}], "CSV has no rows", "فایل هیچ ردیف داده‌ای ندارد")
    return rows


def _add_exc(ctx, run_id, etype, *, row=None, payment=None, ref=None, expected=None, actual=None):
    suggestion, verification = GUIDE[etype]
    exc_id = new_id("rex")
    ctx.conn.execute(
        "INSERT INTO reconciliation_exceptions (id, run_id, exception_type, provider_reference, payment_id,"
        " csv_row, expected, actual, suggestion, verification, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (exc_id, run_id, etype, ref, payment and payment["id"], row and row["csv_row"], expected, actual,
         suggestion, verification, now_iso()))
    return exc_id


def run_reconciliation(ctx, text: str, filename: str) -> dict:
    rows = parse_report(text, ctx.settings)
    run_id = new_id("rec")
    ctx.conn.execute("INSERT INTO reconciliation_runs (id, source_filename, row_count, matched_count,"
                     " exception_count, correlation_id, created_at) VALUES (?,?,?,?,?,?,?)",
                     (run_id, filename[:120], len(rows), 0, 0, ctx.correlation_id, now_iso()))
    seen: set[str] = set()
    matched_payment_ids: set[str] = set()
    matched = 0
    counts: dict[str, int] = {}

    def bump(t):
        counts[t] = counts.get(t, 0) + 1

    for row in rows:
        ref = row["provider_reference"]
        if ref in seen:
            _add_exc(ctx, run_id, "duplicate_provider_reference", row=row, ref=ref,
                     expected="one row per provider_reference", actual=f"repeated at CSV row {row['csv_row']}")
            bump("duplicate_provider_reference")
            continue
        seen.add(ref)
        p = row_to_dict(ctx.conn.execute("SELECT * FROM payments WHERE provider_reference = ?", (ref,)).fetchone())
        if p is None and row["merchant_reference"]:
            # e.g. timeout case: provider has a reference we never received
            p = row_to_dict(ctx.conn.execute("SELECT * FROM payments WHERE id = ? AND provider_reference IS NULL",
                                             (row["merchant_reference"],)).fetchone())
        if p is None:
            _add_exc(ctx, run_id, "provider_only", row=row, ref=ref, expected="internal payment",
                     actual=f"{row['status']} {row['amount']:,} {row['currency']} merchant_reference={row['merchant_reference'] or '-'}")
            bump("provider_only")
            continue
        matched_payment_ids.add(p["id"])
        report_irr = money.to_irr(row["amount"], row["currency"])
        if report_irr != p["amount_irr"]:
            _add_exc(ctx, run_id, "amount_mismatch", row=row, payment=p, ref=ref,
                     expected=f"{p['amount_irr']:,} IRR", actual=f"{row['amount']:,} {row['currency']} (= {report_irr:,} IRR)")
            bump("amount_mismatch")
        elif p["status"] not in STATUS_MAP[row["status"]]:
            _add_exc(ctx, run_id, "status_mismatch", row=row, payment=p, ref=ref,
                     expected=f"internal status compatible with {row['status']}", actual=f"internal status {p['status']}")
            bump("status_mismatch")
        else:
            matched += 1

    dates = sorted(r["transaction_date"] for r in rows)
    lo, hi = dates[0], dates[-1]
    for p in rows_to_dicts(ctx.conn.execute(
            "SELECT * FROM payments WHERE status IN ('succeeded','partially_refunded','refunded')"
            " AND provider_reference IS NOT NULL").fetchall()):
        if p["id"] in matched_payment_ids:
            continue
        if lo <= tehran_date(p["updated_at"]) <= hi or lo <= tehran_date(p["created_at"]) <= hi:
            _add_exc(ctx, run_id, "internal_only", payment=p, ref=p["provider_reference"],
                     expected=f"row in provider report {lo}..{hi}", actual="missing from report")
            bump("internal_only")

    total = sum(counts.values())
    ctx.conn.execute("UPDATE reconciliation_runs SET matched_count = ?, exception_count = ? WHERE id = ?",
                     (matched, total, run_id))
    ctx.log("WARNING" if total else "INFO", "reconciliation.completed",
            f"run {run_id}: rows={len(rows)} matched={matched} exceptions={total} {counts}")
    return {"run": get_run(ctx, run_id), "exception_counts": counts}


def get_run(ctx, run_id: str) -> dict:
    r = row_to_dict(ctx.conn.execute("SELECT * FROM reconciliation_runs WHERE id = ?", (run_id,)).fetchone())
    if r is None:
        raise not_found("reconciliation run", run_id)
    return r


def list_runs(ctx, limit=20) -> list[dict]:
    return rows_to_dicts(ctx.conn.execute("SELECT * FROM reconciliation_runs ORDER BY created_at DESC LIMIT ?",
                                          (limit,)).fetchall())


def list_exceptions(ctx, run_id=None, status=None, exception_type=None) -> list[dict]:
    sql, args = ["SELECT e.*, c.case_number FROM reconciliation_exceptions e"
                 " LEFT JOIN support_cases c ON c.id = e.case_id WHERE 1=1"], []
    for col, val in (("e.run_id", run_id), ("e.status", status), ("e.exception_type", exception_type)):
        if val:
            sql.append(f"AND {col} = ?")
            args.append(val)
    sql.append("ORDER BY e.created_at, e.csv_row")
    return rows_to_dicts(ctx.conn.execute(" ".join(sql), args).fetchall())


def get_exception(ctx, exc_id: str) -> dict:
    r = row_to_dict(ctx.conn.execute("SELECT * FROM reconciliation_exceptions WHERE id = ?", (exc_id,)).fetchone())
    if r is None:
        raise not_found("reconciliation exception", exc_id)
    return r


def link_case(ctx, exc_id: str, case_id: str) -> None:
    exc = get_exception(ctx, exc_id)
    if exc["status"] == "resolved":
        raise conflict("EXCEPTION_RESOLVED", "exception already resolved", "این مغایرت قبلاً حل شده است")
    ctx.conn.execute("UPDATE reconciliation_exceptions SET case_id = ?, status = 'linked' WHERE id = ?",
                     (case_id, exc_id))
    ctx.audit("reconciliation.exception_linked", "reconciliation_exception", exc_id, detail=case_id,
              payment_id=exc["payment_id"])


def resolve_exception(ctx, exc_id: str, body) -> dict:
    from ..validation import Validator
    v = Validator(body)
    note = v.str("resolution_note", max_len=1000, min_len=10)
    v.done()
    exc = get_exception(ctx, exc_id)
    if exc["status"] == "resolved":
        raise conflict("EXCEPTION_RESOLVED", "exception already resolved", "این مغایرت قبلاً حل شده است")
    ctx.conn.execute("UPDATE reconciliation_exceptions SET status = 'resolved', resolution_note = ?, resolved_at = ?"
                     " WHERE id = ?", (note, now_iso(), exc_id))
    ctx.audit("reconciliation.exception_resolved", "reconciliation_exception", exc_id, detail=note,
              payment_id=exc["payment_id"])
    ctx.log("INFO", "reconciliation.exception_resolved", f"{exc_id} resolved (no financial data changed)",
            payment_id=exc["payment_id"], provider_reference=exc["provider_reference"])
    return get_exception(ctx, exc_id)
