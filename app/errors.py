"""Uniform API error type. Every error response has the same JSON shape:

{"error": {"code": "...", "message": "...", "message_fa": "...", "details": [...]},
 "correlation_id": "..."}
"""
from __future__ import annotations


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, message_fa: str = "",
                 details: list | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.message_fa = message_fa or message
        self.details = details or []


def bad_request(code, msg, fa="", details=None):
    return ApiError(400, code, msg, fa, details)


def unauthorized(code, msg, fa=""):
    return ApiError(401, code, msg, fa)


def not_found(what: str, ident: str):
    return ApiError(404, "NOT_FOUND", f"{what} '{ident}' was not found",
                    f"{what} با شناسه {ident} پیدا نشد")


def conflict(code, msg, fa="", details=None):
    return ApiError(409, code, msg, fa, details)


def validation(details: list, msg="Request validation failed", fa="داده‌های درخواست معتبر نیست"):
    return ApiError(422, "VALIDATION_ERROR", msg, fa, details)
