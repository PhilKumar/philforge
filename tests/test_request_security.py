import unittest
from types import SimpleNamespace

from request_security import client_ip, request_rate_subject


class RequestSecurityTests(unittest.TestCase):
    def test_loopback_proxy_uses_valid_real_ip(self):
        self.assertEqual(client_ip("127.0.0.1", {"x-real-ip": "203.0.113.8"}), "203.0.113.8")

    def test_remote_peer_cannot_spoof_proxy_header(self):
        self.assertEqual(client_ip("198.51.100.4", {"x-real-ip": "203.0.113.8"}), "198.51.100.4")

    def test_invalid_forwarded_header_falls_back_to_peer(self):
        self.assertEqual(client_ip("::1", {"x-forwarded-for": "not-an-ip, 203.0.113.8"}), "::1")

    def test_rate_subject_includes_user_and_client_ip(self):
        request = SimpleNamespace(
            client=SimpleNamespace(host="127.0.0.1"),
            headers={"x-real-ip": "203.0.113.8"},
            state=SimpleNamespace(user_id=42),
        )
        self.assertEqual(request_rate_subject(request), "user:42:ip:203.0.113.8")


if __name__ == "__main__":
    unittest.main()
