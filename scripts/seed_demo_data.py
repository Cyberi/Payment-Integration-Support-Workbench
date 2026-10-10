"""Build the demo database from scratch.

Steps (all through the real API, so the seeded evidence is what the app really produces):
  1. two synthetic merchants
  2. run the scenarios that create payments, webhooks, refunds, timeouts, conflicts
  3. export the simulator report, inject the reconciliation anomalies, write the demo CSV
  4. run the reconciliation scenario on that CSV
  5. open one support case per incident, linked to the payment it came from

    python -m scripts.seed_demo_data
"""
from __future__ import annotations

import csv
import io
import sys

from app import ids
from app.config import BASE_DIR, load_settings
from app.database import connect, reset_db
from app.services import provider_sim
from app.support import scenarios
from app.web.framework import LabClient
from app.main import build_app

DEMO_CSV = BASE_DIR / "data" / "examples" / "provider-transactions-demo.csv"
MERCHANTS = [
    ("mer_sefid", "Sefid Market (demo storefront)", "IRR", "فروشگاه نمونه؛ داده ساختگی"),
    ("mer_daneshyar_irt", "Daneshyar Shop (sandbox, adapter set to IRT)", "IRT",
     "پیکربندی آزمایشی با واحد تومان برای سناریوی مغایرت واحد"),
]

# Support case content. Evidence refs are filled from the real scenario run.
CASE_TEXT = {
    "webhook-missing": dict(title="سفارش pending ماند با وجود پرداخت موفق (webhook نرسیده)", problem_type="webhook_missing",
        severity="P2", status="resolved", runbook="webhook-missing",
        business_impact="مشتری پول پرداخت کرده ولی سفارش ارسال نمی‌شود؛ ریسک تماس و لغو.",
        merchant_report="مشتری می‌گوید پیامک بانکی موفق دارد، ولی در پنل سفارش هنوز «در انتظار پرداخت» است.",
        priority_reason="P2: یک سفارش، ولی با ادعای مشتری و احتمال تکرار در همان endpoint.",
        required_data="payment_id یا order_id؛ زمان تقریبی پرداخت.",
        data_not_to_request="شماره کارت، CVV2، رمز پویا یا secret پذیرنده.",
        initial_checks="وضعیت payment/order را خواندیم؛ webhook موفق برای این پرداخت ثبت نشده بود.",
        hypotheses=[{"hypothesis": "webhook به سامانه نرسیده", "confirm_if": "هیچ رویداد processed در provider_events", "reject_if": "رویداد rejected/failed وجود داشته باشد", "result": "تأیید شد"}],
        root_cause="webhook موفق ارسال نشده بود (در lab با ارسال skip شبیه‌سازی شد). وضعیت داخلی تا verify عقب مانده بود.",
        resolution="verify از ارائه‌دهنده؛ پاسخ SUCCEEDED؛ پرداخت succeeded و سفارش paid شد.",
        authority_boundary="پشتیبان فقط verify می‌کند و وضعیت را از پاسخ رسمی می‌گیرد؛ تغییر دستی وضعیت انجام نمی‌شود.",
        validation="verify دوم بدون تغییر بود (no_status_change_needed). timeline رویداد payment.status_changed با منبع verify را نشان داد.",
        merchant_message="پرداخت سفارش شما از سمت ارائه‌دهنده تأیید شد و سفارش «پرداخت‌شده» شد. علت تأخیر، نرسیدن اعلان خودکار بود."),
    "duplicate-webhook": dict(title="webhook تکراری و نگرانی از پرداخت دوباره", problem_type="webhook_duplicate",
        severity="P3", status="closed", runbook="duplicate-webhook",
        business_impact="اگر دوباره اثر داشت، سفارش دو بار پردازش یا کالا دو بار ارسال می‌شد.",
        merchant_report="در لاگ دو بار همان رویداد دیده می‌شود؛ می‌خواهیم مطمئن شویم پرداخت دو بار ثبت نشده.",
        priority_reason="P3: اثر مالی نداشته؛ نگرانی و شواهد لازم است.",
        required_data="payment_id یا event_id.", data_not_to_request="بدنه کامل webhook همراه امضا.",
        initial_checks="query duplicate_webhook_events را اجرا کردیم.",
        hypotheses=[{"hypothesis": "race یا retry ارائه‌دهنده", "confirm_if": "دو تحویل با یک event_id", "reject_if": "دو processed", "result": "تأیید: یک processed + یک duplicate"}],
        root_cause="ارائه‌دهنده رویداد را دو بار ارسال کرد؛ سامانه با event_id یکتا فقط یک بار پردازش کرد.",
        resolution="هیچ اصلاحی لازم نبود؛ شاهد (processed=1، duplicate=1) برای پذیرنده آماده شد.",
        authority_boundary="حذف ردیف duplicate ممنوع؛ شاهد است.",
        validation="ارسال مجدد همان رویداد: result=duplicate و هیچ گذار جدیدی ثبت نشد.",
        closure_summary="اثر تکراری نداشت؛ شواهد ثبت شد.", merchant_message="رویداد پرداخت سفارش شما دو بار رسید ولی فقط یک بار اعمال شد؛ نیازی به اقدام نیست."),
    "timeout-unknown": dict(title="timeout در ایجاد پرداخت؛ وضعیت نامشخص", problem_type="provider_timeout",
        severity="P2", status="investigating", runbook="timeout-unknown",
        business_impact="مشتری ممکن است دو بار پول داده باشد یا سفارش گیر کرده باشد.",
        merchant_report="خطای زمان پاسخ گرفتیم؛ نمی‌دانیم پرداخت انجام شده یا نه.",
        priority_reason="P2: احتمال کسر مبلغ بدون سفارش.",
        required_data="payment_id یا order_id، زمان خطا.", data_not_to_request="شماره کارت یا مشخصات بانکی مشتری.",
        initial_checks="وضعیت unknown است و review_reason=provider_timeout؛ تلاش دوم با ACTIVE_PAYMENT_EXISTS بلاک شد.",
        hypotheses=[{"hypothesis": "ارائه‌دهنده تراکنش را ساخته ولی پاسخ گم شده", "confirm_if": "verify رکورد PENDING/SUCCEEDED بدهد", "reject_if": "verify رکورد نداشته باشد", "result": "رکورد PENDING پیدا شد"}],
        root_cause="", root_cause_status="not_determined",
        resolution="در انتظار نتیجه نهایی؛ verify انجام شد و پرداخت pending است.",
        authority_boundary="failed کردن دستی ممنوع؛ منتظر پاسخ قطعی یا ارجاع.",
        merchant_message="وضعیت پرداخت سفارش شما از ارائه‌دهنده استعلام شد و در حال نهایی‌شدن است. مبلغی دوبار برداشت نمی‌شود."),
    "amount-unit-mismatch": dict(title="مبلغ ارسالی به ارائه‌دهنده یک‌دهم مبلغ سفارش", problem_type="amount_mismatch",
        severity="P1", status="escalated", runbook="amount-unit-mismatch",
        business_impact="پرداخت‌های موفق نزد ارائه‌دهنده سفارش را paid نمی‌کنند؛ مغایرت مالی در حال تجمع است.",
        merchant_report="بعد از پرداخت موفق، سفارش paid نمی‌شود و مبلغ گزارش کم است.",
        priority_reason="P1: پیکربندی اشتباه هنوز فعال است و پرداخت‌های جدید را هم خراب می‌کند.",
        required_data="نام پذیرنده، payment_id نمونه، provider_amount_unit فعلی.", data_not_to_request="هیچ secret یا کلید پیکربندی.",
        initial_checks="sent_amount=25,000 تومان (IRT) در برابر amount_irr=250,000؛ ارائه‌دهنده همان عدد را IRR خوانده.",
        hypotheses=[{"hypothesis": "واحد IRT در adapter تنظیم شده", "confirm_if": "sent_amount = amount_irr/10", "reject_if": "sent_amount برابر amount_irr", "result": "تأیید شد"}],
        root_cause="provider_amount_unit پذیرنده روی IRT تنظیم شده بود (تنظیم آزمایشی).",
        resolution="escalate به تیم یکپارچه‌سازی برای اصلاح پیکربندی و تصمیم درباره پرداخت‌های انجام‌شده.",
        authority_boundary="اصلاح مبلغ در دیتابیس یا تصمیم مالی درباره مابه‌التفاوت خارج از اختیار پشتیبانی است.",
        merchant_message="در بررسی پرداخت سفارش شما، تنظیم واحد مبلغ اشتباه بود. تا اصلاح، سفارش‌ها را ارسال نکنید؛ موضوع به تیم مربوط ارجاع شد."),
    "invalid-signature": dict(title="webhook با امضای نامعتبر رد شد (HTTP 401)", problem_type="webhook_signature",
        severity="P2", status="resolved", runbook="invalid-signature",
        business_impact="webhook پذیرفته نمی‌شد و سفارش‌ها در pending می‌ماندند.",
        merchant_report="ارائه‌دهنده می‌گوید ارسال شده، ولی سامانه ما 401 داده و سفارش pending مانده.",
        priority_reason="P2: مسیر اصلی اعلان پرداخت خراب بود.",
        required_data="payment_id یا provider_reference و زمان ارسال.", data_not_to_request="secret یا کلید امضا (هرگز).",
        initial_checks="rejected_webhooks را اجرا کردیم؛ امضا با secret ناهمخوان بود.",
        hypotheses=[{"hypothesis": "secret دو طرف یکی نیست", "confirm_if": "امضا با secret فعلی تطبیق نمی‌کند", "reject_if": "امضا درست باشد", "result": "تأیید شد"}],
        root_cause="secret ارسال‌کننده با secret فعلی پذیرنده یکی نبود (در lab: کلید قدیمی).",
        resolution="پذیرنده secret را از مسیر امن هم‌سان کرد؛ ارسال مجدد با امضای درست پذیرفته شد.",
        authority_boundary="غیرفعال‌کردن بررسی امضا ممنوع؛ secret در تیکت دریافت نمی‌شود.",
        validation="redeliver با امضای درست: result=processed؛ payment=succeeded.",
        closure_summary="علت ناهمخوانی کلید بود؛ پس از هم‌سانی، webhook پذیرفته شد.",
        merchant_message="اعلان‌های پرداخت به دلیل ناهماهنگی کلید امضا رد می‌شد. پس از هم‌سانی، سفارش پرداخت‌شده است. کلید را در تیکت یا چت نفرستید."),
    "idempotency-conflict": dict(title="تکرار Idempotency-Key با بدنه متفاوت", problem_type="idempotency_conflict",
        severity="P3", status="closed", runbook="idempotency-conflict",
        business_impact="در صورت اشتباه پیاده‌سازی، پرداخت دوم ساخته می‌شد.",
        merchant_report="پس از قطعی، درخواست را دوباره فرستادیم و خطای 409 گرفتیم؛ آیا پرداخت دوم ساخته شده؟",
        priority_reason="P3: شواهد نشان می‌دهد پرداخت تکراری ساخته نشده.",
        required_data="Idempotency-Key و زمان درخواست‌ها.", data_not_to_request="بدنه کامل با اطلاعات کارت (اصلاً ارسال نشود).",
        initial_checks="idempotency_key_usage را اجرا کردیم؛ کلید یکسان با بدنه یکسان یک پرداخت ساخت.",
        hypotheses=[{"hypothesis": "پذیرنده کلید را با بدنه متفاوت دوباره استفاده کرده", "confirm_if": "409 با request_hash متفاوت", "reject_if": "بدنه یکسان", "result": "تأیید شد"}],
        root_cause="همان کلید برای بدنه متفاوت استفاده شده بود (رفتار مستند و درست).",
        resolution="قاعده برای پذیرنده توضیح داده شد: یک کلید = یک عملیات.",
        authority_boundary="حذف رکورد idempotency برای باز کردن کلید ممنوع است.",
        validation="تکرار با همان کلید و بدنه: همان payment_id و تعداد پرداخت = 1.",
        closure_summary="رفتار مطابق مستندات بود؛ پرداخت دوم ساخته نشده.",
        merchant_message="درخواست دوم پرداخت جدیدی نساخت. برای هر عملیات جدید کلید جدید بسازید."),
    "order-payment-conflict": dict(title="پرداخت موفق روی سفارش لغوشده", problem_type="state_inconsistency",
        severity="P2", status="waiting_for_merchant", runbook="order-payment-conflict",
        business_impact="مشتری پول داده ولی سفارش لغو است؛ باید تصمیم برگشت یا احیا گرفته شود.",
        merchant_report="مشتری پرداخت کرده، سفارش در پنل لغوشده است. کدام درست است؟",
        priority_reason="P2: پول کسر شده؛ تصمیم پذیرنده لازم است.",
        required_data="order_id، payment_id.", data_not_to_request="اطلاعات کارت یا رمز.",
        initial_checks="order_payment_conflicts را اجرا کردیم؛ لغو قبل از پایان پرداخت باز ثبت شده بود.",
        hypotheses=[{"hypothesis": "لغو سفارش بین شروع و موفقیت پرداخت رخ داده", "confirm_if": "زمان لغو قبل از موفقیت", "reject_if": "لغو بعد از موفقیت", "result": "تأیید شد"}],
        root_cause="پذیرنده سفارش را لغو کرد در حالی که تلاش پرداخت باز بود.",
        resolution="دو گزینه به پذیرنده داده شد: refund کامل یا احیای سفارش. منتظر تصمیم مکتوب.",
        authority_boundary="تغییر cancelled به paid توسط پشتیبانی ممنوع؛ refund بدون تصمیم پذیرنده ممنوع.",
        merchant_message="مشتری سفارش را پس از لغو پرداخت کرده است. لطفاً اعلام کنید سفارش را احیا می‌کنید یا برگشت وجه انجام شود."),
    "refund-limit": dict(title="درخواست استرداد بیش از مانده قابل استرداد", problem_type="refund",
        severity="P4", status="resolved", runbook="refund-limit",
        business_impact="استرداد دوم ثبت نشد؛ تأثیری روی مبلغ مشتری نداشت.",
        merchant_report="خطای 422 برای استرداد گرفتیم.", priority_reason="P4: سقف استرداد درست عمل کرد.",
        required_data="payment_id و مبلغ.", data_not_to_request="—",
        initial_checks="payment_overview: مبلغ 250,000، استرداد قبلی 100,000.",
        hypotheses=[{"hypothesis": "مبلغ بیش از مانده است", "confirm_if": "مجموع > مانده", "reject_if": "مجموع ≤ مانده", "result": "تأیید شد"}],
        root_cause="مبلغ درخواستی بیش از مانده (150,000 ریال) بود.", root_cause_status="confirmed",
        resolution="مبلغ به مانده اصلاح شود.", authority_boundary="دور زدن سقف ممنوع.",
        validation="استرداد 100,000 موفق؛ استرداد 200,000 با 422 رد شد؛ وضعیت partially_refunded.",
        merchant_message="سقف استرداد برای این پرداخت ۱۵۰,۰۰۰ ریال است. لطفاً مبلغ را اصلاح کنید."),
}


def _seed_merchants(conn):
    now = ids.now_iso()
    for mid, name, unit, note in MERCHANTS:
        conn.execute("INSERT INTO merchants (id, name, provider_amount_unit, note, created_at) VALUES (?,?,?,?,?)",
                     (mid, name, unit, note, now))
    conn.commit()


def _write_demo_csv(settings) -> int:
    conn = connect(settings.db_path)
    rows = provider_sim.export_report_rows(conn)
    by_ref = {}
    for r in rows:
        by_ref.setdefault(r["merchant_reference"], r)
    conn.close()
    # Injected anomalies (synthetic on purpose):
    rows = [r for r in rows if r["merchant_reference"] != _ref_of(by_ref, "duplicate-webhook")]          # internal_only
    dup_src = dict(by_ref[_ref_of(by_ref, "invalid-signature")])
    rows.append(dup_src)                                                                                  # duplicate reference
    status_src = by_ref[_ref_of(by_ref, "order-payment-conflict")]
    for r in rows:
        if r is status_src or r["merchant_reference"] == status_src["merchant_reference"]:
            r["status"] = "FAILED"                                                                        # status_mismatch
    rows.append({"provider_reference": "NP-7FA3C09D12", "merchant_reference": "pay_legacy_000042",
                 "transaction_date": rows[0]["transaction_date"], "amount": 120000, "currency": "IRR",
                 "status": "SUCCEEDED", "refunded_amount": 0})                                         # provider_only
    cols = provider_sim.REPORT_COLUMNS
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r[k] for k in cols})
    DEMO_CSV.parent.mkdir(parents=True, exist_ok=True)
    DEMO_CSV.write_text(buf.getvalue(), encoding="utf-8")
    return len(rows)


def _ref_of(by_ref, scenario_name):
    """Map a scenario to the merchant_reference (payment id) it created, via the last run."""
    return _RUN_PAYMENTS[scenario_name]


_RUN_PAYMENTS: dict[str, str] = {}


def _case_evidence(res: dict, scenario: str) -> list[dict]:
    ev = [{"kind": "api_response", "label": f"{s['step']} → HTTP {s['http_status']}", "ref": s["path"]}
          for s in res["steps"]][:6]
    ev.append({"kind": "timeline", "label": "خط زمانی همان correlation_id", "ref": res["correlation_id"]})
    ev.append({"kind": "query", "label": "payment_overview / webhook_events_for_payment", "ref": res.get("payment_id")})
    return ev


def seed(settings) -> dict:
    ids.set_deterministic(20260610)  # reproducible IDs for the committed demo data
    reset_db(settings.db_path)
    app = build_app(settings)
    conn = connect(settings.db_path)
    _seed_merchants(conn)
    conn.close()
    client = LabClient(app)  # used only to read back after runs

    results = {}
    for name in scenarios.SCENARIOS:
        if name == "reconciliation":
            continue
        results[name] = scenarios.run(app, name)
        _RUN_PAYMENTS[name] = results[name].get("payment_id")

    _write_demo_csv(settings)
    results["reconciliation"] = scenarios.run(app, "reconciliation")

    # Support cases: one per incident, opened and walked through the real API.
    api_key = settings.api_key
    h = {"X-API-Key": api_key, "X-Correlation-ID": "corr_seed_support_case"}
    made = 0
    for name, text in CASE_TEXT.items():
        res = results[name]
        body = {k: v for k, v in text.items() if k not in ("status", "runbook", "hypotheses", "root_cause_status")}
        body["runbook_slug"] = text["runbook"]
        body["payment_id"] = res.get("payment_id")
        body["order_id"] = res.get("order_id")
        body["correlation_id"] = res["correlation_id"]
        body["evidence"] = _case_evidence(res, name)
        body["hypotheses"] = text["hypotheses"]
        body["root_cause_status"] = text.get("root_cause_status", "confirmed" if text.get("root_cause") else "not_determined")
        if not text.get("root_cause"):
            body.pop("root_cause", None)
        r = client.request("POST", "/api/v1/support/cases", json_body=body, headers=h)
        assert r.status_code == 201, r.text
        case = r.json()["case"]
        paths = {"new": [], "investigating": ["investigating"], "waiting_for_merchant": ["waiting_for_merchant"],
                 "escalated": ["escalated"], "resolved": ["investigating", "resolved"],
                 "closed": ["investigating", "resolved", "closed"]}
        for st in paths[text["status"]]:
            patch = {"status": st}
            if st in ("resolved", "closed"):
                patch.update({k: text[k] for k in ("resolution", "validation")})
                patch["root_cause_status"] = "confirmed" if text.get("root_cause") else "unknown"
                if text.get("root_cause"):
                    patch["root_cause"] = text["root_cause"]
            if st == "closed":
                patch.update({k: text[k] for k in ("closure_summary", "merchant_message")})
            r = client.request("PATCH", f"/api/v1/support/cases/{case['case_number']}", json_body=patch, headers=h)
            assert r.status_code == 200, (name, st, r.text)
            case = r.json()["case"]
        made += 1

    ok_count = sum(1 for r in results.values() if r["ok"])
    conn = connect(settings.db_path)
    run_row = conn.execute("SELECT exception_count FROM reconciliation_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    conn.close()
    return {"merchants": len(MERCHANTS), "scenarios": len(results), "scenarios_ok": ok_count, "cases": made,
            "exceptions": run_row[0] if run_row else 0}


if __name__ == "__main__":
    s = load_settings()
    print(seed(s))
    sys.exit(0)
