# Support Case Practice Lab

A small, local training project for practising application-support triage with synthetic payment-related incidents. The interface is Persian and right-to-left; the Python code uses only the standard library.

**Simulation only:** it does not connect to a bank, payment provider, payment gateway, or real customer data. The incident records are fixed fixtures. The app stores only local analyst notes and case status in SQLite.

[Persian guide](README.fa.md) · [Incident-handling checklist](docs/incident-handling-fa.md)

## What it demonstrates

- Reading a support report and identifying case, order, payment, and correlation IDs.
- Comparing internal status, provider evidence, and webhook records without assuming one source is always correct.
- Recording investigation notes and a reasoned resolution.
- Keeping resolved cases read-only and preventing resolution before an investigation note exists.
- Using SQLite parameterized queries and escaping dynamic text before rendering HTML.
- Running a small Python application and its tests without runtime dependencies.

## Included practice cases

1. A payment remains pending while synthetic provider evidence says succeeded and no webhook is found.
2. The same webhook event is delivered twice; the sample evidence says the second delivery was marked duplicate.
3. A reconciliation report has an amount mismatch whose unit is unclear; the safe next step is verification, not editing the amount.

The fixtures are deliberately small. They are not a payment engine and do not attempt to model every real-world provider rule.

## Requirements

- Python 3.11 or newer
- No third-party runtime packages

## Run locally

From the project directory:

~~~bash
python -m support_lab --reset
~~~

Open http://127.0.0.1:8000 in a browser. The server binds to loopback only; it is not intended to be exposed to a LAN or the public internet. Press Ctrl+C to stop it.

Optional settings:

~~~powershell
$env:SUPPORT_LAB_PORT = "8010"
$env:SUPPORT_LAB_DB = "var/my-practice.sqlite3"
python -m support_lab
~~~

~~~bash
SUPPORT_LAB_PORT=8010 SUPPORT_LAB_DB=var/my-practice.sqlite3 python -m support_lab
~~~

The --reset option deletes the selected local demo database and seeds the three sample cases again. Do not use it on a database you need to preserve.

## Run tests

~~~bash
python -m unittest discover -s tests -v
~~~

The tests cover fixture seeding, note persistence, investigation/resolution rules, read-only resolved cases, and HTML escaping. They do not establish that the program is production-ready.

## Project structure

~~~text
support_lab/
  data.py
  store.py
  web.py
  __main__.py
tests/
docs/
~~~

## Limitations

- No login, role management, CSRF protection, audit-grade retention, concurrency testing, production monitoring, or real payment integration.
- Demo data is static and cannot prove that a real bank or PSP behaved this way.
- This is a learning/portfolio exercise, not evidence of professional operation of a financial system.
- Use AI assistance transparently and make sure you understand and can explain every part you present in an interview. Do not claim production experience or expertise that this project does not demonstrate.

## License

MIT. See [LICENSE](LICENSE).
