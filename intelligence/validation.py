"""Input validation for pickup photos and photo links. No web framework or torch needed.

Every image check raises ValidationError with a machine-readable code, so the API
can answer precisely and the monitor can count each failure type.
"""
import io
from urllib.parse import urlsplit

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BYTES = 8 * 1024 * 1024          # the Engine stores at most 5 MB; 8 MB leaves headroom
MIN_SIDE = 128                       # smaller than this carries too little detail to classify
MAX_PIXELS = 40_000_000              # guards against decompression bombs (a tiny file that expands hugely)
ALLOWED_FORMATS = {'JPEG', 'PNG', 'WEBP'}
TRAINING_MAX_SIDE = 768              # local training images were stored at <= 768 px ...
TRAINING_JPEG_QUALITY = 92           # ... as JPEG quality 92 (Stage 2a download step)
SIGNED_PATH_PREFIX = '/storage/v1/object/sign/'   # Supabase Storage signed links


class ValidationError(Exception):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code, self.detail = code, detail


def photo_url_allowed(url, allowed_hosts):
    """Only fetch https links to our own Supabase Storage signed-object path.
    Anything else (other hosts, internal addresses, credentials in the URL,
    odd ports, other paths) is refused, which blocks server-side request forgery."""
    try:
        p = urlsplit(url)
        port = p.port
    except ValueError:
        return False
    return (p.scheme == 'https' and p.hostname is not None and p.hostname.lower() in allowed_hosts
            and p.username is None and p.password is None and port in (None, 443)
            and p.path.startswith(SIGNED_PATH_PREFIX))


def load_image(data):
    """Validate raw image bytes and return an RGB PIL image prepared exactly the way
    training images were prepared (to avoid training/serving skew)."""
    if not data:
        raise ValidationError('empty_file', 'no image data received')
    if len(data) > MAX_BYTES:
        raise ValidationError('file_too_large', f'image larger than {MAX_BYTES // (1024 * 1024)} MB')
    try:
        img = Image.open(io.BytesIO(data))
        fmt = img.format
    except Image.DecompressionBombError:
        raise ValidationError('too_many_pixels', 'image dimensions are far beyond the limit') from None
    except (UnidentifiedImageError, OSError):
        raise ValidationError('unsupported_type', 'file is not a readable image') from None
    # The format comes from the file's actual bytes, never from a client header.
    if fmt not in ALLOWED_FORMATS:
        raise ValidationError('unsupported_type', f'format {fmt} not allowed; use JPEG, PNG or WEBP')
    w, h = img.size
    if w * h > MAX_PIXELS:
        raise ValidationError('too_many_pixels', f'image has {w * h} pixels; limit is {MAX_PIXELS}')
    if min(w, h) < MIN_SIDE:
        raise ValidationError('image_too_small', f'shortest side is {min(w, h)} px; minimum is {MIN_SIDE}')
    try:
        img.load()
    except Exception:
        raise ValidationError('corrupt_image', 'image data is truncated or corrupt') from None

    img = ImageOps.exif_transpose(img)      # phones store rotation in EXIF; apply it
    if img.mode in ('P', 'LA', 'RGBA'):
        img = img.convert('RGBA').convert('RGB')
    else:
        img = img.convert('RGB')
    return normalise_like_training(img)


def normalise_like_training(img):
    """Training images were downscaled to <= 768 px and re-saved as JPEG q92.
    Doing the same here keeps serving inputs statistically like training inputs.
    Re-encoding also drops all metadata (including any GPS location in EXIF)."""
    s = TRAINING_MAX_SIDE / max(img.size)
    if s < 1:
        img = img.resize((round(img.width * s), round(img.height * s)), Image.BICUBIC)
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=TRAINING_JPEG_QUALITY)
    buf.seek(0)
    return Image.open(buf).convert('RGB')


def dhash(img, size=8):
    """64-bit difference hash: near-identical photos (resized, recompressed) get
    hashes that differ in only a few bits. Used to monitor reused pickup photos."""
    g = img.convert('L').resize((size + 1, size), Image.BILINEAR)
    px = list(g.getdata())
    bits = 0
    for row in range(size):
        for col in range(size):
            left = px[row * (size + 1) + col]
            right = px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (left > right)
    return bits
