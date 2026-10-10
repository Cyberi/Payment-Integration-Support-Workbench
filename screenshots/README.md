# Screenshots

Captured from the real HTML the app serves (after `python -m scripts.reset_demo_data`), rendered to PNG with WeasyPrint at 1280px width. This is a static render, not a live browser session, so hover and JavaScript states are not shown (the console needs no JavaScript).

| File | What it proves |
|---|---|
| `01-dashboard.png` | Counters are live COUNT queries over the seeded database: payments needing review, open cases, webhook rejections and duplicates. |
| `02-payment-timeline.png` | One payment's audit trail, webhooks received (with signature validity), refunds, logs and support notes in one time-ordered list. |
| `03-support-case.png` | A case that follows the required 11-step structure: report, data needed, priority, checks, evidence, hypotheses, root cause status, resolution, validation, merchant message and closure. |
| `04-reconciliation-exceptions.png` | Exceptions from the synthetic provider CSV (amount mismatch, status mismatch, duplicate reference, provider-only, internal-only), each with a suggestion, a verification step and a link to a case. |
| `05-api-diagnostic.png` | A named read-only diagnostic query, its SQL, and its rows for one payment. |

To regenerate: start the app, open the pages, or re-run the render described above.
