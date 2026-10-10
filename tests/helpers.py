"""Shared test setup: each test class gets its own temporary SQLite file."""
from __future__ import annotations

import tempfile
from pathlib import Path

from app import ids
from app.config import load_settings
from app.database import connect, reset_db
from app.main import build_app
from app.web.framework import LabClient
from scripts.seed_demo_data import MERCHANTS


def make_env():
    tmp = Path(tempfile.mkdtemp(prefix="payment-lab-test-"))
    settings = load_settings().with_overrides(
        db_path=str(tmp / "test.sqlite3"), log_path=str(tmp / "app.jsonl"), write_log_file=False)
    ids.set_deterministic(None)
    reset_db(settings.db_path)
    conn = connect(settings.db_path)
    for mid, name, unit, note in MERCHANTS:
        conn.execute("INSERT INTO merchants (id, name, provider_amount_unit, note, created_at) VALUES (?,?,?,?,?)",
                     (mid, name, unit, note, ids.now_iso()))
    conn.commit()
    conn.close()
    app = build_app(settings)
    client = LabClient(app, {"X-API-Key": settings.api_key})
    return settings, app, client


def db(settings):
    return connect(settings.db_path)
