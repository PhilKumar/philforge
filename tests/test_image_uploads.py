import unittest
from io import BytesIO

from PIL import Image

from image_uploads import ImageValidationError, sanitize_image


class ImageUploadTests(unittest.TestCase):
    @staticmethod
    def _image_bytes(fmt: str = "PNG", size: tuple[int, int] = (8, 6)) -> bytes:
        output = BytesIO()
        Image.new("RGB", size, (12, 34, 56)).save(output, format=fmt)
        return output.getvalue()

    def test_rewrites_supported_image_from_signature(self):
        result = sanitize_image(self._image_bytes("PNG"), "image/png")
        self.assertEqual(result.extension, ".png")
        self.assertEqual(result.content_type, "image/png")
        self.assertEqual((result.width, result.height), (8, 6))
        self.assertTrue(result.data.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_rejects_non_image_with_image_content_type(self):
        with self.assertRaises(ImageValidationError):
            sanitize_image(b"<script>alert(1)</script>", "image/png")

    def test_rejects_declared_type_mismatch(self):
        with self.assertRaises(ImageValidationError):
            sanitize_image(self._image_bytes("JPEG"), "image/png")

    def test_rejects_unsupported_declared_type(self):
        with self.assertRaises(ImageValidationError):
            sanitize_image(self._image_bytes("PNG"), "image/svg+xml")


if __name__ == "__main__":
    unittest.main()
