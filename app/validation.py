"""Small explicit validator (no external dependency). Collects all field errors
and raises one HTTP 422 with a details list."""
from __future__ import annotations

import re

from .errors import validation

ID_RE = re.compile(r"^[A-Za-z0-9_\-]{3,64}$")


class Validator:
    def __init__(self, data):
        if not isinstance(data, dict):
            raise validation([{"field": "$", "issue": "body must be a JSON object"}])
        self.data = data
        self.errors: list[dict] = []

    def _err(self, field, issue):
        self.errors.append({"field": field, "issue": issue})

    def int(self, field, *, required=True, min_value=None, max_value=None):
        if field not in self.data or self.data[field] is None:
            if required:
                self._err(field, "required")
            return None
        v = self.data[field]
        if isinstance(v, bool) or not isinstance(v, int):
            self._err(field, "must be an integer (no decimals, no strings)")
            return None
        if min_value is not None and v < min_value:
            self._err(field, f"must be >= {min_value}")
        if max_value is not None and v > max_value:
            self._err(field, f"must be <= {max_value}")
        return v

    def str(self, field, *, required=True, max_len=200, choices=None, pattern=None, min_len=1):
        if field not in self.data or self.data[field] in (None, ""):
            if required:
                self._err(field, "required")
            return None
        v = self.data[field]
        if not isinstance(v, str):
            self._err(field, "must be a string")
            return None
        v = v.strip()
        if len(v) < min_len or len(v) > max_len:
            self._err(field, f"length must be between {min_len} and {max_len}")
        if choices is not None and v not in choices:
            self._err(field, f"must be one of: {', '.join(choices)}")
        if pattern is not None and not pattern.match(v):
            self._err(field, "has an invalid format")
        return v

    def done(self):
        if self.errors:
            raise validation(self.errors)
