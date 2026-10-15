import unittest

from support_lab.data import CASE_BY_ID
from support_lab.web import render_case_detail, render_dashboard


class RenderingTests(unittest.TestCase):
    def test_dashboard_renders_each_case(self):
        cases = [{**case, "status": "open", "resolution": "", "updated_at": "now"} for case in CASE_BY_ID.values()]
        page = render_dashboard(cases)
        for case in cases:
            self.assertIn(case["case_id"], page)
            self.assertIn(case["title"], page)
        self.assertIn('dir="rtl"', page)

    def test_dynamic_note_is_html_escaped(self):
        case = {**CASE_BY_ID["CASE-1001"], "status": "investigating", "resolution": "", "updated_at": "now"}
        page = render_case_detail(case, [{"note_id": 1, "note": '<script>alert("x")</script>', "created_at": "now"}])
        self.assertNotIn('<script>alert("x")</script>', page)
        self.assertIn("&lt;script&gt;", page)

    def test_unknown_case_is_not_in_fixture(self):
        self.assertNotIn("CASE-9999", CASE_BY_ID)


if __name__ == "__main__":
    unittest.main()
