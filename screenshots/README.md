# Screenshots

The first five images are static renders of the real HTML served by the app (after `python -m scripts.reset_demo_data`), rendered to PNG with WeasyPrint at 1280px width. They are not live browser captures. `06-support-case-browser.png` is different: it is a full-page screenshot captured by Playwright/Chromium from the running local app in GitHub Actions after seeding the synthetic demo data.

| File | What it proves |
|---|---|
| `01-dashboard.png` | Counters are live COUNT queries over the seeded database: payments needing review, open cases, webhook rejections and duplicates. |
| `02-payment-timeline.png` | One payment's audit trail, webhooks received (with signature validity), refunds, logs and support notes in one time-ordered list. |
| `03-support-case.png` | A case that follows the required 11-step structure: report, data needed, priority, checks, evidence, hypotheses, root cause status, resolution, validation, merchant message and closure. |
| `04-reconciliation-exceptions.png` | Exceptions from the synthetic provider CSV (amount mismatch, status mismatch, duplicate reference, provider-only, internal-only), each with a suggestion, a verification step and a link to a case. |
| `05-api-diagnostic.png` | A named read-only diagnostic query, its SQL, and its rows for one payment. |
| `06-support-case-browser.png` | Actual full-page Chromium capture of `/cases/CASE-1001`, including the root-cause status selector; the capture job waits for the selector to be visible before saving the PNG. |

To regenerate the first five static renders, use the existing render process for the pages listed above. To capture the browser screenshot locally, install the optional Playwright development tool, install Chromium, seed the app, start the server, and run `python -m scripts.capture_screenshot`. The capture script reads `LAB_SCREENSHOT_BASE_URL` if the local server is not at `http://127.0.0.1:8000`. The runtime workbench itself still uses only the Python standard library.
