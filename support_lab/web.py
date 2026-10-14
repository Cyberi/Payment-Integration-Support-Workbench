"""A minimal Persian RTL web interface for the local practice lab."""

from html import escape
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit
import re

from . import store

STATUS_LABELS = {"open": "باز", "investigating": "در حال بررسی", "resolved": "حل‌شده"}

STYLE = """
:root{color-scheme:light;--ink:#18212f;--muted:#5d6877;--line:#dce2e9;--paper:#f5f7fa;--panel:#fff;--blue:#2459a6;--soft:#eaf1fb;--red:#a33434;--amber:#875e12;--green:#236747}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:Tahoma,Arial,sans-serif;line-height:1.8}a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}header{background:#152238;color:#fff;padding:22px max(20px,calc((100% - 1050px)/2))}header a{color:#fff}header .sub{color:#d7e1ef;font-size:.9rem}.wrap{max-width:1050px;margin:28px auto;padding:0 18px}.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:22px;margin:16px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(245px,1fr));gap:14px}.case{border:1px solid var(--line);border-radius:9px;padding:17px;background:#fff}.meta{color:var(--muted);font-size:.9rem}.tag{display:inline-block;border-radius:999px;padding:2px 10px;background:var(--soft);font-size:.83rem}.status-open{color:var(--red)}.status-investigating{color:var(--amber)}.status-resolved{color:var(--green)}.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}.fact{padding:12px;background:#f7f9fc;border-radius:7px;overflow-wrap:anywhere}.fact strong{display:block;font-size:.88rem;color:var(--muted)}table{border-collapse:collapse;width:100%;margin:8px 0 16px;background:#fff}th,td{border:1px solid var(--line);padding:10px;text-align:right;vertical-align:top}th{background:#f0f3f7}textarea{width:100%;min-height:105px;border:1px solid #abb6c5;border-radius:7px;padding:12px;font:inherit}button{border:0;border-radius:7px;padding:10px 16px;font:inherit;cursor:pointer;background:var(--blue);color:#fff}button.secondary{background:#e8edf4;color:var(--ink)}.form-row{margin:14px 0}label{display:block;font-weight:bold;margin-bottom:5px}.notice{padding:12px 16px;background:#fff4dd;border:1px solid #edcf87;border-radius:8px}.notice.error{background:#fff0f0;border-color:#e2b4b4;color:#7d2e2e}.notice.good{background:#eaf7ef;border-color:#b4d7c1;color:#20573c}.btnrow{display:flex;gap:10px;flex-wrap:wrap}.evidence{overflow-x:auto}footer{max-width:1050px;margin:20px auto;padding:0 18px 30px;color:var(--muted);font-size:.85rem}@media(max-width:600px){.panel{padding:15px}header{padding:18px}.wrap{margin-top:16px}th,td{padding:7px;font-size:.9rem}}
"""


def e(value) -> str:
    return escape(str(value), quote=True)


def _page(title: str, body: str, message: str = "", error: bool = False) -> str:
    message_html = ""
    if message:
        kind = "notice error" if error else "notice good"
        message_html = f'<div class="{kind}" role="status">{e(message)}</div>'
    return f"""<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="آزمایشگاه محلی تمرین بررسی پرونده‌های پشتیبانی نرم‌افزار">
<title>{e(title)} | آزمایشگاه پشتیبانی</title><style>{STYLE}</style></head>
<body><header><a href="/"><strong>آزمایشگاه تمرین پشتیبانی نرم‌افزار</strong></a><div class="sub">نمونه‌کار آموزشی محلی · تمام داده‌ها ساختگی هستند</div></header>
<main class="wrap">{message_html}{body}</main><footer>این برنامه فقط برای تمرین و نمونه‌کار است؛ به بانک یا درگاه واقعی متصل نیست و برای دادهٔ واقعی یا استفادهٔ عملیاتی طراحی نشده است.</footer></body></html>"""


def render_dashboard(cases: list[dict], message: str = "", error: bool = False) -> str:
    counts = {status: sum(1 for case in cases if case["status"] == status) for status in STATUS_LABELS}
    summary = "".join(
        f'<div class="fact"><strong>{e(STATUS_LABELS[status])}</strong><span>{counts[status]} پرونده</span></div>'
        for status in STATUS_LABELS
    )
    cards = []
    for case in cases:
        cards.append(
            f"""<article class="case"><div class="btnrow"><span class="tag">{e(case['priority'])}</span>
            <span class="tag status-{e(case['status'])}">{e(STATUS_LABELS[case['status']])}</span></div>
            <h2><a href="/cases/{e(case['case_id'])}">{e(case['title'])}</a></h2>
            <div class="meta">{e(case['case_id'])} · {e(case['merchant'])}</div>
            <p>{e(case['report'])}</p><a href="/cases/{e(case['case_id'])}">بررسی پرونده ←</a></article>"""
        )
    body = f"""<section class="panel"><h1>پرونده‌های تمرینی</h1>
    <p>هدف این محیط، تمرین جمع‌آوری شواهد، تفکیک واقعیت از فرضیه و ثبت اقدام بعدی است. هیچ عملیاتی روی پرداخت واقعی انجام نمی‌شود.</p>
    <div class="facts">{summary}</div></section>
    <section class="grid">{''.join(cards)}</section>
    <section class="panel"><h2>روش پیشنهادی بررسی</h2><ol><li>شناسه‌های سفارش، پرداخت و هم‌بستگی را بررسی کن.</li><li>وضعیت داخلی را با شواهد ارائه‌دهنده و رویدادها تطبیق بده.</li><li>قبل از هر نتیجه‌گیری، یک یادداشت مستند ثبت کن.</li><li>در جمع‌بندی، اقدام انجام‌شده و نتیجهٔ قابل اثبات را بنویس.</li></ol></section>"""
    return _page("پرونده‌ها", body, message, error)


def render_case_detail(case: dict, notes: list[dict], message: str = "", error: bool = False) -> str:
    facts = [
        ("پذیرنده", case["merchant"]), ("شناسه سفارش", case["order_id"]),
        ("شناسه پرداخت", case["payment_id"]), ("شناسه هم‌بستگی", case["correlation_id"]),
        ("مبلغ نمونه", f"{case['amount']} ({case['unit']})"),
        ("وضعیت داخلی", case["internal_status"]), ("وضعیت ارائه‌دهنده", case["provider_status"]),
        ("وضعیت webhook", case["webhook_status"]),
    ]
    fact_html = "".join(f'<div class="fact"><strong>{e(label)}</strong>{e(value)}</div>' for label, value in facts)
    evidence_rows = "".join(
        f"<tr><td>{e(at)}</td><td>{e(source)}</td><td>{e(detail)}</td></tr>"
        for at, source, detail in case["evidence"]
    )
    if notes:
        note_rows = "".join(
            f"<li><strong>{e(note['created_at'])}</strong><p>{e(note['note'])}</p></li>" for note in notes
        )
        notes_html = f"<ol>{note_rows}</ol>"
    else:
        notes_html = "<p class='meta'>هنوز یادداشتی ثبت نشده است.</p>"

    if case["status"] == "open":
        status_controls = f"""<form method="post" action="/cases/{e(case['case_id'])}/start"><button type="submit">شروع بررسی</button></form>"""
    elif case["status"] == "investigating":
        status_controls = f"""<form method="post" action="/cases/{e(case['case_id'])}/resolve">
        <div class="form-row"><label for="resolution">جمع‌بندی و اقدام نهایی</label>
        <textarea id="resolution" name="resolution" minlength="20" maxlength="1500" required placeholder="شواهدی که بررسی شد، نتیجهٔ قابل اثبات و اقدام انجام‌شده را بنویس..."></textarea></div>
        <button class="secondary" type="submit">ثبت جمع‌بندی و حل پرونده</button></form>"""
    else:
        status_controls = f"<div class='notice good'><strong>پرونده حل شده است.</strong><p>{e(case['resolution'])}</p></div>"

    if case["status"] != "resolved":
        note_form = f"""<form method="post" action="/cases/{e(case['case_id'])}/notes">
        <div class="form-row"><label for="note">یادداشت بررسی</label><textarea id="note" name="note" maxlength="1000" required placeholder="مثلاً: وضعیت داخلی و گزارش ارائه‌دهنده را با شناسه پرداخت تطبیق دادم..."></textarea></div>
        <button type="submit">ثبت یادداشت</button></form>"""
    else:
        note_form = "<p class='meta'>ثبت یادداشت برای پروندهٔ حل‌شده غیرفعال است.</p>"

    body = f"""<p><a href="/">← بازگشت به فهرست پرونده‌ها</a></p>
    <section class="panel"><div class="btnrow"><span class="tag">{e(case['priority'])}</span><span class="tag status-{e(case['status'])}">{e(STATUS_LABELS[case['status']])}</span></div>
    <h1>{e(case['title'])}</h1><div class="meta">{e(case['case_id'])} · زمان گزارش: {e(case['reported_at'])}</div><p>{e(case['report'])}</p>
    <div class="facts">{fact_html}</div><h2>شواهد موجود</h2><div class="evidence"><table><thead><tr><th>زمان</th><th>منبع</th><th>شرح</th></tr></thead><tbody>{evidence_rows}</tbody></table></div>
    <div class="notice"><strong>پرسش تمرینی</strong><p>{e(case['question'])}</p></div><div class="form-row">{status_controls}</div></section>
    <section class="panel"><h2>یادداشت‌های بررسی</h2>{notes_html}{note_form}</section>"""
    return _page(case["title"], body, message, error)


class SupportHandler(BaseHTTPRequestHandler):
    db_path = "var/support_lab.sqlite3"
    server_version = "SupportCaseLab/0.1"

    def _send(self, status: int, html: str) -> None:
        payload = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/":
            self._send(200, render_dashboard(store.list_cases(self.db_path)))
            return
        match = re.fullmatch(r"/cases/(CASE-\d{4})", path)
        if match:
            case_id = match.group(1)
            case = store.get_case(self.db_path, case_id)
            if case is None:
                self._send(404, _page("پیدا نشد", "<section class='panel'><h1>پرونده پیدا نشد</h1><a href='/'>بازگشت</a></section>"))
                return
            self._send(200, render_case_detail(case, store.get_notes(self.db_path, case_id)))
            return
        self._send(404, _page("پیدا نشد", "<section class='panel'><h1>صفحه پیدا نشد</h1><a href='/'>بازگشت</a></section>"))

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        match = re.fullmatch(r"/cases/(CASE-\d{4})/(notes|start|resolve)", path)
        if not match:
            self._send(404, _page("پیدا نشد", "<section class='panel'><h1>مسیر پیدا نشد</h1><a href='/'>بازگشت</a></section>"))
            return
        case_id, action = match.groups()
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 16_384:
                raise ValueError("اندازهٔ درخواست معتبر نیست.")
            form = parse_qs(self.rfile.read(length).decode("utf-8", errors="strict"), keep_blank_values=True)
            if action == "notes":
                store.add_note(self.db_path, case_id, form.get("note", [""])[0])
                message = "یادداشت ثبت شد."
            elif action == "start":
                store.start_investigation(self.db_path, case_id)
                message = "بررسی آغاز شد."
            else:
                store.resolve_case(self.db_path, case_id, form.get("resolution", [""])[0])
                message = "جمع‌بندی ثبت شد و پرونده حل شد."
            case = store.get_case(self.db_path, case_id)
            self._send(200, render_case_detail(case, store.get_notes(self.db_path, case_id), message=message))
        except (ValueError, UnicodeDecodeError) as exc:
            case = store.get_case(self.db_path, case_id)
            if case is None:
                self._send(404, _page("پیدا نشد", "<section class='panel'><h1>پرونده پیدا نشد</h1><a href='/'>بازگشت</a></section>"))
                return
            self._send(400, render_case_detail(case, store.get_notes(self.db_path, case_id), message=str(exc), error=True))

    def log_message(self, format: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {self.address_string()} {format % args}")
