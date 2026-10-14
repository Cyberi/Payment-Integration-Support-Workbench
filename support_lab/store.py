"""Small SQLite store for analyst notes and case status."""

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from .data import CASES, CASE_BY_ID


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect(db_path: str | Path) -> sqlite3.Connection:
    path = str(db_path)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(db_path: str | Path) -> None:
    """Create the two tables and seed the three synthetic cases once."""
    with closing(_connect(db_path)) as db, db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS case_state (
                case_id TEXT PRIMARY KEY,
                status TEXT NOT NULL CHECK(status IN ('open','investigating','resolved')),
                resolution TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS case_notes (
                note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL REFERENCES case_state(case_id),
                note TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        now = _now()
        for case in CASES:
            db.execute(
                "INSERT OR IGNORE INTO case_state(case_id, status, resolution, updated_at) VALUES (?, 'open', '', ?)",
                (case["case_id"], now),
            )


def list_cases(db_path: str | Path) -> list[dict]:
    with closing(_connect(db_path)) as db:
        states = {
            row["case_id"]: dict(row)
            for row in db.execute("SELECT case_id, status, resolution, updated_at FROM case_state")
        }
    return [{**case, **states[case["case_id"]]} for case in CASES]


def get_case(db_path: str | Path, case_id: str) -> dict | None:
    case = CASE_BY_ID.get(case_id)
    if case is None:
        return None
    with closing(_connect(db_path)) as db:
        row = db.execute(
            "SELECT status, resolution, updated_at FROM case_state WHERE case_id = ?",
            (case_id,),
        ).fetchone()
    if row is None:
        return None
    return {**case, **dict(row)}


def get_notes(db_path: str | Path, case_id: str) -> list[dict]:
    with closing(_connect(db_path)) as db:
        rows = db.execute(
            "SELECT note_id, note, created_at FROM case_notes WHERE case_id = ? ORDER BY note_id",
            (case_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def add_note(db_path: str | Path, case_id: str, note: str) -> None:
    clean_note = note.strip()
    if case_id not in CASE_BY_ID:
        raise ValueError("پرونده پیدا نشد.")
    if not clean_note:
        raise ValueError("یادداشت نمی‌تواند خالی باشد.")
    if len(clean_note) > 1000:
        raise ValueError("یادداشت حداکثر می‌تواند ۱۰۰۰ نویسه باشد.")

    with closing(_connect(db_path)) as db, db:
        row = db.execute("SELECT status FROM case_state WHERE case_id = ?", (case_id,)).fetchone()
        if row is None:
            raise ValueError("پرونده پیدا نشد.")
        if row["status"] == "resolved":
            raise ValueError("برای پروندهٔ حل‌شده نمی‌توان یادداشت جدید ثبت کرد.")
        db.execute(
            "INSERT INTO case_notes(case_id, note, created_at) VALUES (?, ?, ?)",
            (case_id, clean_note, _now()),
        )


def start_investigation(db_path: str | Path, case_id: str) -> None:
    with closing(_connect(db_path)) as db, db:
        row = db.execute("SELECT status FROM case_state WHERE case_id = ?", (case_id,)).fetchone()
        if row is None:
            raise ValueError("پرونده پیدا نشد.")
        if row["status"] != "open":
            raise ValueError("فقط پروندهٔ باز را می‌توان وارد مرحلهٔ بررسی کرد.")
        db.execute(
            "UPDATE case_state SET status = 'investigating', updated_at = ? WHERE case_id = ?",
            (_now(), case_id),
        )


def resolve_case(db_path: str | Path, case_id: str, resolution: str) -> None:
    clean_resolution = resolution.strip()
    if len(clean_resolution) < 20:
        raise ValueError("جمع‌بندی باید دست‌کم ۲۰ نویسه و شامل شواهد و اقدام انجام‌شده باشد.")
    if len(clean_resolution) > 1500:
        raise ValueError("جمع‌بندی حداکثر می‌تواند ۱۵۰۰ نویسه باشد.")

    with closing(_connect(db_path)) as db, db:
        row = db.execute("SELECT status FROM case_state WHERE case_id = ?", (case_id,)).fetchone()
        if row is None:
            raise ValueError("پرونده پیدا نشد.")
        if row["status"] != "investigating":
            raise ValueError("برای حل پرونده، ابتدا بررسی را آغاز کن.")
        note_count = db.execute(
            "SELECT COUNT(*) FROM case_notes WHERE case_id = ?", (case_id,)
        ).fetchone()[0]
        if note_count == 0:
            raise ValueError("پیش از حل پرونده، دست‌کم یک یادداشت شواهد ثبت کن.")
        db.execute(
            "UPDATE case_state SET status = 'resolved', resolution = ?, updated_at = ? WHERE case_id = ?",
            (clean_resolution, _now(), case_id),
        )
