"""Unit tests for the parts that need neither a model nor a web server."""
import io

import numpy as np
import pytest
from PIL import Image

from decision import DECISIONS, UNCERTAIN, apply_temperature, decide, final_decision
from monitoring import Monitor, psi
from preprocess import center_crop, resize_shorter_side, to_input
from security import AuthError, RateLimiter, authenticate, parse_tokens
from validation import ValidationError, dhash, load_image, photo_url_allowed


def image_bytes(w=640, h=480, color=(120, 160, 90), fmt='JPEG', **kw):
    buf = io.BytesIO()
    Image.new('RGB', (w, h), color).save(buf, fmt, **kw)
    return buf.getvalue()


def bad_image(code, data):
    with pytest.raises(ValidationError) as e:
        load_image(data)
    assert e.value.code == code


def test_decision_mapping():
    cases = {(0, 0): 'accept', (0, 1): 'reject_contaminated', (1, 0): 'reject_wrong_type', (2, 1): 'retake_photo'}
    for (m, c), want in cases.items():
        pm = np.full((1, 3), 0.05)
        pm[0, m] = 0.9
        pc = np.full((1, 2), 0.05)
        pc[0, c] = 0.95
        d, _ = decide(pm, pc)
        assert DECISIONS[d[0]] == want
    pm = np.array([[0.9, 0.05, 0.05]])
    pc = np.array([[0.8, 0.2]])
    assert abs(decide(pm, pc)[1][0] - 0.72) < 1e-9      # plastic: material conf x cleanliness conf
    assert final_decision(pm, pc, 0.9)[0] == UNCERTAIN
    assert final_decision(pm, pc, 0.7)[0] == 'accept'


def test_temperature():
    p = np.array([[0.6, 0.3, 0.1]])
    assert np.allclose(apply_temperature(p, 1.0), p)
    assert apply_temperature(p, 0.5)[0, 0] > 0.6 > apply_temperature(p, 2.0)[0, 0]


def test_image_validation():
    img = load_image(image_bytes())
    assert img.mode == 'RGB' and max(img.size) == 640
    assert max(load_image(image_bytes(3000, 2000)).size) == 768       # downscaled like training
    assert load_image(image_bytes(fmt='PNG')).mode == 'RGB'
    bad_image('empty_file', b'')
    bad_image('unsupported_type', b'not an image at all' * 10)
    bad_image('unsupported_type', image_bytes(fmt='GIF'))
    bad_image('image_too_small', image_bytes(100, 80))
    bad_image('file_too_large', b'\xff' * (8 * 1024 * 1024 + 1))
    bad_image('corrupt_image', image_bytes(800, 600, quality=95)[:3000])
    big = io.BytesIO()
    Image.new('L', (8000, 6000)).save(big, 'PNG')                       # small file, 48M pixels
    bad_image('too_many_pixels', big.getvalue())
    bomb = io.BytesIO()
    Image.new('1', (20000, 20000)).save(bomb, 'PNG')                     # ~50 KB file, 400M pixels
    bad_image('too_many_pixels', bomb.getvalue())


def test_exif_rotation_applied_and_metadata_stripped():
    im = Image.new('RGB', (400, 200), (10, 200, 10))
    exif = im.getexif()
    exif[0x0112] = 6                                                     # "rotate 90" orientation tag
    buf = io.BytesIO()
    im.save(buf, 'JPEG', exif=exif)
    out = load_image(buf.getvalue())
    assert out.size == (200, 400)
    assert not out.getexif()


def test_photo_url_allow_list():
    hosts = {'abcd.supabase.co'}
    ok = 'https://abcd.supabase.co/storage/v1/object/sign/pickup-photos/p.jpg?token=x'
    assert photo_url_allowed(ok, hosts)
    assert photo_url_allowed(ok.replace('abcd', 'ABCD'), hosts)
    for bad in [
        'http://abcd.supabase.co/storage/v1/object/sign/p.jpg',            # not https
        'https://evil.example/storage/v1/object/sign/p.jpg',               # other host
        'https://abcd.supabase.co.evil.example/storage/v1/object/sign/p',  # look-alike host
        'https://user:pw@abcd.supabase.co/storage/v1/object/sign/p.jpg',  # credentials
        'https://abcd.supabase.co:8443/storage/v1/object/sign/p.jpg',      # odd port
        'https://abcd.supabase.co/rest/v1/riders',                         # other path
        'https://169.254.169.254/latest/meta-data/',                       # cloud metadata (SSRF)
        'file:///etc/passwd',
        'https://abcd.supabase.co:99999/storage/v1/object/sign/p.jpg',     # invalid port
    ]:
        assert not photo_url_allowed(bad, hosts), bad


def test_dhash_near_duplicates():
    rng = np.random.default_rng(0)
    base = Image.fromarray(rng.integers(0, 255, (300, 400, 3), dtype=np.uint8))
    other = Image.fromarray(rng.integers(0, 255, (300, 400, 3), dtype=np.uint8))
    assert (dhash(base) ^ dhash(base.resize((200, 150)))).bit_count() <= 5
    assert (dhash(base) ^ dhash(other)).bit_count() > 15


def test_auth_and_rate_limit():
    toks = parse_tokens('be:backend:' + 'b' * 32 + '; rv:reviewer:' + 'r' * 32)
    assert authenticate(toks, 'Bearer ' + 'b' * 32, 'verify') == 'be'
    assert authenticate(toks, 'Bearer ' + 'r' * 32, 'upload') == 'rv'
    for header, perm, status in [('Bearer ' + 'b' * 32, 'upload', 403), ('Bearer ' + 'b' * 32, 'metrics', 403),
                                 ('Bearer wrong', 'verify', 401), (None, 'verify', 401)]:
        with pytest.raises(AuthError) as e:
            authenticate(toks, header, perm)
        assert e.value.status == status
    for bad in ['x:backend:short', 'x:superuser:' + 'z' * 32]:
        with pytest.raises(ValueError):
            parse_tokens(bad)
    rl = RateLimiter(3)
    assert [rl.check('a', 1000 + i) for i in range(4)] == [0, 0, 0, 58]
    assert rl.check('a', 1061) == 0 and rl.check('b', 1000) == 0


def test_monitor():
    m = Monitor(baseline_hist=[0.1] * 10)
    for i in range(40):
        m.record({'outcome': 'predicted', 'decision': 'accept', 'reason': 'ok', 'confidence': 0.95 if i % 2 else 0.85,
                  'declared_type': 'pet' if i < 30 else 'hdpe', 'duplicate_of': 'x' if i < 4 else None,
                  'latency_ms': 100 + i, 'model_ms': 50})
    for _ in range(10):
        m.record({'outcome': 'unusable_photo', 'error': 'image_too_small'})
    m.record({'outcome': 'url_blocked'})
    r = m.metrics()
    assert r['predictions'] == 40 and r['unusable_share'] == 0.2 and r['duplicate_rate'] == 0.1
    assert r['deferred_share_under_engine_policy'] == 0.5                 # 0.85 < engine accept threshold 0.90
    assert r['confidence_by_declared_type']['pet']['n'] == 30
    assert r['confidence_psi_vs_offline'] > 0.2
    assert any('drift ALERT' in a for a in r['alerts']) and any('security' in a for a in r['alerts'])
    assert psi([10] * 10, [0.1] * 10) < 1e-9
    assert m.check_duplicate('s1', 0b1011, 'A') is None
    assert m.check_duplicate('s1', 0b1011, 'A') is None                 # a retry of the same verification
    assert m.check_duplicate('s1', 0b1011, 'B') == 'A'                  # same photo, another verification
    assert m.check_duplicate('s2', 0b1010, 'C') == 'A'                  # near-identical photo


def test_preprocess_matches_torchvision_rules():
    assert resize_shorter_side(Image.new('RGB', (768, 431)), 256).size == (456, 256)
    assert resize_shorter_side(Image.new('RGB', (431, 768)), 256).size == (256, 456)
    assert resize_shorter_side(Image.new('RGB', (500, 500)), 256).size == (256, 256)
    assert center_crop(Image.new('RGB', (456, 256)), 224).size == (224, 224)
    x = to_input(Image.new('RGB', (640, 480), (255, 0, 128)), 224, [0.5] * 3, [0.5] * 3)
    assert x.shape == (1, 3, 224, 224) and x.dtype == np.float32 and x.flags['C_CONTIGUOUS']
    assert np.allclose(x[0, :, 0, 0], [1.0, -1.0, (128 / 255 - 0.5) / 0.5], atol=1e-6)
