"""Regenerate docs/scenario-runbooks.md from app/support/runbooks.py (single source of truth).

    python -m scripts.export_runbooks
"""
from __future__ import annotations

from app.config import BASE_DIR
from app.support.runbooks import RUNBOOKS

OUT = BASE_DIR / "docs" / "scenario-runbooks.md"

ISSUE_SAMPLES = """## نمونه‌های Bug / Incident

این سه نمونه از اجرای واقعی سناریوهای همین lab ساخته شده‌اند (نتیجه‌ها از خروجی `scripts/seed_demo_data.py` و تست‌ها). هیچ باگ تاریخی ادعا نمی‌شود؛ هدف نشان‌دادن شکل گزارش است.

### INC-01: پرداخت موفق نزد ارائه‌دهنده، سفارش هنوز pending
- **Summary:** پرداخت نزد NavaPay موفق شده، ولی سفارش در `pending_payment` است چون webhook دریافت نشده.
- **Environment:** lab محلی، Python 3.12، SQLite، `python -m scripts.reset_demo_data`.
- **Reproduction steps:** ۱) سفارش و پرداخت بسازید. ۲) `POST /api/v1/simulator/payments/{payment_id}/complete` با `{"outcome":"succeeded","webhook":"skip"}`. ۳) `GET /api/v1/payments/{payment_id}`. ۴) `POST .../verify`.
- **Expected result:** قبل از verify، وضعیت تا روشن شدن باقی بماند؛ بعد از verify، پرداخت `succeeded` و سفارش `paid`.
- **Actual result (lab run):** قبل از verify: `pending` / `pending_payment`؛ بعد از verify: `succeeded` / `paid`.
- **Evidence:** خروجی سناریوی `webhook-missing`؛ خط زمانی پرداخت.
- **Root cause:** در lab، ارسال webhook عمداً حذف شده است (`webhook: skip`). علت در سامانه واقعی باید از سمت ارائه‌دهنده یا endpoint بررسی شود؛ lab فقط مسیر بازیابی (verify) را نشان می‌دهد.
- **Resolution:** استعلام verify از ارائه‌دهنده؛ تغییر وضعیت فقط بر اساس پاسخ رسمی.
- **Regression test:** `tests/test_payment_flows.py::test_happy_path_end_to_end` (مسیر عادی) و سناریوی `webhook-missing` در تست‌های دستی.

### INC-02: تلاش پرداخت دوم وقتی پرداخت اول unknown است
- **Summary:** بعد از timeout، ساخت پرداخت دوم برای همان سفارش نباید مجاز باشد.
- **Environment:** lab محلی، هدر `X-Lab-Simulate: provider_timeout`.
- **Reproduction steps:** ۱) `POST /api/v1/payments` با `X-Lab-Simulate: provider_timeout` (پاسخ 202). ۲) `POST /api/v1/payments` دوم برای همان سفارش.
- **Expected result:** HTTP 409 با کد `ACTIVE_PAYMENT_EXISTS` و راهنمای verify.
- **Actual result (lab run):** HTTP 409 با `ACTIVE_PAYMENT_EXISTS`؛ پرداخت اول `unknown` باقی ماند.
- **Evidence:** خروجی سناریوی `timeout-unknown`.
- **Root cause:** not applicable (رفتار مورد انتظار طراحی است).
- **Resolution:** not applicable.
- **Regression test:** سناریوی `timeout-unknown` و بخش ۸ از پرامپت (timeout و unknown تا verify).

### INC-03: مبلغ ارسالی به ارائه‌دهنده یک‌دهم مبلغ سفارش
- **Summary:** پذیرنده‌ای با واحد تومان تنظیم شده است؛ مبلغ ارسالی ۲۵,۰۰۰ بوده و ارائه‌دهنده آن را ریال خوانده.
- **Environment:** پذیرنده `mer_daneshyar_irt` در داده نمونه.
- **Reproduction steps:** ۱) سفارش ۲۵۰,۰۰۰ ریال برای این پذیرنده. ۲) پرداخت بسازید و `sent_amount` را بخوانید. ۳) پرداخت موفق را با webhook عادی ارسال کنید.
- **Expected result:** مبلغ ارسالی با واحد صریح باشد؛ مغایرت پرچم شود؛ پرداخت به `succeeded` نرود.
- **Actual result (lab run):** `sent_amount` = 25,000 با واحد IRT؛ webhook با `failed`؛ پرداخت `pending` با `review_reason=amount_mismatch`.
- **Evidence:** خروجی سناریوی `amount-unit-mismatch`؛ پرس‌وجوی `amount_unit_check`.
- **Root cause (تست پیکربندی):** واحد adapter روی IRT تنظیم شده است. این یک آزمایش پیکربندی است و ادعا نمی‌شود درگاه‌های واقعی چنین رفتاری دارند.
- **Resolution:** پیکربندی باید اصلاح شود و تصمیم درباره پرداخت‌های انجام‌شده به تیم مربوط ارجاع شود؛ پشتیبان مبلغ را دستی اصلاح نمی‌کند.
- **Regression test:** `tests/test_payment_flows.py::test_unit_misconfiguration_is_flagged_not_marked_paid`.
"""


def render() -> str:
    parts = ["# Scenario Runbooks", "",
             "> تولیدشده از `app/support/runbooks.py` با `python -m scripts.export_runbooks`. منبع اصلی همان فایل است.", ""]
    for r in RUNBOOKS:
        parts += [f"## {r['title']}", f"**slug:** `{r['slug']}`", "",
                  f"- **نشانه:** {r['symptom']}",
                  f"- **شرط اجرا:** {r['applies_when']}",
                  f"- **داده لازم:** {r['required_data']}",
                  "- **بررسی‌ها (قبل از هر تغییر):**"]
        parts += [f"  {i}. {c}" for i, c in enumerate(r["checks"], 1)]
        parts += ["- **query / API:** " + ", ".join(f"`{q}`" for q in r["queries"]),
                  "- **APIها:** " + "؛ ".join(f"`{a}`" for a in r["apis"]),
                  "- **اقدام مجاز:**"] + [f"  - {a}" for a in r["allowed_actions"]]
        parts += ["- **اقدام ممنوع:**"] + [f"  - {a}" for a in r["not_allowed"]]
        parts += [f"- **شرط escalation:** {r['escalation']}",
                  f"- **روش verification:** {r['verification']}",
                  f"- **پیام نمونه به پذیرنده:** {r['merchant_message']}", ""]
    parts.append(ISSUE_SAMPLES)
    return "\n".join(parts)


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(), encoding="utf-8")
    print(f"wrote {OUT}")
