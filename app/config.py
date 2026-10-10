"""Settings loaded from environment variables and an optional local .env file.

Only demo values exist in this project. Nothing here is a real credential.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    """Minimal .env reader (KEY=VALUE per line). Real env vars take priority."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    db_path: str
    log_path: str
    api_key: str
    webhook_secret: str
    host: str = "127.0.0.1"
    port: int = 8000
    internal_currency: str = "IRR"
    max_webhook_delivery_attempts: int = 3
    csv_max_bytes: int = 1_000_000
    csv_max_rows: int = 5_000
    write_log_file: bool = True

    def with_overrides(self, **kwargs) -> "Settings":
        return replace(self, **kwargs)

    @property
    def sensitive_values(self) -> list[str]:
        """Values that must never appear in logs."""
        return [v for v in (self.api_key, self.webhook_secret) if v]


def load_settings() -> Settings:
    _load_env_file(BASE_DIR / ".env")
    return Settings(
        db_path=os.environ.get("LAB_DB_PATH", str(BASE_DIR / "var" / "lab.sqlite3")),
        log_path=os.environ.get("LAB_LOG_PATH", str(BASE_DIR / "var" / "logs" / "app.jsonl")),
        api_key=os.environ.get("LAB_API_KEY", "lab_demo_api_key_change_me"),
        webhook_secret=os.environ.get("LAB_WEBHOOK_SECRET", "lab_demo_webhook_secret_change_me"),
        host=os.environ.get("LAB_HOST", "127.0.0.1"),
        port=int(os.environ.get("LAB_PORT", "8000")),
        max_webhook_delivery_attempts=int(os.environ.get("LAB_MAX_WEBHOOK_ATTEMPTS", "3")),
    )
