import unittest

from app.main import _is_loopback_host

from app.domain import money
from app.domain.states import InvalidTransition, ensure_transition, can_transition


class StateMachineTests(unittest.TestCase):
    def test_unknown_is_not_failed_on_timeout(self):
        self.assertTrue(can_transition("payment", "created", "unknown"))
        self.assertTrue(can_transition("payment", "unknown", "succeeded"))

    def test_invalid_transitions_raise(self):
        with self.assertRaises(InvalidTransition):
            ensure_transition("order", "cancelled", "paid")
        with self.assertRaises(InvalidTransition):
            ensure_transition("payment", "succeeded", "failed")
        with self.assertRaises(InvalidTransition):
            ensure_transition("case", "new", "closed")


class MoneyTests(unittest.TestCase):
    def test_irr_to_irt_is_exact_integer(self):
        self.assertEqual(money.to_unit(250_000, "IRT"), 25_000)
        self.assertEqual(money.to_irr(25_000, "IRT"), 250_000)

    def test_non_exact_conversion_is_refused(self):
        with self.assertRaises(money.MoneyError):
            money.to_unit(250_005, "IRT")

    def test_floats_are_refused(self):
        with self.assertRaises(money.MoneyError):
            money.to_irr(1.5, "IRR")
        with self.assertRaises(money.MoneyError):
            money.to_irr(True, "IRR")


class AppBindingTests(unittest.TestCase):
    def test_loopback_hosts_are_allowed(self):
        self.assertTrue(_is_loopback_host("127.0.0.1"))
        self.assertTrue(_is_loopback_host("::1"))
        self.assertTrue(_is_loopback_host("localhost"))

    def test_network_hosts_are_rejected(self):
        self.assertFalse(_is_loopback_host("0.0.0.0"))
        self.assertFalse(_is_loopback_host("192.168.1.10"))
        self.assertFalse(_is_loopback_host("example.com"))


if __name__ == "__main__":
    unittest.main()
