"""Photo intake (engine-design.md section 5). Every upload is re-encoded
before it is stored, which drops EXIF, its GPS position and any comment, and
fingerprinted so the same photo can't be used for two pickups.

`prepare` is CPU-bound: call it from a worker thread, not the event loop."""

import hashlib
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
# JPEGs decode at a reduced scale, so they can be larger. Other formats decode
# in full, and on a 512 MB server a big one could exhaust memory.
MAX_PIXELS = {"JPEG": 16_000_000, "MPO": 16_000_000, "PNG": 4_000_000, "WEBP": 4_000_000}
MIN_SIDE = 320
MAX_SIDE = 1600
JPEG_QUALITY = 85
# A difference hash with almost all bits equal comes from a flat or dark photo
# and can't tell two such photos apart.
FLAT_BITS = 8


class NotAPhoto(ValueError):
    pass


@dataclass(frozen=True)
class PreparedPhoto:
    jpeg: bytes
    # Of the stored JPEG, so the classifier can check what it fetched.
    sha256: str
    # Of the upload itself. Unlike `sha256` it doesn't change with the encoder.
    upload_sha256: str
    # Difference hash: near-identical images differ in few of its 64 bits,
    # even after resizing or re-compression. Signed, to fit a Postgres bigint.
    dhash: int
    width: int
    height: int


def prepare(data: bytes) -> PreparedPhoto:
    if len(data) > MAX_UPLOAD_BYTES:
        raise NotAPhoto("the photo is larger than 5 MB")
    try:
        image = _decode(data)
    except NotAPhoto:
        raise
    except Exception as exc:
        # Hostile or corrupt input can fail anywhere inside Pillow.
        raise NotAPhoto("the photo can't be read") from exc
    if min(image.size) < MIN_SIDE:
        raise NotAPhoto(f"the photo must be at least {MIN_SIDE} pixels on each side")

    image.info.clear()
    out = BytesIO()
    image.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True)
    jpeg = out.getvalue()
    return PreparedPhoto(
        jpeg=jpeg,
        sha256=hashlib.sha256(jpeg).hexdigest(),
        upload_sha256=hashlib.sha256(data).hexdigest(),
        dhash=_dhash(image),
        width=image.width,
        height=image.height,
    )


def bits_apart(a: int, b: int) -> int:
    """How many of two hashes' 64 bits differ."""
    return ((a ^ b) & (2**64 - 1)).bit_count()


def has_signal(dhash: int) -> bool:
    """Whether the hash can tell this photo from others at all."""
    ones = (dhash & (2**64 - 1)).bit_count()
    return FLAT_BITS <= ones <= 64 - FLAT_BITS


def _decode(data: bytes) -> Image.Image:
    # Only these parsers ever see the upload.
    with Image.open(BytesIO(data), formats=("JPEG", "PNG", "WEBP")) as probe:
        # Checked from the header, before any pixels are decoded.
        limit = MAX_PIXELS.get(probe.format or "")
        if limit is None:
            raise NotAPhoto("send a JPEG, PNG or WebP photo")
        if probe.width * probe.height > limit:
            raise NotAPhoto("the photo has too many pixels")
        probe.verify()
    with Image.open(BytesIO(data), formats=("JPEG", "PNG", "WEBP")) as original:
        # A JPEG decodes straight to about the size we keep, not in full.
        original.draft("RGB", (MAX_SIDE, MAX_SIDE))
        original.thumbnail((MAX_SIDE, MAX_SIDE))
        return ImageOps.exif_transpose(original).convert("RGB")


def _dhash(image: Image.Image) -> int:
    small = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS).tobytes()
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (small[row * 9 + col] > small[row * 9 + col + 1])
    return bits - 2**64 if bits >= 2**63 else bits
