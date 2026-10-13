"""End-to-end tests against the real app (in-process HTTP), one temp DB per class."""
import json
import unittest

from app.support import scenarios
from app.ids import new_id
from tests.helpers import db, make_env

AMOUNT = 250_000


class PaymentFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings, cls.app, cls.client = make_env()

    def _order(self, merchant="mer_sefid"):
        r = self.client.post("/api/v1/orders", json_body={"merchant_id": merchant, "amount_irr": AMOUNT})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["order"]["id"]

    def _pay(self, order_id, key=None, simulate=None):
        h = {"Idempotency-Key": key or new_id("idem")}
        if simulate:
            h["X-Lab-Simulate"] = simulate
        return self.client.post("/api/v1/payments", json_body={"order_id": order_id, "amount_irr": AMOUNT}, headers=h)

    def _state(self, pid):
        d = self.client.get(f"/api/v1/payments/{pid}").json()
        return d["payment"]["status"], d["order"]["status"], d["payment"]["review_reason"]

    # 1. happy path
    def test_happy_path_end_to_end(self):
        oid = self._order()
        r = self._pay(oid)
        self.assertEqual(r.status_code, 201)
        pid = r.json()["payment"]["id"]
        self.assertEqual(self._state(pid), ("pending", "pending_payment", None))
        c = self.client.post(f"/api/v1/simulator/payments/{pid}/complete", json_body={"outcome": "succeeded"})
        self.assertEqual(c.status_code, 200, c.text)
        self.assertEqual(c.json()["deliveries"][0]["response"]["result"], "processed")
        self.assertEqual(self._state(pid), ("succeeded", "paid", None))
        tl = self.client.get(f"/api/v1/timeline?payment_id={pid}").json()
        self.assertTrue(any(i["type"] == "payment.status_changed" or i["type"] == "payment.succeeded" for i in tl["items"])
                        or any("succeeded" in (i["summary"] or "") for i in tl["items"]))

    # 2. timeout keeps unknown until verify
    def test_timeout_is_unknown_not_failed_and_verify_resolves(self):
        oid = self._order()
        r = self._pay(oid, simulate="provider_timeout")
        self.assertEqual(r.status_code, 202)
        pid = r.json()["payment"]["id"]
        self.assertEqual(self._state(pid)[0], "unknown")
        self.assertEqual(r.json()["payment"]["review_reason"], "provider_timeout")
        v = self.client.post(f"/api/v1/payments/{pid}/verify")
        self.assertEqual(v.status_code, 200, v.text)
        self.assertEqual(self._state(pid)[0], "pending")

    # 3. duplicate webhook has no second effect
    def test_duplicate_webhook_has_no_second_effect(self):
        oid = self._order()
        pid = self._pay(oid).json()["payment"]["id"]
        c = self.client.post(f"/api/v1/simulator/payments/{pid}/complete",
                             json_body={"outcome": "succeeded", "webhook": "duplicate"})
        results = [d["response"]["result"] for d in c.json()["deliveries"]]
        self.assertEqual(results, ["processed", "duplicate"])
        conn = db(self.settings)
        n = conn.execute("SELECT COUNT(*) FROM audit_events WHERE payment_id = ? AND event_type = 'payment.status_changed'"
                         " AND to_status = 'succeeded'", (pid,)).fetchone()[0]
        conn.close()
        self.assertEqual(n, 1)

    # 4. invalid signature is rejected and stored
    def test_invalid_signature_is_rejected(self):
        oid = self._order()
        pid = self._pay(oid).json()["payment"]["id"]
        c = self.client.post(f"/api/v1/simulator/payments/{pid}/complete",
                             json_body={"outcome": "succeeded", "webhook": "invalid_signature"})
        self.assertEqual(c.json()["deliveries"][0]["http_status"], 401)
        self.assertEqual(self._state(pid)[0], "pending")
        conn = db(self.settings)
        rej = conn.execute("SELECT COUNT(*) FROM provider_events WHERE payment_id = ? AND status = 'rejected'", (pid,)).fetchone()[0]
        conn.close()
        self.assertEqual(rej, 1)

    def test_unsigned_webhook_is_rejected_without_body_processing(self):
        r = self.client.post("/api/v1/webhooks/navapay", data=b'{"event_id":"x"}', headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["error"]["code"], "INVALID_SIGNATURE")

    # 5. idempotency
    def test_idempotency_same_body_replays_and_different_body_conflicts(self):
        oid = self._order()
        key = new_id("idem")
        a = self._pay(oid, key=key)
        b = self._pay(oid, key=key)
        self.assertEqual(b.status_code, 201)
        self.assertEqual(b.headers["idempotent-replayed"], "true")
        self.assertEqual(a.json()["payment"]["id"], b.json()["payment"]["id"])
        c = self.client.post("/api/v1/payments", json_body={"order_id": oid, "amount_irr": AMOUNT, "note": "x"},
                             headers={"Idempotency-Key": key})
        self.assertEqual(c.status_code, 409)
        self.assertEqual(c.json()["error"]["code"], "IDEMPOTENCY_KEY_REUSED")

    # 6. refunds never exceed the paid amount
    def test_refund_cannot_exceed_paid_amount(self):
        oid = self._order()
        pid = self._pay(oid).json()["payment"]["id"]
        self.client.post(f"/api/v1/simulator/payments/{pid}/complete", json_body={"outcome": "succeeded"})
        ok = self.client.post(f"/api/v1/payments/{pid}/refunds", json_body={"amount_irr": 100_000},
                              headers={"Idempotency-Key": new_id("idem")})
        self.assertEqual(ok.status_code, 201)
        over = self.client.post(f"/api/v1/payments/{pid}/refunds", json_body={"amount_irr": 200_000},
                                headers={"Idempotency-Key": new_id("idem")})
        self.assertEqual(over.status_code, 422)
        rest = self.client.post(f"/api/v1/payments/{pid}/refunds", json_body={"amount_irr": 150_000},
                                headers={"Idempotency-Key": new_id("idem")})
        self.assertEqual(rest.status_code, 201)
        self.assertEqual(self._state(pid)[0], "refunded")

    # 7. unit conversion in the adapter + mismatch flagged
    def test_unit_misconfiguration_is_flagged_not_marked_paid(self):
        oid = self._order(merchant="mer_daneshyar_irt")
        pid = self._pay(oid).json()["payment"]["id"]
        conn = db(self.settings)
        sent = conn.execute("SELECT sent_amount, sent_amount_unit FROM payments WHERE id = ?", (pid,)).fetchone()
        conn.close()
        self.assertEqual((sent[0], sent[1]), (25_000, "IRT"))
        c = self.client.post(f"/api/v1/simulator/payments/{pid}/complete", json_body={"outcome": "succeeded"})
        self.assertEqual(c.json()["deliveries"][0]["response"]["result"], "failed")
        self.assertEqual(self._state(pid), ("pending", "pending_payment", "amount_mismatch"))

    # 8. reconciliation detects the required exception types
    def test_reconciliation_detects_required_exception_types(self):
        a = scenarios.run(self.app, "refund-limit")["payment_id"]
        b = scenarios.run(self.app, "duplicate-webhook")["payment_id"]
        c = scenarios.run(self.app, "invalid-signature")["payment_id"]
        conn = db(self.settings)
        ref = {pid: conn.execute("SELECT provider_reference FROM payments WHERE id = ?", (pid,)).fetchone()[0]
               for pid in (a, b, c)}
        conn.close()
        csv_text = "\n".join([
            "provider_reference,merchant_reference,transaction_date,amount,currency,status,refunded_amount",
            f"{ref[a]},{a},2026-10-10,250000,IRR,SUCCEEDED,100000",   # matched
            f"{ref[a]},{a},2026-10-10,250000,IRR,SUCCEEDED,100000",   # duplicate_provider_reference
            f"{ref[b]},{b},2026-10-10,249990,IRR,SUCCEEDED,0",       # amount_mismatch
            f"{ref[c]},{c},2026-10-10,250000,IRR,FAILED,0",          # status_mismatch
            "NP-ZZZ99999,pay_nobody_0001,2026-10-10,120000,IRR,SUCCEEDED,0",  # provider_only
            "",
        ])
        up = self.client.post("/api/v1/reconciliation/runs?filename=t.csv", data=csv_text.encode("utf-8"),
                              headers={"Content-Type": "text/csv"})
        self.assertEqual(up.status_code, 201, up.text)
        counts = up.json()["exception_counts"]
        for t in ("duplicate_provider_reference", "amount_mismatch", "status_mismatch", "provider_only"):
            self.assertIn(t, counts)
            self.assertEqual(counts[t], 1)

    # 9. timeline by correlation ID
    def test_timeline_by_correlation_id_contains_order_payment_and_webhook(self):
        corr = new_id("corr")
        r = self.client.post("/api/v1/orders", json_body={"merchant_id": "mer_sefid", "amount_irr": AMOUNT},
                             headers={"X-Correlation-ID": corr})
        oid = r.json()["order"]["id"]
        self.client.post("/api/v1/payments", json_body={"order_id": oid, "amount_irr": AMOUNT},
                         headers={"Idempotency-Key": new_id("idem"), "X-Correlation-ID": corr})
        tl = self.client.get(f"/api/v1/timeline?correlation_id={corr}").json()
        sources = {i["source"] for i in tl["items"]}
        self.assertIn("audit", sources)
        self.assertIn("log", sources)
        self.assertTrue(all(i["correlation_id"] == corr for i in tl["items"] if i["source"] != "support"))

    # 10. case closure rules
    def test_case_cannot_be_resolved_or_closed_without_evidence(self):
        body = {"title": "t", "problem_type": "other", "severity": "P3", "business_impact": "x",
                "merchant_report": "y"}
        cid = self.client.post("/api/v1/support/cases", json_body=body).json()["case"]["case_number"]
        r = self.client.patch(f"/api/v1/support/cases/{cid}", json_body={"status": "investigating"})
        self.assertEqual(r.status_code, 200)
        r = self.client.patch(f"/api/v1/support/cases/{cid}", json_body={"status": "resolved"})
        self.assertEqual(r.status_code, 422)
        r = self.client.patch(f"/api/v1/support/cases/{cid}", json_body={
            "status": "resolved", "resolution": "fixed", "validation": "retested", "root_cause_status": "unknown"})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.patch(f"/api/v1/support/cases/{cid}", json_body={"status": "closed"})
        self.assertEqual(r.status_code, 422)
        r = self.client.patch(f"/api/v1/support/cases/{cid}", json_body={
            "status": "closed", "closure_summary": "done", "merchant_message": "ok"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["case"]["status"], "closed")

    # 11. no secrets in logs
    def test_logs_never_contain_secrets_or_signatures(self):
        oid = self._order()
        pid = self._pay(oid).json()["payment"]["id"]
        self.client.post(f"/api/v1/simulator/payments/{pid}/complete", json_body={"outcome": "succeeded", "webhook": "invalid_signature"})
        self.client.get("/api/v1/payments/" + pid, headers={"X-API-Key": "wrong-key-with-4111111111111111"})
        conn = db(self.settings)
        blob = json.dumps([dict(r) for r in conn.execute("SELECT * FROM app_logs")], ensure_ascii=False)
        conn.close()
        self.assertNotIn(self.settings.webhook_secret, blob)
        self.assertNotIn(self.settings.api_key, blob)
        self.assertNotIn("4111111111111111", blob)
        self.assertNotIn("sha256=", blob)

    # 12. input validation
    def test_invalid_inputs_return_422_with_field_details(self):
        r = self.client.post("/api/v1/orders", json_body={"merchant_id": "mer_sefid", "amount_irr": 12.5})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(r.json()["error"]["details"][0]["field"], "amount_irr")
        r = self.client.post("/api/v1/orders", json_body={"merchant_id": "nope", "amount_irr": AMOUNT})
        self.assertEqual(r.status_code, 404)
        r = self.client.post("/api/v1/orders", data=b"not json", headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("correlation_id", r.json())


    def test_ui_does_not_auto_confirm_root_cause_from_text(self):
        created = self.client.post("/api/v1/support/cases", json_body={
            "title": "Root cause status regression",
            "problem_type": "other",
            "severity": "P3",
            "business_impact": "Synthetic test only",
            "merchant_report": "A synthetic report for the UI regression test",
        })
        self.assertEqual(created.status_code, 201, created.text)
        case_number = created.json()["case"]["case_number"]

        updated = self.client.post(
            f"/ui/cases/{case_number}/status",
            form={"status": "investigating", "root_cause": "A plausible but unverified hypothesis"},
        )
        self.assertIn(updated.status_code, (302, 303))
        stored = self.client.get(f"/api/v1/support/cases/{case_number}").json()["case"]
        self.assertEqual(stored["root_cause_status"], "not_determined")

        self.client.post(
            f"/ui/cases/{case_number}/status",
            form={"status": "investigating", "root_cause": "Verified with event timeline",
                  "root_cause_status": "confirmed"},
        )
        stored = self.client.get(f"/api/v1/support/cases/{case_number}").json()["case"]
        self.assertEqual(stored["root_cause_status"], "confirmed")


if __name__ == "__main__":
    unittest.main()
