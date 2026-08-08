import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LoggingSafetyTests(unittest.TestCase):
    def test_order_payloads_and_crash_file_are_not_logged(self):
        broker_source = (ROOT / "broker" / "dhan.py").read_text(encoding="utf-8")
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        error_source = (ROOT / "error_handlers.py").read_text(encoding="utf-8")
        self.assertNotIn("Order payload:", broker_source)
        self.assertNotIn("Forever order payload:", broker_source)
        self.assertNotIn("Super Order payload:", broker_source)
        self.assertNotIn("Async order payload:", broker_source)
        self.assertNotIn('os.path.join(_HERE, "crash.log")', app_source)
        self.assertNotIn("crash.log", error_source)

    def test_server_errors_never_return_tracebacks(self):
        from error_handlers import _build_response

        response = _build_response(500, exc=RuntimeError("secret internal path"))
        self.assertNotIn("debug", response["error"])
        self.assertNotIn("detail", response["error"])


if __name__ == "__main__":
    unittest.main()
