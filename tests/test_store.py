import tempfile
import unittest
from pathlib import Path

from support_lab import store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.sqlite3"
        store.initialize(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_seeds_three_cases_once(self):
        store.initialize(self.db_path)
        cases = store.list_cases(self.db_path)
        self.assertEqual(len(cases), 3)
        self.assertTrue(all(case["status"] == "open" for case in cases))

    def test_add_note_keeps_plain_text(self):
        store.add_note(self.db_path, "CASE-1001", "شناسه پرداخت با گزارش تطبیق داده شد.")
        notes = store.get_notes(self.db_path, "CASE-1001")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["note"], "شناسه پرداخت با گزارش تطبیق داده شد.")

    def test_cannot_resolve_without_investigation_and_note(self):
        with self.assertRaisesRegex(ValueError, "ابتدا بررسی"):
            store.resolve_case(self.db_path, "CASE-1001", "این یک جمع‌بندی تمرینی با شواهد کافی است.")
        store.start_investigation(self.db_path, "CASE-1001")
        with self.assertRaisesRegex(ValueError, "دست‌کم یک یادداشت"):
            store.resolve_case(self.db_path, "CASE-1001", "این یک جمع‌بندی تمرینی با شواهد کافی است.")

    def test_case_requires_a_note_before_resolution(self):
        store.start_investigation(self.db_path, "CASE-1001")
        store.add_note(self.db_path, "CASE-1001", "وضعیت ارائه‌دهنده و لاگ محلی مقایسه شد.")
        store.resolve_case(
            self.db_path,
            "CASE-1001",
            "وضعیت ارائه‌دهنده با شناسه پرداخت تطبیق داده شد؛ علت محتمل، نرسیدن رویداد است و باید ارجاع شود.",
        )
        case = store.get_case(self.db_path, "CASE-1001")
        self.assertEqual(case["status"], "resolved")
        self.assertIn("تطبیق داده شد", case["resolution"])

    def test_invalid_case_and_oversized_note_are_rejected(self):
        with self.assertRaises(ValueError):
            store.add_note(self.db_path, "CASE-9999", "یادداشت")
        with self.assertRaisesRegex(ValueError, "۱۰۰۰"):
            store.add_note(self.db_path, "CASE-1001", "x" * 1001)

    def test_resolved_case_is_read_only(self):
        store.start_investigation(self.db_path, "CASE-1001")
        store.add_note(self.db_path, "CASE-1001", "شواهد بررسی شد و ثبت گردید.")
        store.resolve_case(self.db_path, "CASE-1001", "بررسی شواهد انجام شد؛ نتیجه به تیم مسئول ارجاع و تأیید شد.")
        with self.assertRaisesRegex(ValueError, "حل‌شده"):
            store.add_note(self.db_path, "CASE-1001", "یادداشت جدید")


if __name__ == "__main__":
    unittest.main()
