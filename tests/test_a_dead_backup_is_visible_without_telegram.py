"""A failed backup must be visible even when Telegram is dead.

Found 2026-09-24: the nightly Google Drive upload had failed six days running
("rclone is not installed" after the EC2 move) and nobody knew, because the
only alarm — philforge-backup-alert — shouts over Telegram, and the bot token
was dead at the same time. Two safety nets, one shared dependency, both silent.

So the app itself is now the second channel: /api/health reads artefacts left
on disk by the backup, which no outage can suppress. These tests pin the two
ends of that — the scripts write the files, and the health check reads them.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ALERT_SCRIPT = (ROOT / "deploy" / "philforge-backup-alert").read_text(encoding="utf-8")
UPLOAD_SCRIPT = (ROOT / "deploy" / "philforge-upload-backup").read_text(encoding="utf-8")


def _load_app_health():
    import app

    return app._backup_health


class TheAlarmIsWrittenDownNotJustSent(unittest.TestCase):
    def test_the_failure_is_recorded_before_telegram_is_attempted(self):
        record_at = ALERT_SCRIPT.index("record_failure(unit,")
        token_at = ALERT_SCRIPT.index('read_env_value("TELEGRAM_BOT_TOKEN")')
        self.assertLess(
            record_at,
            token_at,
            "the failure must be on disk before anything Telegram-shaped can exit early",
        )

    def test_a_rate_limited_run_still_left_a_record(self):
        self.assertIn("recorded_on_disk=yes", ALERT_SCRIPT)

    def test_delivery_success_is_recorded_too(self):
        self.assertIn("telegram_delivered=True", ALERT_SCRIPT)

    def test_the_uploader_leaves_a_receipt(self):
        self.assertIn("offsite-receipt.json", UPLOAD_SCRIPT)
        self.assertIn("completed_at_epoch", UPLOAD_SCRIPT)
        self.assertLess(
            UPLOAD_SCRIPT.index("remote_sha"),
            UPLOAD_SCRIPT.index("offsite-receipt.json"),
            "the receipt must only be written after the remote copy is verified",
        )


class TheDeadManSwitchAlarmsOnSilence(unittest.TestCase):
    """The box being gone is the failure no on-box alarm can report.

    So the ping fires on SUCCESS only, and the external watcher alarms when it
    stops arriving — a failed snapshot, a failed upload, a dead server and a cut
    network are then indistinguishable, which is exactly what we want.
    """

    def test_the_ping_is_the_last_thing_the_uploader_does(self):
        ping = UPLOAD_SCRIPT.index("PHILFORGE_HEALTHCHECK_URL")
        for earlier in ("remote_sha", "offsite-receipt.json"):
            self.assertLess(
                UPLOAD_SCRIPT.index(earlier),
                ping,
                f"the ping must come after {earlier} — otherwise it reports health it has not proven",
            )

    def test_there_is_no_ping_on_the_failure_paths(self):
        """A ping inside a failure branch would tell the watcher all is well."""
        for line in UPLOAD_SCRIPT.splitlines():
            if "curl" in line and "hc_url" in line:
                self.assertNotIn("||", line)

    def test_a_missing_url_is_not_an_error(self):
        self.assertIn('if [[ -n "$hc_url" ]]', UPLOAD_SCRIPT)

    def test_a_failed_ping_does_not_fail_the_backup(self):
        self.assertIn("the backup itself is fine", UPLOAD_SCRIPT)


class TheHealthCheckReadsThoseFiles(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        import config

        self._old = config.BACKUP_ROOT
        config.BACKUP_ROOT = str(self.root)
        self.addCleanup(self._dir.cleanup)
        self.addCleanup(lambda: setattr(config, "BACKUP_ROOT", self._old))

    def _write_snapshot(self, age_hours: float) -> None:
        archive = self.root / "philforge-backup-test.tar.gz"
        archive.write_text("x")
        when = time.time() - age_hours * 3600
        os.utime(archive, (when, when))
        (self.root / "latest.tar.gz").symlink_to(archive)

    def _write_receipt(self, age_hours: float) -> None:
        (self.root / "offsite-receipt.json").write_text(
            json.dumps(
                {
                    "archive": "philforge-backup-test.tar.gz",
                    "completed_at_epoch": time.time() - age_hours * 3600,
                }
            )
        )

    def test_a_fresh_backup_with_a_fresh_offsite_copy_is_ok(self):
        self._write_snapshot(2)
        self._write_receipt(2)
        health = _load_app_health()()
        self.assertTrue(health["ok"], health["problems"])
        self.assertEqual(health["problems"], [])

    def test_a_snapshot_that_never_reached_drive_is_not_ok(self):
        """The real 24-Sep bug: local backups perfect, offsite silently absent."""
        self._write_snapshot(2)
        health = _load_app_health()()
        self.assertFalse(health["ok"])
        self.assertIn("no offsite copy has ever been confirmed", health["problems"])

    def test_an_offsite_copy_that_stopped_six_days_ago_is_not_ok(self):
        self._write_snapshot(2)
        self._write_receipt(24 * 6)
        health = _load_app_health()()
        self.assertFalse(health["ok"])
        self.assertTrue(any("offsite copy is" in p for p in health["problems"]))

    def test_a_recorded_failure_is_surfaced_even_when_undelivered(self):
        self._write_snapshot(2)
        self._write_receipt(2)
        (self.root / "last-failure.json").write_text(
            json.dumps(
                {
                    "at": "2026-09-24T09:09:54+05:30",
                    "unit": "philforge-backup.service",
                    "reason": "result=exit-code exit_status=1",
                    "telegram_delivered": False,
                }
            )
        )
        health = _load_app_health()()
        self.assertIsNotNone(health["last_failure"])
        self.assertFalse(health["last_failure"]["telegram_delivered"])
        self.assertIn("exit_status=1", health["last_failure"]["reason"])

    def test_a_failure_after_the_last_good_copy_is_not_ok(self):
        """The real 25-Sep bug. rclone's config was unreadable to the service
        user, so the upload failed while yesterday's receipt was only 17h old.
        Both ages were inside the 36h window, so this endpoint said ok:true and
        Phil learnt of it from Telegram — the one channel it must not rely on."""
        self._write_snapshot(0.2)
        self._write_receipt(17)
        (self.root / "last-failure.json").write_text(
            json.dumps(
                {
                    "at": "2026-09-25T09:12:14+05:30",
                    "at_epoch": time.time() - 600,  # AFTER the receipt
                    "unit": "philforge-backup.service",
                    "reason": "result=exit-code exit_status=1",
                    "telegram_delivered": True,
                }
            )
        )
        health = _load_app_health()()
        self.assertFalse(health["ok"])
        self.assertTrue(any("failed after the last good copy" in p for p in health["problems"]), health["problems"])

    def test_an_old_failure_before_a_good_copy_does_not_keep_it_red(self):
        """Once a later upload succeeds, yesterday's failure must not stick."""
        self._write_snapshot(0.2)
        self._write_receipt(1)
        (self.root / "last-failure.json").write_text(
            json.dumps(
                {
                    "at": "2026-09-24T09:09:54+05:30",
                    "at_epoch": time.time() - 24 * 3600,  # BEFORE the receipt
                    "unit": "philforge-backup.service",
                    "reason": "result=exit-code exit_status=1",
                    "telegram_delivered": True,
                }
            )
        )
        health = _load_app_health()()
        self.assertTrue(health["ok"], health["problems"])
        self.assertIsNotNone(health["last_failure"])

    def test_nothing_at_all_reads_as_broken_not_as_fine(self):
        health = _load_app_health()()
        self.assertFalse(health["ok"])
        self.assertIn("no local snapshot has ever been recorded", health["problems"])


if __name__ == "__main__":
    unittest.main()
