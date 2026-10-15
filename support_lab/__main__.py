"""Run with: python -m support_lab"""

import argparse
import os
from pathlib import Path
from http.server import ThreadingHTTPServer

from .store import initialize
from .web import SupportHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local support-case practice lab.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("SUPPORT_LAB_PORT", "8000")))
    parser.add_argument("--reset", action="store_true", help="delete the local demo database before seeding it")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    db_path = Path(os.environ.get("SUPPORT_LAB_DB", "var/support_lab.sqlite3"))
    if args.reset:
        db_path.unlink(missing_ok=True)
    initialize(db_path)
    handler = type("ConfiguredSupportHandler", (SupportHandler,), {"db_path": str(db_path)})
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"Support Case Lab is running at http://127.0.0.1:{args.port}")
    print("Local training data only. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
