import unittest

from journal_validation import JournalValidationError, clean_journal_payload, validate_journal_date


class JournalValidationTests(unittest.TestCase):
    def test_accepts_and_limits_supported_fields(self):
        clean = clean_journal_payload(
            {
                "asset": " NIFTY ",
                "strategy": "Breakout",
                "grade": "A",
                "went_well": "x" * 2100,
                "to_improve": "Patience",
                "mental_state": "Focused",
                "ignored": "not persisted",
            }
        )
        self.assertEqual(clean["asset"], "NIFTY")
        self.assertEqual(len(clean["went_well"]), 2000)
        self.assertNotIn("ignored", clean)

    def test_rejects_html_grade_class_injection(self):
        with self.assertRaises(JournalValidationError):
            clean_journal_payload({"grade": 'A" onclick="alert(1)'})

    def test_rejects_non_object_and_non_text(self):
        with self.assertRaises(JournalValidationError):
            clean_journal_payload([])
        with self.assertRaises(JournalValidationError):
            clean_journal_payload({"asset": {"unexpected": True}})

    def test_rejects_impossible_calendar_date(self):
        with self.assertRaises(JournalValidationError):
            validate_journal_date("2026-02-31")
        self.assertEqual(validate_journal_date("2026-07-17"), "2026-07-17")


if __name__ == "__main__":
    unittest.main()
