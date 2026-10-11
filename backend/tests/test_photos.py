import hashlib
import random
from io import BytesIO

import pytest
from PIL import ExifTags, Image, ImageDraw

from app.domain.photos import MAX_SIDE, NotAPhoto, bits_apart, has_signal, prepare


def scene(size=(1200, 900), shapes=((100, 100, 500, 400), (700, 300, 1100, 800))) -> Image.Image:
    image = Image.new("RGB", size, (40, 120, 60))
    draw = ImageDraw.Draw(image)
    for i, box in enumerate(shapes):
        draw.ellipse(box, fill=(230, 230 - 80 * i, 30 + 90 * i))
    return image


def encode(image: Image.Image, fmt="JPEG", **kwargs) -> bytes:
    out = BytesIO()
    image.save(out, fmt, **kwargs)
    return out.getvalue()


def with_gps_and_orientation(image: Image.Image, orientation: int = 1) -> bytes:
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = orientation
    exif[ExifTags.Base.Make] = "Tecno"
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[ExifTags.GPS.GPSLatitudeRef] = "N"
    gps[ExifTags.GPS.GPSLatitude] = (5.0, 41.0, 2.0)
    gps[ExifTags.GPS.GPSLongitudeRef] = "W"
    gps[ExifTags.GPS.GPSLongitude] = (0.0, 10.0, 5.0)
    return encode(image, exif=exif)


def jpeg_markers(jpeg: bytes) -> set[int]:
    """The segment markers before the image data starts."""
    markers, i = set(), 2
    while jpeg[i] == 0xFF and jpeg[i + 1] != 0xDA:
        markers.add(jpeg[i + 1])
        i += 2 + int.from_bytes(jpeg[i + 2 : i + 4], "big")
    return markers


def test_exif_gps_and_comments_are_dropped():
    upload = with_gps_and_orientation(scene())
    commented = encode(scene(), comment=b"lat 5.6837 lon -0.1681 rider 0244")
    assert Image.open(BytesIO(upload)).getexif().get_ifd(ExifTags.IFD.GPSInfo)

    for photo in (prepare(upload), prepare(commented)):
        stored = Image.open(BytesIO(photo.jpeg))
        assert stored.format == "JPEG"
        assert len(stored.getexif()) == 0
        # APP0 (JFIF), quantisation tables, frame header and Huffman tables only.
        assert jpeg_markers(photo.jpeg) <= {0xE0, 0xDB, 0xC0, 0xC4}


def test_orientation_is_applied_before_the_exif_goes():
    upload = with_gps_and_orientation(scene((800, 400), ((50, 50, 300, 300),)), orientation=6)

    photo = prepare(upload)

    assert (photo.width, photo.height) == (400, 800)


def test_large_photos_are_scaled_down():
    photo = prepare(encode(scene((2400, 1800))))

    assert max(photo.width, photo.height) == MAX_SIDE


def test_the_same_upload_gives_the_same_fingerprints():
    upload = encode(scene())

    first, again = prepare(upload), prepare(upload)

    assert first.sha256 == again.sha256 == hashlib.sha256(first.jpeg).hexdigest()
    assert first.upload_sha256 == hashlib.sha256(upload).hexdigest()


def test_a_flat_photo_gives_no_duplicate_signal():
    assert has_signal(prepare(encode(scene())).dhash)
    assert not has_signal(prepare(encode(Image.new("RGB", (800, 600), (20, 20, 20)))).dhash)


def test_a_resaved_copy_is_a_near_duplicate_and_another_scene_is_not():
    original = scene()
    smaller = original.resize((840, 630))

    first = prepare(encode(original, quality=95))
    copy = prepare(encode(smaller, quality=60))
    other = prepare(encode(scene(shapes=((600, 50, 1150, 450), (50, 450, 450, 850)))))

    assert first.sha256 != copy.sha256
    assert bits_apart(first.dhash, copy.dhash) <= 4
    assert bits_apart(first.dhash, other.dhash) > 12


def test_fingerprints_fit_a_signed_64_bit_column():
    photo = prepare(encode(scene()))

    assert -(2**63) <= photo.dhash < 2**63


@pytest.mark.parametrize(
    "upload",
    [
        b"not an image at all",
        encode(scene())[:2000],
        encode(scene(), "GIF"),
        encode(scene((200, 150))),
        encode(Image.new("1", (2500, 2000)), "PNG"),
        encode(Image.new("RGB", (5000, 4000)), quality=10),
        encode(scene((320, 3000))),
        b"\xff\xd8" + b"\x00" * (5 * 1024 * 1024),
    ],
    ids=[
        "garbage",
        "truncated",
        "gif",
        "too-small",
        "png-too-many-pixels",
        "jpeg-too-many-pixels",
        "too-narrow",
        "too-large",
    ],
)
def test_unusable_uploads_are_refused(upload):
    with pytest.raises(NotAPhoto):
        prepare(upload)


def test_png_and_webp_are_accepted():
    for fmt in ("PNG", "WEBP"):
        assert Image.open(BytesIO(prepare(encode(scene(), fmt)).jpeg)).format == "JPEG"


def test_phone_jpegs_with_a_second_image_are_accepted():
    out = BytesIO()
    scene().save(out, "MPO", save_all=True, append_images=[scene((600, 450))])
    assert Image.open(BytesIO(out.getvalue())).format == "MPO"

    assert Image.open(BytesIO(prepare(out.getvalue()).jpeg)).format == "JPEG"


@pytest.mark.filterwarnings("ignore:.*:UserWarning")
def test_corrupted_photos_are_refused_cleanly():
    upload = bytearray(with_gps_and_orientation(scene((400, 400), ((50, 50, 300, 300),)), 6))
    rng = random.Random(591)
    for _ in range(300):
        mutated = bytearray(upload)
        for _ in range(rng.randint(1, 8)):
            mutated[rng.randrange(2, len(mutated))] = rng.randrange(256)
        try:
            prepare(bytes(mutated))
        except NotAPhoto:
            pass
