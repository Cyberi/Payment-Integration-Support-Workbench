"""Tiny WSGI layer (standard library only).

Why not FastAPI? See docs/architecture.md: the lab must run on a clean Python
install without downloading packages. This module is deliberately small:
routing, request parsing, JSON/HTML responses, uniform errors, correlation IDs
and one structured log line per request.
"""
from __future__ import annotations

import io
import json
import re
import sqlite3
import time
import traceback
from urllib.parse import parse_qs, urlencode

from ..context import Ctx
from ..database import connect
from ..domain.states import InvalidTransition
from ..errors import ApiError
from ..ids import new_id
from ..logs import log_event

CORR_RE = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")
MAX_BODY = 2_000_000
STATUS_TEXT = {200: "OK", 201: "Created", 202: "Accepted", 204: "No Content", 302: "Found", 303: "See Other",
               400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 405: "Method Not Allowed",
               409: "Conflict", 413: "Payload Too Large", 415: "Unsupported Media Type",
               422: "Unprocessable Entity", 500: "Internal Server Error", 502: "Bad Gateway",
               504: "Gateway Timeout"}


class Request:
    def __init__(self, method, path, query, headers, body):
        self.method, self.path, self.headers, self.body = method, path, headers, body
        self.query = {k: v[0] for k, v in query.items()}
        self.params: dict = {}

    def header(self, name, default=None):
        return self.headers.get(name.lower(), default)

    def json(self):
        if not self.body:
            raise ApiError(400, "BODY_REQUIRED", "JSON body is required", "بدنه JSON الزامی است")
        try:
            return json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "INVALID_JSON", "request body is not valid JSON", "بدنه درخواست JSON معتبر نیست")

    def form(self) -> dict:
        return {k: v[0] for k, v in parse_qs(self.body.decode("utf-8"), keep_blank_values=True).items()}


class Response:
    def __init__(self, status=200, body=b"", content_type="application/json; charset=utf-8", headers=None):
        self.status = status
        self.body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.headers = [("Content-Type", content_type)] + list(headers or [])


def json_response(data, status=200, headers=None) -> Response:
    return Response(status, json.dumps(data, ensure_ascii=False, indent=2), headers=headers)


def html_response(html: str, status=200) -> Response:
    return Response(status, html, "text/html; charset=utf-8")


def redirect(location: str, **query) -> Response:
    if query:
        location += ("&" if "?" in location else "?") + urlencode(query)
    return Response(303, b"", "text/plain", [("Location", location)])


class Router:
    def __init__(self):
        self.routes: list[tuple[str, re.Pattern, str, object]] = []

    def add(self, method: str, pattern: str, handler) -> None:
        regex = "^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "$"
        self.routes.append((method, re.compile(regex), pattern, handler))

    def route(self, method, pattern):
        def deco(fn):
            self.add(method, pattern, fn)
            return fn
        return deco

    def match(self, method, path):
        allowed = False
        for m, rx, pattern, handler in self.routes:
            mo = rx.match(path)
            if mo:
                if m == method:
                    return handler, mo.groupdict(), pattern
                allowed = True
        return None, None, ("METHOD" if allowed else None)


def error_body(exc: ApiError, correlation_id: str) -> dict:
    return {"error": {"code": exc.code, "message": exc.message, "message_fa": exc.message_fa,
                      "details": exc.details}, "correlation_id": correlation_id}


class App:
    def __init__(self, settings, router: Router, html_error=None):
        self.settings = settings
        self.router = router
        self.html_error = html_error

    # -- WSGI entry point --------------------------------------------------
    def __call__(self, environ, start_response):
        resp = self.handle(environ)
        start_response(f"{resp.status} {STATUS_TEXT.get(resp.status, '')}", resp.headers)
        return [resp.body]

    def handle(self, environ) -> Response:
        started = time.perf_counter()
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/") or "/"
        headers = {k[5:].replace("_", "-").lower(): v for k, v in environ.items() if k.startswith("HTTP_")}
        if environ.get("CONTENT_TYPE"):
            headers["content-type"] = environ["CONTENT_TYPE"]
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        body = environ["wsgi.input"].read(min(length, MAX_BODY + 1)) if length else b""
        req = Request(method, path, parse_qs(environ.get("QUERY_STRING", "")), headers, body)

        supplied = headers.get("x-correlation-id", "")
        client_supplied = bool(CORR_RE.match(supplied))
        corr = supplied if client_supplied else new_id("corr")
        is_api = path.startswith("/api/")
        conn = connect(self.settings.db_path)
        ctx = Ctx(conn, self.settings, corr, client_supplied, actor="api" if is_api else "support-ui", app=self)
        route_pattern = None
        error_code = None
        try:
            if len(body) > MAX_BODY:
                raise ApiError(413, "BODY_TOO_LARGE", "request body too large", "حجم درخواست بیش از حد مجاز است")
            handler, params, route_pattern = self.router.match(method, path)
            if handler is None:
                if route_pattern == "METHOD":
                    raise ApiError(405, "METHOD_NOT_ALLOWED", f"{method} not allowed on {path}", "این متد برای این مسیر مجاز نیست")
                raise ApiError(404, "ROUTE_NOT_FOUND", f"no route for {path}", "این مسیر وجود ندارد")
            req.params = params
            resp = handler(req, ctx)
            conn.commit()
        except (ApiError, InvalidTransition, sqlite3.IntegrityError) as exc:
            conn.rollback()
            if isinstance(exc, InvalidTransition):
                exc = ApiError(409, "INVALID_STATE_TRANSITION", str(exc), "این تغییر وضعیت مجاز نیست")
            elif isinstance(exc, sqlite3.IntegrityError):
                exc = ApiError(409, "INTEGRITY_CONFLICT", "database constraint violated", "تعارض داده؛ درخواست تکراری یا نامعتبر است")
            error_code = exc.code
            if is_api or self.html_error is None:
                resp = json_response(error_body(exc, ctx.correlation_id), exc.status)
            else:
                resp = html_response(self.html_error(exc, ctx.correlation_id), exc.status)
        except Exception:  # pragma: no cover - safety net
            conn.rollback()
            traceback.print_exc()
            error_code = "INTERNAL_ERROR"
            exc = ApiError(500, "INTERNAL_ERROR", "unexpected server error", "خطای پیش‌بینی‌نشده؛ correlation_id را برای تیم فنی بفرستید")
            resp = json_response(error_body(exc, ctx.correlation_id), 500) if is_api or not self.html_error \
                else html_response(self.html_error(exc, ctx.correlation_id), 500)
        resp.headers.append(("X-Correlation-ID", ctx.correlation_id))
        duration = int((time.perf_counter() - started) * 1000)
        if not path.startswith("/static/"):
            try:
                level = "ERROR" if resp.status >= 500 else "WARNING" if resp.status >= 400 else "INFO"
                msg = f"{method} {path} -> {resp.status}" + (f" {error_code}" if error_code else "")
                log_event(conn, self.settings, level, "http.request", msg, correlation_id=ctx.correlation_id,
                          endpoint=f"{method} {route_pattern if route_pattern not in (None, 'METHOD') else path}",
                          status_code=resp.status, duration_ms=duration, **ctx.log_fields)
                conn.commit()
            finally:
                conn.close()
        else:
            conn.close()
        return resp


class ClientResponse:
    def __init__(self, status, headers, body):
        self.status_code, self.headers, self.content = status, headers, body

    @property
    def text(self):
        return self.content.decode("utf-8")

    def json(self):
        return json.loads(self.content.decode("utf-8"))


class LabClient:
    """In-process HTTP client: builds a WSGI environ and calls the app.
    Used by tests, the seed script, diagnostics scenarios and the simulator's
    webhook delivery (so every call is logged exactly like a real request)."""

    def __init__(self, app: App, default_headers: dict | None = None):
        self.app = app
        self.default_headers = default_headers or {}

    def request(self, method, path, *, json_body=None, data: bytes | str | None = None, headers=None,
                form: dict | None = None) -> ClientResponse:
        hdrs = dict(self.default_headers)
        hdrs.update(headers or {})
        body = b""
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        elif form is not None:
            body = urlencode(form).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif data is not None:
            body = data.encode("utf-8") if isinstance(data, str) else data
        path_only, _, qs = path.partition("?")
        environ = {"REQUEST_METHOD": method.upper(), "PATH_INFO": path_only, "QUERY_STRING": qs,
                   "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body),
                   "SERVER_NAME": "lab.local", "SERVER_PORT": "0", "wsgi.url_scheme": "http"}
        for k, v in hdrs.items():
            if k.lower() == "content-type":
                environ["CONTENT_TYPE"] = v
            else:
                environ["HTTP_" + k.upper().replace("-", "_")] = v
        resp = self.app.handle(environ)
        return ClientResponse(resp.status, {k.lower(): v for k, v in resp.headers}, resp.body)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, **kw):
        return self.request("POST", path, **kw)

    def patch(self, path, **kw):
        return self.request("PATCH", path, **kw)
