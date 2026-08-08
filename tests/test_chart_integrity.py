import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("PHILFORGE_PIN", "test-pin-not-real")
os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_SCRIP_MASTER", "0")
os.environ.setdefault("PHILFORGE_STARTUP_TRADE_BACKFILL", "0")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")
os.environ.setdefault("DHAN_CLIENT_ID", "dummy")
os.environ.setdefault("DHAN_ACCESS_TOKEN", "dummy")

import app as app_module


class ChartIntegrityTests(unittest.TestCase):
    def test_invalid_calendar_folders_do_not_crash_or_become_dates(self):
        sort_key, label = app_module._parse_day_folder("31-Feb-2026", year_hint=2026, month_hint=2)
        self.assertEqual(sort_key, "9999-99-31-Feb-2026")
        self.assertEqual(label, "31-Feb-2026")
        self.assertEqual(app_module._canonicalize_chart_day_folder_name("2026", "Feb-2026", "31-Feb-2026"), "")

    def test_new_day_folder_must_match_parent_month_and_year(self):
        self.assertEqual(
            app_module._canonicalize_chart_day_folder_name("2026", "Feb-2026", "12-Feb-2026"),
            "12-Feb-2026",
        )
        self.assertEqual(app_module._canonicalize_chart_day_folder_name("2026", "Feb-2026", "12-Mar-2026"), "")
        self.assertEqual(app_module._canonicalize_chart_day_folder_name("bad", "Feb-2026", "12-Feb-2026"), "")

    def test_upload_target_accepts_existing_legacy_folder_names(self):
        target = app_module._chart_upload_target("2026", "FEBRUARY_2026", "12_Feb_2026")
        self.assertEqual(target[:3], ("2026", "FEBRUARY_2026", "12_Feb_2026"))
        self.assertEqual(target[3].isoformat(), "2026-02-12")

    def test_upload_target_rejects_partial_or_mismatched_dates(self):
        with self.assertRaises(app_module.HTTPException):
            app_module._chart_upload_target("2026", None, None)
        with self.assertRaises(app_module.HTTPException):
            app_module._chart_upload_target("2026", "Mar-2026", "12-Feb-2026")

    def test_parallel_uploads_never_overwrite_each_other(self):
        payloads = [f"payload-{index}".encode() for index in range(12)]
        with tempfile.TemporaryDirectory() as tmp:
            with ThreadPoolExecutor(max_workers=12) as pool:
                results = list(
                    pool.map(
                        lambda payload: app_module._write_unique_chart(payload, tmp, "12-02-2026", ".png"),
                        payloads,
                    )
                )
            names = [name for name, _path in results]
            self.assertEqual(len(set(names)), len(payloads))
            stored = {Path(path).read_bytes() for _name, path in results}
            self.assertEqual(stored, set(payloads))


if __name__ == "__main__":
    unittest.main()
