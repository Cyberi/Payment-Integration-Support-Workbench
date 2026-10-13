"""Entry point: python -m app.main  (serves http://127.0.0.1:8000)."""
from __future__ import annotations

import ipaddress
import sys
from wsgiref.simple_server import WSGIRequestHandler, make_server

from .config import load_settings
from .database import init_db
from .web import api, ui, ui_actions
from .web.framework import App, Router


class QuietHandler(WSGIRequestHandler):
    def log_message(self, fmt, *args):  # request logging is done in the app, as JSON
        return


def build_app(settings) -> App:
    router = Router()
    for r in (api.router, ui.router, ui_actions.router):
        router.routes.extend(r.routes)
    return App(settings, router, html_error=ui.html_error)


def _is_loopback_host(host: str) -> bool:
    """Return True only for localhost names and IP addresses marked loopback."""
    value = host.strip().lower()
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def main() -> None:
    settings = load_settings()
    if not _is_loopback_host(settings.host):
        raise SystemExit(
            "Refusing to expose this simulation-only support workbench on a network interface. "
            "Set LAB_HOST=127.0.0.1 (or localhost) to run it locally."
        )
    init_db(settings.db_path)
    app = build_app(settings)
    with make_server(settings.host, settings.port, app, handler_class=QuietHandler) as srv:
        print(f"Payment Integration Support Workbench: http://{settings.host}:{settings.port}  (Ctrl+C to stop)")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            sys.exit(0)


if __name__ == "__main__":
    main()
