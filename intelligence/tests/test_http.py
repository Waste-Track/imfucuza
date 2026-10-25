"""HTTP-layer tests (main.py) with the model and photo fetcher replaced by fakes.
Skipped where FastAPI isn't installed; CI installs it from requirements.txt."""
import hashlib
import io
import json
import uuid

import pytest
from PIL import Image

pytest.importorskip('fastapi')
from fastapi.testclient import TestClient

import main
import photo_fetch
import verifier
from tests.test_service import FakeVerifier

BACKEND, REVIEWER = 'b' * 32, 'r' * 32
URL = 'https://abcd.supabase.co/storage/v1/object/sign/pickup-photos/p.jpg?token=x'


def jpeg():
    buf = io.BytesIO()
    Image.new('RGB', (640, 480), (120, 160, 90)).save(buf, 'JPEG')
    return buf.getvalue()


@pytest.fixture
def client(monkeypatch, tmp_path):
    data = jpeg()
    monkeypatch.setenv('SERVICE_TOKENS', f'engine:backend:{BACKEND};rv1:reviewer:{REVIEWER}')
    monkeypatch.setenv('PHOTO_URL_ALLOWED_HOSTS', 'abcd.supabase.co')
    monkeypatch.setenv('LOG_DIR', str(tmp_path))
    monkeypatch.setattr(verifier, 'fetch_model_files', lambda: ('w', 'c'))
    monkeypatch.setattr(verifier, 'Verifier', lambda w, c, sha=None: FakeVerifier())
    monkeypatch.setattr(photo_fetch, 'make_fetcher', lambda: (lambda url: data))
    with TestClient(main.app) as c:
        c.photo = data
        yield c


def request_body(data):
    return {'verification_id': str(uuid.uuid4()), 'request_id': str(uuid.uuid4()), 'declared_type': 'pet',
            'photo': {'url': URL, 'sha256': hashlib.sha256(data).hexdigest()},
            'captured_at': '2026-10-11T09:30:00+00:00', 'capture_source': 'rider'}


def test_health(client):
    assert client.get('/v1/health').status_code == 200
    assert client.get('/health').status_code == 200


def test_engine_call(client):
    b = request_body(client.photo)
    r = client.post('/v1/verify', json=b, headers={'Authorization': f'Bearer {BACKEND}',
                                                    'Idempotency-Key': b['verification_id']})
    assert r.status_code == 200 and r.json()['verification_id'] == b['verification_id']
    assert r.headers['cache-control'] == 'no-store'


def test_wrong_content_is_400_not_422(client):
    r = client.post('/v1/verify', content=b'garbage', headers={'Authorization': f'Bearer {BACKEND}'})
    assert r.status_code == 400
    r = client.post('/v1/verify-upload', headers={'Authorization': f'Bearer {REVIEWER}'})   # no file at all
    assert r.status_code == 400


def test_upload_and_monitoring(client):
    h = {'Authorization': f'Bearer {REVIEWER}'}
    r = client.post('/v1/verify-upload', headers=h, files={'image': ('p.jpg', client.photo, 'image/jpeg')},
                    data={'declared_type': 'pet'})
    assert r.status_code == 200 and 'material' in r.json()
    m = client.get('/metrics', headers=h)
    assert m.status_code == 200 and m.json()['predictions'] == 1
    assert client.get('/v1/logs?limit=5', headers=h).status_code == 200
    assert client.get('/metrics', headers={'Authorization': f'Bearer {BACKEND}'}).status_code == 403
    assert json.loads(client.get('/v1/logs', headers=h).text)['events']
