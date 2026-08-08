import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class JournalClientIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "static" / "philforge-app.js").read_text(encoding="utf-8")

    def test_autosave_captures_the_selected_date_and_snapshot(self):
        self.assertIn("const dateStr = _cjCurrentDate;\n    const snapshot = _cjGetFormData();", self.source)
        self.assertIn("_cjSaveJournal(dateStr, snapshot)", self.source)

    def test_saves_are_serialized_and_http_failures_are_not_reported_as_success(self):
        self.assertIn("const previous = _cjSaveChains.get(dateStr) || Promise.resolve();", self.source)
        self.assertIn("if (!r.ok)", self.source)
        self.assertIn("Journal remains in this browser, but the server save failed.", self.source)

    def test_stale_loads_are_aborted_and_cannot_replace_a_new_date(self):
        self.assertIn("if (_cjLoadController) _cjLoadController.abort();", self.source)
        self.assertIn("generation !== _cjLoadGeneration || _cjCurrentDate !== dateStr", self.source)


if __name__ == "__main__":
    unittest.main()
