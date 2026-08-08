import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DeployHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.unit = (ROOT / "deploy" / "philforge.service").read_text(encoding="utf-8")

    def test_service_binds_only_to_loopback(self):
        self.assertIn("--host 127.0.0.1", self.unit)

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
