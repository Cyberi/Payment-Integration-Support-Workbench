"""Persian RTL support console. Server-rendered HTML, no CDN, no JavaScript needed.
All user/provider text goes through html.escape (no raw HTML injection)."""
from __future__ import annotations

from html import escape as e

from ..database import rows_to_dicts
from ..domain.money import format_amount
from ..ids import to_tehran_display
from ..services import cases, dashboard, payments, reconciliation, timeline
from ..support import queries as qmod
from ..support import scenarios
from ..support.runbooks import BY_SLUG, RUNBOOKS
from .framework import Request, Response, Router, html_response, redirect

router = Router()
STATUS_FA = {
    "created": "ایجاد شده", "pending": "در انتظار", "succeeded": "موفق", "failed": "ناموفق", "unknown": "نامشخص",
    "expired": "منقضی", "partially_refunded": "استرداد جزئی", "refunded": "مسترد شده", "pending_payment": "در انتظار پرداخت",
    "paid": "پرداخت‌شده", "cancelled": "لغو شده", "new": "جدید", "investigating": "در بررسی",
    "waiting_for_merchant": "منتظر پذیرنده", "escalated": "ارجاع‌شده", "resolved": "حل‌شده", "closed": "بسته‌شده",
    "open": "باز", "linked": "به پرونده وصل", "processed": "پردازش‌شده", "duplicate": "تکراری", "rejected": "ردشده",
    "requested": "درخواست‌شده", "matched": "تطبیق", "received": "دریافت‌شده",
}
REVIEW_FA = {"provider_timeout": "timeout با نتیجه نامشخص", "amount_mismatch": "مغایرت مبلغ",
             "order_state_conflict": "ناسازگاری سفارش", "provider_record_missing": "رکورد ارائه‌دهنده نیست",
             "state_conflict": "تعارض وضعیت"}
EXC_FA = {"internal_only": "فقط داخلی", "provider_only": "فقط در گزارش ارائه‌دهنده", "amount_mismatch": "مغایرت مبلغ",
          "status_mismatch": "مغایرت وضعیت", "duplicate_provider_reference": "شناسه ارائه‌دهنده تکراری"}

DO_NOT = "شماره کامل کارت، CVV2، رمز، secret یا API key"

CSS = """
*{box-sizing:border-box} body{margin:0;font-family:Tahoma,'Noto Sans Arabic',sans-serif;background:#f4f6f9;color:#1d2330;line-height:1.8}
header{background:#1f3a5f;color:#fff;padding:10px 20px;display:flex;flex-wrap:wrap;gap:14px;align-items:center}
header a{color:#fff;text-decoration:none;opacity:.9} header a.on{opacity:1;font-weight:bold;border-bottom:2px solid #ffd166}
.banner{background:#fff4d6;border-bottom:1px solid #e8c766;padding:8px 20px;font-size:14px}
main{padding:18px 20px;max-width:1200px;margin:auto} h1{font-size:22px;margin:4px 0 12px} h2{font-size:17px;margin:20px 0 8px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.card{background:#fff;border:1px solid #dde3ec;border-radius:8px;padding:12px 14px}
.card b{display:block;font-size:26px;color:#1f3a5f} .card span{font-size:13px;color:#4a5568}
table{width:100%;border-collapse:collapse;background:#fff;font-size:14px} th,td{border-bottom:1px solid #e5e9f0;padding:7px 8px;text-align:right;vertical-align:top}
th{background:#eef2f7} .ltr{direction:ltr;text-align:left;font-family:Consolas,monospace;font-size:13px;word-break:break-all}
.tag{display:inline-block;padding:1px 9px;border-radius:12px;font-size:12px;border:1px solid #98a2b3;background:#fff}
.sev-P1{background:#fde2e2;border-color:#c53030;color:#7a1f1f;font-weight:bold} .sev-P2{background:#fdebd0;border-color:#b7791f;color:#6b4a0f}
.sev-P3{background:#e6f0fb;border-color:#2b6cb0} .sev-P4{background:#eef2f7}
.st-failed,.st-rejected,.st-escalated{background:#fde2e2} .st-unknown{background:#fdebd0} .st-succeeded,.st-paid,.st-resolved,.st-processed{background:#e3f6e8}
.st-pending,.st-investigating,.st-pending_payment,.st-open,.st-linked{background:#e6f0fb} .st-duplicate{background:#f1ebfb}
.flash{background:#e3f6e8;border:1px solid #38a169;padding:8px 12px;border-radius:6px;margin-bottom:12px}
.err{background:#fde2e2;border:1px solid #c53030;padding:8px 12px;border-radius:6px;margin-bottom:12px}
.panel{background:#fff;border:1px solid #dde3ec;border-radius:8px;padding:12px 14px;margin-bottom:14px}
form.inline{display:inline} input,select,textarea{font:inherit;padding:5px 7px;border:1px solid #aab4c3;border-radius:5px;background:#fff}
textarea{width:100%;min-height:70px} button,.btn{font:inherit;padding:6px 12px;border:1px solid #1f3a5f;border-radius:6px;background:#1f3a5f;color:#fff;cursor:pointer;text-decoration:none;display:inline-block}
button.sec,.btn.sec{background:#fff;color:#1f3a5f} button.warn{background:#9b2c2c;border-color:#9b2c2c}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px} @media(max-width:800px){.grid2{grid-template-columns:1fr}} 
.muted{color:#4a5568;font-size:13px} pre{background:#101827;color:#e2e8f0;padding:10px;border-radius:6px;overflow:auto;direction:ltr;text-align:left;font-size:12px}
.wrap{overflow-x:auto} label{display:block;margin:4px 0 2px;font-size:13px}
"""

NAV = [("/", "داشبورد", "dash"), ("/payments", "پرداخت‌ها و سفارش‌ها", "pay"), ("/cases", "پرونده‌های پشتیبانی", "cases"),
       ("/diagnostics", "عیب‌یابی", "diag"), ("/reconciliation", "تطبیق گزارش", "rec"), ("/runbooks", "راهنماها", "rb")]


def page(title, body, active="", flash=None, error=None) -> Response:
    nav = "".join(f'<a href="{u}" class="{"on" if k == active else ""}">{e(t)}</a>' for u, t, k in NAV)
    msg = ""
    if flash:
        msg += f'<div class="flash">{e(flash)}</div>'
    if error:
        msg += f'<div class="err">{e(error)}</div>'
    html = f"""<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)} | آزمایشگاه پشتیبانی پرداخت</title>
<style>{CSS}</style></head><body>
<header><strong>آزمایشگاه پشتیبانی و عیب‌یابی یکپارچه‌سازی پرداخت پذیرندگان</strong><nav style="display:flex;gap:12px;flex-wrap:wrap">{nav}</nav></header>
<div class="banner">محیط آزمایشی با <b>داده ساختگی</b> (Demo/Synthetic Data). هیچ درگاه، بانک یا سرویس بیرونی به برنامه متصل نیست. زمان‌ها به وقت تهران (UTC+03:30) نمایش داده می‌شوند.</div>
<main>{msg}{body}</main></body></html>"""
    return html_response(html)


def money(v, unit="IRR"):
    return format_amount(v, unit)


def tag(value, kind=None, labels=STATUS_FA):
    label = labels.get(value, value) if value else "-"
    return f'<span class="tag st-{e(str(value))}">{e(label)}</span>'


def ltr(v):
    return f'<span class="ltr">{e(str(v if v is not None else "-"))}</span>'


def redirect_back(req: Request, msg=None, err=None):
    path = req.header("x-ui-return") or "/"
    return page_redirect(path, msg, err)


def page_redirect(path, msg=None, err=None):
    params = {}
    if msg:
        params["msg"] = msg
    if err:
        params["err"] = err
    return redirect(path, **params)


def html_error(exc, corr: str) -> str:
    return page("خطا", f'<div class="err">{e(exc.message_fa)}</div><p class="muted">کد: {e(exc.code)} | correlation_id: <span class="ltr">{e(corr)}</span></p>'
                f'<p><a class="btn sec" href="/">بازگشت به داشبورد</a></p>', error=None).body.decode("utf-8")


def _flash(req):
    return req.query.get("msg"), req.query.get("err")


# ---------------------------------------------------------------- pages
@router.route("GET", "/")
def dash(req, ctx):
    m = dashboard.metrics(ctx)
    cards = [("پرداخت‌های نیازمند بررسی", m["payments_needing_review"]), ("پرداخت نامشخص (unknown)", m["payments_unknown"]),
             ("پرونده‌های باز", m["open_cases"]), ("پرونده‌های P1/P2 باز", m["p1_p2_open_cases"]),
             ("webhook ردشده", m["webhooks_rejected"]), ("webhook تکراری", m["webhooks_duplicate"]),
             ("webhook ناموفق", m["webhooks_failed"]), ("مغایرت باز تطبیق (آخرین اجرا)", m["open_reconciliation_exceptions"]),
             ("خطاهای HTTP ثبت‌شده", m["api_errors"])]
    cards_html = "".join(f'<div class="card"><b>{v}</b><span>{e(k)}</span></div>' for k, v in cards)
    review = payments.list_payments(ctx, review_only=True, limit=10)
    rows = "".join(f'<tr><td><a href="/payments/{e(p["id"])}">{ltr(p["id"])}</a></td><td>{tag(p["status"])}</td>'
                   f'<td>{e(REVIEW_FA.get(p["review_reason"], "-"))}</td><td>{money(p["amount_irr"])}</td></tr>' for p in review)
    open_cases = cases.list_cases(ctx, open_only=True)[:8]
    crow = "".join(f'<tr><td><a href="/cases/{e(c["case_number"])}">{ltr(c["case_number"])}</a></td><td>{e(c["title"])}</td>'
                   f'<td><span class="tag sev-{c["severity"]}">{c["severity"]}</span></td><td>{tag(c["status"])}</td></tr>' for c in open_cases)
    body = f"""<h1>داشبورد</h1><div class="cards">{cards_html}</div>
<h2>پرداخت‌های نیازمند بررسی</h2><div class="wrap"><table><tr><th>شناسه پرداخت</th><th>وضعیت</th><th>دلیل بررسی</th><th>مبلغ</th></tr>
{rows or '<tr><td colspan=4>موردی نیست. خوب است.</td></tr>'}</table></div>
<h2>پرونده‌های باز</h2><div class="wrap"><table><tr><th>شماره</th><th>عنوان</th><th>شدت</th><th>وضعیت</th></tr>
{crow or '<tr><td colspan=4>پرونده باز نداریم.</td></tr>'}</table></div>
<p class="muted">برای ساخت داده نمونه از خط فرمان: <span class="ltr">python -m scripts.reset_demo_data</span></p>"""
    return page("داشبورد", body, "dash")


@router.route("GET", "/payments")
def payments_page(req, ctx):
    q = req.query.get("q") or None
    status = req.query.get("status") or None
    rows = payments.list_payments(ctx, q=q, status=status, date_from=req.query.get("from") or None,
                                  date_to=req.query.get("to") or None)
    opts = "".join(f'<option value="{s}" {"selected" if s == status else ""}>{e(STATUS_FA.get(s, s))}</option>'
                   for s in ("created", "pending", "succeeded", "failed", "unknown", "expired", "partially_refunded", "refunded"))
    tbl = "".join(f'<tr><td><a href="/payments/{e(p["id"])}">{ltr(p["id"])}</a></td><td>{ltr(p["order_id"])}</td>'
                  f'<td>{tag(p["status"])}</td><td>{tag(p["order_status"])}</td><td>{money(p["amount_irr"])}</td>'
                  f'<td>{ltr(p["provider_reference"])}</td><td>{ltr(p["correlation_id"])}</td></tr>' for p in rows)
    body = f"""<h1>پرداخت‌ها و سفارش‌ها</h1>
<form method="get" class="panel"><div class="grid2"><div><label>جست‌وجو (payment_id، order_id، provider_reference یا correlation_id)</label>
<input name="q" value="{e(q or '')}" style="width:100%" dir="ltr"></div>
<div><label>وضعیت پرداخت</label><select name="status"><option value="">همه</option>{opts}</select></div>
<div><label>از تاریخ (YYYY-MM-DD)</label><input name="from" value="{e(req.query.get('from',''))}" dir="ltr"></div>
<div><label>تا تاریخ (YYYY-MM-DD)</label><input name="to" value="{e(req.query.get('to',''))}" dir="ltr"></div></div>
<p><button>اعمال فیلتر</button> <a class="btn sec" href="/payments">پاک‌کردن</a></p></form>
<p class="muted">{len(rows)} مورد. زمان ایجاد به وقت تهران است.</p>
<div class="wrap"><table><tr><th>پرداخت</th><th>سفارش</th><th>وضعیت پرداخت</th><th>وضعیت سفارش</th><th>مبلغ</th><th>provider_reference</th><th>correlation_id</th></tr>
{tbl or '<tr><td colspan=7>نتیجه‌ای نیست.</td></tr>'}</table></div>"""
    return page("پرداخت‌ها", body, "pay")


@router.route("GET", "/payments/{payment_id}")
def payment_detail(req, ctx):
    pid = req.params["payment_id"]
    p = payments.get_payment(ctx, pid)
    o = ctx.conn.execute("SELECT * FROM orders WHERE id = ?", (p["order_id"],)).fetchone()
    tl = timeline.build_timeline(ctx, payment_id=pid)
    flash, err = _flash(req)
    tl_rows = "".join(f'<tr><td>{ltr(to_tehran_display(i["at"]))}</td><td>{e(i["source"])}</td><td>{ltr(i["type"])}</td>'
                      f'<td>{tag(i["status"]) if i["status"] else ""}</td><td>{e(i["summary"])}</td></tr>' for i in tl)
    refs = rows_to_dicts(ctx.conn.execute("SELECT * FROM refunds WHERE payment_id = ? ORDER BY created_at", (pid,)))
    refund_rows = "".join(f'<tr><td>{ltr(r["id"])}</td><td>{money(r["amount_irr"])}</td><td>{tag(r["status"])}</td></tr>' for r in refs)
    related = ctx.conn.execute("SELECT case_number, id FROM support_cases WHERE payment_id = ?", (pid,)).fetchall()
    rel = " ".join(f'<a class="btn sec" href="/cases/{e(r["case_number"])}">{e(r["case_number"])}</a>' for r in related) or '<span class="muted">پرونده‌ای به این پرداخت وصل نیست.</span>'
    review = f'<p><span class="tag">دلیل بررسی: {e(REVIEW_FA.get(p["review_reason"], "-"))}</span></p>' if p["review_reason"] else ""
    body = f"""<h1>پرداخت {ltr(pid)}</h1>
<div class="cards"><div class="card"><b>{tag(p['status'])}</b><span>وضعیت پرداخت</span></div>
<div class="card"><b>{tag(o['status'])}</b><span>وضعیت سفارش</span></div>
<div class="card"><b>{e(money(p['amount_irr']))}</b><span>مبلغ داخلی</span></div>
<div class="card"><b>{e(money(p['sent_amount'], p['sent_amount_unit'] or 'IRR')) if p['sent_amount'] is not None else '-'}</b><span>مبلغ ارسال‌شده به ارائه‌دهنده</span></div></div>
{review}
<div class="panel"><b>شناسه‌ها:</b> سفارش {ltr(p['order_id'])} | ارائه‌دهنده {ltr(p['provider_reference'])} | correlation_id {ltr(p['correlation_id'])}
<p class="muted">پرونده‌های مرتبط: {rel}</p></div>
<div class="grid2">
<div class="panel"><h2>عملیات پشتیبانی</h2>
<form method="post" action="/ui/payments/{e(pid)}/verify" class="inline"><button>verify از ارائه‌دهنده</button></form>
<form method="post" action="/ui/payments/{e(pid)}/simulate" style="margin-top:10px"><label>شبیه‌سازی: نتیجه نزد ارائه‌دهنده (فقط lab)</label>
<select name="outcome"><option value="succeeded">موفق</option><option value="failed">ناموفق</option><option value="expired">منقضی</option></select>
<label>نحوه ارسال webhook</label><select name="webhook"><option value="deliver">ارسال عادی</option><option value="skip">ارسال نشود (webhook نرسیده)</option>
<option value="duplicate">ارسال دوبار (تکراری)</option><option value="invalid_signature">امضای نامعتبر</option></select>
<p><button>اجرای شبیه‌ساز</button></p></form></div>
<div class="panel"><h2>استرداد آزمایشی</h2>
<form method="post" action="/ui/payments/{e(pid)}/refund"><label>مبلغ (ریال، مضرب ۱۰)</label><input name="amount_irr" dir="ltr" value="" required>
<label>دلیل</label><input name="reason" style="width:100%"><p><button>ثبت استرداد</button></p></form>
<table style="margin-top:8px"><tr><th>شناسه</th><th>مبلغ</th><th>وضعیت</th></tr>{refund_rows or '<tr><td colspan=3>استردادی نیست</td></tr>'}</table></div>
</div>
<h2>خط زمانی رویدادها</h2><div class="wrap"><table><tr><th>زمان (تهران)</th><th>منبع</th><th>نوع</th><th>وضعیت</th><th>شرح</th></tr>{tl_rows}</table></div>"""
    return page("پرداخت", body, "pay", flash, err)


@router.route("GET", "/cases")
def cases_page(req, ctx):
    rows = cases.list_cases(ctx, status=req.query.get("status") or None, severity=req.query.get("severity") or None,
                            q=req.query.get("q") or None)
    flash, err = _flash(req)
    tbl = "".join(f'<tr><td><a href="/cases/{e(c["case_number"])}">{ltr(c["case_number"])}</a></td><td>{e(c["title"])}</td>'
                  f'<td>{e(c["problem_type"])}</td><td><span class="tag sev-{c["severity"]}">{c["severity"]}</span></td>'
                  f'<td>{tag(c["status"])}</td><td>{ltr(to_tehran_display(c["created_at"]))}</td></tr>' for c in rows)
    body = f"""<h1>پرونده‌های پشتیبانی</h1>
<form method="get" class="panel"><label>جست‌وجو (شماره پرونده، payment_id، correlation_id یا عنوان)</label>
<input name="q" dir="ltr" value="{e(req.query.get('q',''))}" style="width:60%"> <select name="severity"><option value="">همه شدت‌ها</option>
{''.join(f'<option {"selected" if s == req.query.get("severity") else ""}>{s}</option>' for s in ("P1","P2","P3","P4"))}</select>
<button>فیلتر</button></form>
<div class="wrap"><table><tr><th>شماره</th><th>عنوان</th><th>نوع</th><th>شدت</th><th>وضعیت</th><th>ایجاد</th></tr>{tbl or '<tr><td colspan=6>پرونده‌ای پیدا نشد.</td></tr>'}</table></div>"""
    return page("پرونده‌ها", body, "cases", flash, err)


@router.route("GET", "/cases/{ident}")
def case_detail(req, ctx):
    c = cases.get_case(ctx, req.params["ident"])
    flash, err = _flash(req)
    ev = "".join(f"<li><b>{e(x.get('kind',''))}</b>: {e(x.get('label',''))} <span class='ltr'>{e(str(x.get('ref','')))}</span></li>" for x in c["evidence"])
    hy = "".join(f"<li>{e(h.get('hypothesis',''))} — <span class='muted'>تأیید: {e(h.get('confirm_if',''))} | رد: {e(h.get('reject_if',''))} | نتیجه: {e(h.get('result','باز'))}</span></li>" for h in c["hypotheses"])
    notes = "".join(f'<tr><td>{ltr(to_tehran_display(n["created_at"]))}</td><td>{e(n["note_type"])}</td><td>{e(n["author"])}</td><td>{e(n["body"])}</td></tr>' for n in c["notes"])
    rb = BY_SLUG.get(c.get("runbook_slug") or "")
    rb_link = f'<a class="btn sec" href="/runbooks/{e(rb["slug"])}">راهنمای این مورد</a>' if rb else ""
    pay = f'<a href="/payments/{e(c["payment_id"])}">{ltr(c["payment_id"])}</a>' if c.get("payment_id") else "-"
    status_opts = "".join(f'<option value="{s}" {"selected" if s == c["status"] else ""}>{e(STATUS_FA[s])}</option>' for s in STATUS_FA if s in ("new","investigating","waiting_for_merchant","escalated","resolved","closed"))
    root_cause_labels = {"not_determined": "تعیین نشده", "confirmed": "تأییدشده با شواهد", "unknown": "نامشخص / نیازمند ارجاع"}
    root_cause_status_opts = "".join(f'<option value="{status}" {"selected" if status == c["root_cause_status"] else ""}>{label}</option>' for status, label in root_cause_labels.items())
    blank = lambda k: e(c.get(k) or "")
    body = f"""<h1>{ltr(c['case_number'])} — {e(c['title'])}</h1>
<p>{tag(c['status'])} <span class="tag sev-{c['severity']}">{c['severity']}</span> {rb_link} | پرداخت مرتبط: {pay}</p>
<div class="grid2"><div class="panel"><h2>۱. گزارش اولیه</h2><p>{blank('merchant_report')}</p>
<p><b>اثر بر کسب‌وکار:</b> {blank('business_impact')}</p><p><b>دلیل اولویت:</b> {blank('priority_reason')}</p></div>
<div class="panel"><h2>۲. داده لازم و داده ممنوع</h2><p><b>لازم:</b> {blank('required_data')}</p>
<p><b>نباید درخواست شود:</b> {blank('data_not_to_request')}</p><h2>۳. بررسی اولیه</h2><p>{blank('initial_checks')}</p></div></div>
<div class="panel"><h2>۴. شواهد</h2><ul>{ev or '<li>شاهدی ثبت نشده</li>'}</ul>
<h2>۵. فرضیه‌ها</h2><ul>{hy or '<li>فرضیه‌ای ثبت نشده</li>'}</ul></div>
<div class="grid2"><div class="panel"><h2>۶. علت ریشه‌ای</h2><p>وضعیت: {tag(c['root_cause_status'], labels={'not_determined':'تعیین نشده','confirmed':'تأییدشده','unknown':'نامشخص (escalate)'})}</p>
<p>{blank('root_cause') or '<span class="muted">هنوز تأیید نشده؛ اگر شواهد کافی نیست، «نامشخص» بماند.</span>'}</p>
<h2>۷. راهکار و مرز اختیار</h2><p>{blank('resolution') or '-'}</p><p><b>مرز اختیار:</b> {blank('authority_boundary') or '-'}</p></div>
<div class="panel"><h2>۸. تست بعد از راهکار</h2><p>{blank('validation') or '<span class="muted">هنوز ثبت نشده</span>'}</p>
<h2>۹. پیام به پذیرنده</h2><p>{blank('merchant_message') or '-'}</p><h2>۱۰. جمع‌بندی بستن</h2><p>{blank('closure_summary') or '-'}</p></div></div>
<div class="panel"><h2>به‌روزرسانی پرونده</h2>
<form method="post" action="/ui/cases/{e(c['case_number'])}/status"><label>تغییر وضعیت</label><select name="status">{status_opts}</select>
<label>نتیجه/راهکار (برای resolved الزامی)</label><textarea name="resolution">{blank('resolution')}</textarea>
<label>تست تأیید (برای resolved الزامی)</label><textarea name="validation">{blank('validation')}</textarea>
<label>وضعیت علت ریشه‌ای</label><select name="root_cause_status">{root_cause_status_opts}</select><p class="muted">«تأییدشده با شواهد» را فقط پس از بررسی شواهد انتخاب کنید؛ وارد کردن توضیح به‌تنهایی تأیید محسوب نمی‌شود.</p>
<label>علت ریشه‌ای (اگر تأیید شد)</label><textarea name="root_cause">{blank('root_cause')}</textarea>
<label>جمع‌بندی بستن (برای closed الزامی)</label><textarea name="closure_summary">{blank('closure_summary')}</textarea>
<label>پیام به پذیرنده (برای closed الزامی)</label><textarea name="merchant_message">{blank('merchant_message')}</textarea>
<p><button>ذخیره</button></p></form></div>
<div class="panel"><h2>یادداشت جدید</h2><form method="post" action="/ui/cases/{e(c['case_number'])}/notes">
<textarea name="body" required></textarea><p><button class="sec">افزودن یادداشت</button></p></form>
<div class="wrap"><table><tr><th>زمان</th><th>نوع</th><th>نویسنده</th><th>متن</th></tr>{notes}</table></div></div>"""
    return page(c["case_number"], body, "cases", flash, err)


@router.route("GET", "/diagnostics")
def diagnostics(req, ctx):
    flash, err = _flash(req)
    last = req.query.get("last")
    cards = "".join(f"""<div class="panel"><h2>{e(v['title'])}</h2><p class="muted">runbook: {e(v['runbook'])}</p>
<form method="post" action="/ui/diagnostics/{e(k)}"><button>اجرای سناریو</button></form></div>""" for k, v in scenarios.SCENARIOS.items())
    queries = "".join(f'<option value="{e(k)}">{e(v["title_fa"])}</option>' for k, v in qmod.QUERIES.items())
    body = f"""<h1>عیب‌یابی و سناریوهای آزمایشگاهی</h1>
<p class="muted">هر سناریو درخواست‌های واقعی API را در همین برنامه اجرا می‌کند. شناسه‌ها و پاسخ‌ها از دیتابیس همین اجرا می‌آیند.</p>
<div class="grid2">{cards}</div>
<h2>اجرای query تشخیصی (فقط‌خواندنی)</h2><form method="get" action="/diagnostics/query" class="panel">
<select name="name">{queries}</select> <label>پارامترها (مثلاً payment_id=pay_... یا correlation_id=corr_...)</label>
<input name="params" dir="ltr" style="width:60%"> <button>اجرا</button></form>
{'<p class="muted">آخرین نتیجه ثبت شد: ' + e(last) + '</p>' if last else ''}"""
    return page("عیب‌یابی", body, "diag", flash, err)


@router.route("GET", "/diagnostics/query")
def diag_query(req, ctx):
    name = req.query.get("name", "")
    params = {}
    raw = req.query.get("params", "")
    for part in raw.split("&") if "&" in raw else [raw]:
        if "=" in part:
            k, v = part.split("=", 1)
            params[k.strip()] = v.strip()
    from ..errors import ApiError
    try:
        res = qmod.run_query(ctx, name, params)
        head = "".join(f"<th>{e(c)}</th>" for c in res["columns"])
        rows = "".join("<tr>" + "".join(f"<td>{ltr(v)}</td>" for v in r) + "</tr>" for r in res["rows"])
        body = f"""<h1>{e(res['title_fa'])}</h1><pre>{e(res['sql'])}</pre>
<div class="wrap"><table><tr>{head}</tr>{rows or '<tr><td>نتیجه‌ای نیست</td></tr>'}</table></div>
<p><a class="btn sec" href="/diagnostics">بازگشت</a></p>"""
        return page("query", body, "diag")
    except ApiError as exc:
        return page("query", "", "diag", error=exc.message_fa)


@router.route("GET", "/reconciliation")
def recon_page(req, ctx):
    flash, err = _flash(req)
    runs = reconciliation.list_runs(ctx)
    rows = "".join(f'<tr><td><a href="/reconciliation/runs/{e(r["id"])}">{ltr(r["id"])}</a></td><td>{e(r["source_filename"])}</td>'
                   f'<td>{r["row_count"]}</td><td>{r["matched_count"]}</td><td>{r["exception_count"]}</td>'
                   f'<td>{ltr(to_tehran_display(r["created_at"]))}</td></tr>' for r in runs)
    body = f"""<h1>تطبیق گزارش ارائه‌دهنده</h1>
<div class="panel"><p>این یک <b>ابزار تشخیصی با داده ساختگی</b> است و به فرایند تسویه واقعی هیچ شرکتی وصل نیست. ردیفی حذف یا مبلغی اصلاح نمی‌شود؛ فقط مغایرت ثبت می‌شود.</p>
<form method="post" action="/ui/reconciliation/demo" class="inline"><button>اجرای تطبیق با فایل نمونه</button></form>
<form method="post" action="/ui/reconciliation/upload" enctype="application/x-www-form-urlencoded" style="margin-top:10px">
<p class="muted">فایل CSV را در حالت متن الصاق کنید (حداکثر ۱ مگابایت، ۵۰۰۰ ردیف، ستون‌های provider_reference, merchant_reference, transaction_date, amount, currency, status, refunded_amount):</p>
<textarea name="csv" dir="ltr" style="min-height:120px"></textarea><p><button class="sec">تطبیق این متن</button></p></form></div>
<div class="wrap"><table><tr><th>اجرا</th><th>فایل</th><th>ردیف</th><th>تطبیق</th><th>مغایرت</th><th>زمان</th></tr>{rows or '<tr><td colspan=6>هنوز اجرایی نیست.</td></tr>'}</table></div>"""
    return page("تطبیق", body, "rec", flash, err)


@router.route("GET", "/reconciliation/runs/{run_id}")
def recon_run(req, ctx):
    flash, err = _flash(req)
    run = reconciliation.get_run(ctx, req.params["run_id"])
    excs = reconciliation.list_exceptions(ctx, run_id=run["id"])
    flt = req.query.get("type")
    if flt:
        excs = [x for x in excs if x["exception_type"] == flt]
    types = "".join(f'<a class="btn sec" href="?type={k}">{e(v)}</a> ' for k, v in EXC_FA.items())
    rows = []
    for x in excs:
        action = (f'<a href="/cases/{e(x["case_number"])}">{e(x["case_number"])}</a>' if x["case_number"] else
                  f'<form method="post" action="/ui/recon-exceptions/{e(x["id"])}/case" class="inline"><button class="sec">ایجاد پرونده</button></form>')
        if x["status"] != "resolved":
            action += f'<form method="post" action="/ui/recon-exceptions/{e(x["id"])}/resolve" style="margin-top:4px"><input name="resolution_note" placeholder="نتیجه بررسی (حداقل ۱۰ کاراکتر)" style="width:220px"><button class="sec">ثبت حل</button></form>'
        rows.append(f'<tr><td>{e(EXC_FA.get(x["exception_type"], x["exception_type"]))}</td><td>{ltr(x["provider_reference"])}</td>'
                    f'<td>{ltr(x["payment_id"])}</td><td>{ltr(x["csv_row"])}</td><td>{e(x["expected"] or "-")}</td><td>{e(x["actual"] or "-")}</td>'
                    f'<td>{tag(x["status"])}</td><td><div>{e(x["suggestion"])}</div><div class="muted">تأیید: {e(x["verification"])}</div></td><td>{action}</td></tr>')
    body = f"""<h1>مغایرت‌های اجرای {ltr(run['id'])}</h1><p class="muted">فایل: {e(run['source_filename'])} | ردیف‌ها: {run['row_count']} | تطبیق‌شده: {run['matched_count']} | مغایرت: {run['exception_count']}</p>
<p>فیلتر: <a class="btn sec" href="?">همه</a> {types}</p>
<div class="wrap"><table><tr><th>نوع</th><th>provider_reference</th><th>payment_id</th><th>ردیف CSV</th><th>انتظار</th><th>واقعیت</th><th>وضعیت</th><th>پیشنهاد و تأیید</th><th>اقدام</th></tr>
{''.join(rows) or '<tr><td colspan=9>مغایرتی نیست.</td></tr>'}</table></div>"""
    return page("مغایرت", body, "rec", flash, err)


@router.route("GET", "/runbooks")
def runbooks_page(req, ctx):
    items = "".join(f'<li><a href="/runbooks/{e(r["slug"])}">{e(r["title"])}</a></li>' for r in RUNBOOKS)
    return page("راهنماها", f"<h1>راهنماهای پشتیبانی</h1><ul>{items}</ul>", "rb")


@router.route("GET", "/runbooks/{slug}")
def runbook_page(req, ctx):
    from ..errors import not_found
    r = BY_SLUG.get(req.params["slug"])
    if r is None:
        raise not_found("runbook", req.params["slug"])
    lst = lambda xs: "".join(f"<li>{e(x)}</li>" for x in xs)
    body = f"""<h1>{e(r['title'])}</h1>
<div class="grid2"><div class="panel"><h2>نشانه</h2><p>{e(r['symptom'])}</p><h2>شرط اجرای این راهنما</h2><p>{e(r['applies_when'])}</p>
<h2>داده لازم</h2><p>{e(r['required_data'])}</p><h2>داده‌ای که نباید درخواست شود</h2><p>{e(r.get('data_not_to_request') or DO_NOT)}</p></div>
<div class="panel"><h2>بررسی‌ها (قبل از هر تغییر)</h2><ol>{lst(r['checks'])}</ol><h2>query / API</h2><ul>{lst(r['queries'])}</ul><ul>{lst(r['apis'])}</ul></div></div>
<div class="grid2"><div class="panel"><h2>اقدام مجاز</h2><ul>{lst(r['allowed_actions'])}</ul><h2>اقدام ممنوع</h2><ul>{lst(r['not_allowed'])}</ul></div>
<div class="panel"><h2>شرط escalation</h2><p>{e(r['escalation'])}</p><h2>روش verification</h2><p>{e(r['verification'])}</p>
<h2>پیام نمونه به پذیرنده</h2><p>{e(r['merchant_message'])}</p></div></div>"""
    return page(r["title"], body, "rb")


