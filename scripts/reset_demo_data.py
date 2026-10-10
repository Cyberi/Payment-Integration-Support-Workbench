"""Delete the local database and rebuild it from scratch.

    python -m scripts.reset_demo_data
"""
from __future__ import annotations

import sys

from app.config import load_settings
from app.database import reset_db
from scripts.seed_demo_data import seed


def main() -> None:
    settings = load_settings()
    reset_db(settings.db_path)
    summary = seed(settings)
    print(f"reset done. merchants={summary['merchants']} scenarios_ok={summary['scenarios_ok']}/{summary['scenarios']} "
          f"cases={summary['cases']} reconciliation_exceptions={summary['exceptions']}")
    sys.exit(0 if summary["scenarios_ok"] == summary["scenarios"] else 1)


if __name__ == "__main__":
    main()
