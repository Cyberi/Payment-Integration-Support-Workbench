"""Entry point: python -m app.main  (serves http://127.0.0.1:8000)."""
from __future__ import annotations

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


def main() -> None:
    settings = load_settings()
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
