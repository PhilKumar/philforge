import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DeployHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.unit = (ROOT / "deploy" / "philforge.service").read_text(encoding="utf-8")

    def test_service_binds_only_to_loopback(self):
        self.assertIn("--host 127.0.0.1", self.unit)

    def test_service_runs_pythons_own_loop_and_restarts_without_a_core_dump(self):
        # 2026-11-02: uvloop segfaulted mid-order, and writing its core kept the
        # site down 4.5 minutes.
        self.assertIn("--loop asyncio", self.unit)
        self.assertIn("LimitCORE=0", self.unit)

    def test_service_keeps_both_application_state_roots_writable(self):
        self.assertIn(
            "ReadWritePaths=/home/ec2-user/philforge /home/ec2-user/.local/share/philforge",
            self.unit,
        )

    def test_service_has_minimum_privilege_controls(self):
        for setting in (
            "UMask=0077",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "PrivateDevices=true",
            "ProtectSystem=full",
            "ProtectHome=read-only",
            "CapabilityBoundingSet=",
            "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        ):
            with self.subTest(setting=setting):
                self.assertIn(setting, self.unit)


if __name__ == "__main__":
    unittest.main()
