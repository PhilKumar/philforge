"""Decode and rewrite user-uploaded chart images before they reach storage."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_PIXELS = 25_000_000

_FORMAT_DETAILS = {
    "JPEG": (".jpg", "image/jpeg"),
    "PNG": (".png", "image/png"),
    "WEBP": (".webp", "image/webp"),
}


class ImageValidationError(ValueError):
    """The upload is not a safe supported raster image."""


@dataclass(frozen=True)
class SanitizedImage:
    data: bytes
    extension: str
    content_type: str
    width: int
    height: int


def _open_image(data: bytes) -> Image.Image:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(BytesIO(data))
            image.verify()
        return Image.open(BytesIO(data))
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError, OSError) as exc:
        raise ImageValidationError("The upload is not a valid supported image.") from exc


def sanitize_image(data: bytes, declared_content_type: str) -> SanitizedImage:
    """Validate by file signature, cap decoded size, and strip active/hidden metadata."""
    declared = str(declared_content_type or "").lower().strip()
    allowed_declared = {details[1] for details in _FORMAT_DETAILS.values()}
    if declared not in allowed_declared:
        raise ImageValidationError("Only PNG, JPEG, and WebP images are allowed.")

    image = _open_image(data)
    source_format = str(image.format or "").upper()
    if source_format not in _FORMAT_DETAILS:
        image.close()
        raise ImageValidationError("Only PNG, JPEG, and WebP images are allowed.")

    extension, detected_content_type = _FORMAT_DETAILS[source_format]
    if declared != detected_content_type:
        image.close()
        raise ImageValidationError("The image contents do not match the declared file type.")

    width, height = image.size
    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
        image.close()
        raise ImageValidationError("The image dimensions are too large.")

    try:
        image.seek(0)
        clean = ImageOps.exif_transpose(image)
        has_alpha = clean.mode in {"RGBA", "LA"} or (clean.mode == "P" and "transparency" in clean.info)
        clean = clean.convert("RGBA" if has_alpha and source_format != "JPEG" else "RGB")
        output = BytesIO()
        if source_format == "JPEG":
            clean.save(output, format="JPEG", quality=92, optimize=True)
        elif source_format == "PNG":
            clean.save(output, format="PNG", optimize=True)
        else:
            clean.save(output, format="WEBP", quality=92, method=4)
    except OSError as exc:
        raise ImageValidationError("The image could not be safely decoded.") from exc
    finally:
        image.close()

    return SanitizedImage(
        data=output.getvalue(),
        extension=extension,
        content_type=detected_content_type,
        width=width,
        height=height,
    )
