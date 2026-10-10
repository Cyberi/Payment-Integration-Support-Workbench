"""Re-render the five screenshots in screenshots/ from the current demo database.

Pages are fetched through the app itself (no mock HTML). Rendering uses WeasyPrint
(optional, not in the default install), so this is a static capture, not a browser run.

    pip install weasyprint pdf2image   # or any HTML->PNG tool you already have
    python -m scripts.render_screenshots
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from app.config import BASE_DIR, load_settings
from app.database import connect
from app.main import build_app
from app.web.framework import LabClient

OUT = BASE_DIR / "screenshots"


def main() -> None:
    import weasyprint  # optional dependency

    s = load_settings()
    app = build_app(s)
    client = LabClient(app)
    con = connect(s.db_path)
    pid = con.execute("SELECT payment_id FROM support_cases WHERE runbook_slug='invalid-signature'").fetchone()[0]
    run = con.execute("SELECT id FROM reconciliation_runs ORDER BY created_at DESC LIMIT 1").fetchone()[0]
    case = con.execute("SELECT case_number FROM support_cases WHERE runbook_slug='amount-unit-mismatch'").fetchone()[0]
    con.close()
    pages = {
        "01-dashboard": "/",
        "02-payment-timeline": f"/payments/{pid}",
        "03-support-case": f"/cases/{case}",
        "04-reconciliation-exceptions": f"/reconciliation/runs/{run}",
        "05-api-diagnostic": f"/diagnostics/query?name=webhook_events_for_payment&params=payment_id%3D{pid}",
    }
    css = weasyprint.CSS(string="@page{size:1280px 2000px;margin:0} body{width:1280px}")
    with tempfile.TemporaryDirectory() as tmp:
        for name, path in pages.items():
            html = Path(tmp) / f"{name}.html"
            html.write_text(client.get(path).text, encoding="utf-8")
            pdf = Path(tmp) / f"{name}.pdf"
            weasyprint.HTML(filename=str(html)).write_pdf(str(pdf), stylesheets=[css])
            subprocess.run(["pdftoppm", "-png", "-r", "96", "-singlefile", str(pdf), str(OUT / name)], check=True)
            print("wrote", OUT / f"{name}.png")


if __name__ == "__main__":
    main()
