"""Contract tests for service.py against spec/classifier-contract.md, with a fake model
and a fake photo store. No web server, no model file, no network."""
import hashlib
import io
import json
import uuid
from uuid import UUID

import pytest
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from monitoring import Monitor
from security import RateLimiter, parse_tokens
from service import ClassifierService, PhotoFetchError, PhotoTooLarge

BACKEND, REVIEWER = 'b' * 32, 'r' * 32
HOST = 'abcd.supabase.co'
URL = f'https://{HOST}/storage/v1/object/sign/pickup-photos/p1.jpg?token=SECRET-SIGNING-TOKEN'


class EngineReply(BaseModel):
    """Copy of the Engine's strict parser (backend/app/adapters/classifier.py, _Reply)."""
    model_config = ConfigDict(extra='ignore', strict=True)
    verification_id: UUID
    decision: str = Field(pattern='^(accept|reject|uncertain)$')
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    reason: str = Field(pattern='^(ok|contaminated|wrong_type|duplicate)$')
    model_version: str = Field(min_length=1, max_length=100)


class FakeVerifier:
    version = 'verifier-v1+abc123def456'
    weights_sha256 = 'f' * 64
    cfg = {}

    def __init__(self, suggested='accept', confidence=0.93, fail=False):
        self.suggested, self.confidence, self.fail, self.calls = suggested, confidence, fail, 0

    def predict(self, img):
        self.calls += 1
        if self.fail:
            raise RuntimeError('model crashed')
        return {'decision': self.suggested, 'suggested_decision': self.suggested, 'confidence': self.confidence,
                'status': 'auto', 'material': {'plastic': 0.9, 'non_plastic': 0.05, 'discard': 0.05},
                'cleanliness': {'clean': 0.9, 'contaminated': 0.1}}


def photo(color=(120, 160, 90), w=640, h=480):
    buf = io.BytesIO()
    Image.new('RGB', (w, h), color).save(buf, 'JPEG')
    return buf.getvalue()


def make(verifier=None, store=None, fetch=None, per_minute=1000):
    store = {URL: photo()} if store is None else store

    def default_fetch(url):
        if url not in store:
            raise PhotoFetchError('HTTP 404')
        return store[url]

    tokens = parse_tokens(f'engine:backend:{BACKEND};rv1:reviewer:{REVIEWER}')
    svc = ClassifierService(verifier or FakeVerifier(), Monitor(), tokens, RateLimiter(per_minute),
                            fetch or default_fetch, [HOST])
    return svc, store


def body(data=None, **overrides):
    data = photo() if data is None else data
    b = {'verification_id': str(uuid.uuid4()), 'request_id': str(uuid.uuid4()), 'declared_type': 'pet',
         'photo': {'url': URL, 'sha256': hashlib.sha256(data).hexdigest()},
         'captured_at': '2026-10-11T09:30:00+00:00', 'capture_source': 'rider'}
    b.update(overrides)
    return b


def call(svc, b, token=BACKEND, key='same'):
    if key == 'same':
        key = b.get('verification_id') if isinstance(b, dict) else None
    raw = json.dumps(b).encode() if isinstance(b, dict) else b
    return svc.verify_json(f'Bearer {token}' if token else None, key, raw)


@pytest.mark.parametrize('suggested,decision,reason', [
    ('accept', 'accept', 'ok'),
    ('reject_contaminated', 'reject', 'contaminated'),
    ('reject_wrong_type', 'reject', 'wrong_type'),
])
def test_decisions_follow_the_contract_and_pass_the_engines_parser(suggested, decision, reason):
    svc, _ = make(FakeVerifier(suggested, 0.87))
    b = body()
    status, resp = call(svc, b)
    assert status == 200
    reply = EngineReply.model_validate_json(json.dumps(resp))
    assert str(reply.verification_id) == b['verification_id']
    assert (reply.decision, reply.reason, reply.confidence) == (decision, reason, 0.87)


def test_nothing_recognisable_is_a_422_so_the_engine_asks_for_a_retake():
    svc, _ = make(FakeVerifier('retake_photo', 0.7))
    status, resp = call(svc, body())
    assert status == 422 and resp['reason'] == 'nothing_recognisable'


@pytest.mark.parametrize('data,reason', [(b'not an image' * 50, 'unsupported_type'), (None, 'image_too_small')])
def test_unusable_photos_are_422(data, reason):
    data = photo(w=100, h=80) if data is None else data
    svc, store = make()
    store[URL] = data
    status, resp = call(svc, body(data))
    assert status == 422 and resp['reason'] == reason


def test_photo_hash_mismatch_is_422():
    svc, _ = make()
    b = body()
    b['photo']['sha256'] = '0' * 64
    assert call(svc, b)[0] == 422


def test_photo_that_cannot_be_fetched_is_503():
    svc, _ = make(store={})
    assert call(svc, body()) == (503, {'error': 'photo_unavailable'})


def test_oversized_download_is_422():
    def fetch(url):
        raise PhotoTooLarge()
    svc, _ = make(fetch=fetch)
    assert call(svc, body())[0] == 422


@pytest.mark.parametrize('url', [
    'https://evil.example/storage/v1/object/sign/x.jpg',
    'https://169.254.169.254/latest/meta-data/',
    f'http://{HOST}/storage/v1/object/sign/x.jpg',
    f'https://{HOST}/rest/v1/households',
])
def test_links_outside_our_storage_are_refused_without_fetching(url):
    fetched = []

    def fetch(u):
        fetched.append(u)
        return photo()
    svc, _ = make(fetch=fetch)
    b = body()
    b['photo']['url'] = url
    assert call(svc, b) == (400, {'error': 'photo_url_not_allowed'})
    assert fetched == []
    assert svc.monitor.metrics()['outcomes']['url_blocked'] == 1


@pytest.mark.parametrize('mutate', [
    lambda b: b.pop('verification_id'),
    lambda b: b.update(verification_id='not-a-uuid'),
    lambda b: b.update(declared_type='glass'),
    lambda b: b.update(capture_source='stranger'),
    lambda b: b['photo'].update(sha256='XYZ'),
    lambda b: b.update(household_phone='+233200000000'),       # personal data must never cross the boundary
    lambda b: b.update(captured_at='yesterday'),
], ids=['missing-id', 'bad-uuid', 'bad-type', 'bad-source', 'bad-sha', 'extra-personal-field', 'bad-date'])
def test_malformed_requests_are_400_never_422(mutate):
    svc, _ = make()
    b = body()
    mutate(b)
    status, resp = call(svc, b, key=None)
    assert status == 400 and resp['error'] == 'invalid_request'


def test_invalid_json_is_400():
    svc, _ = make()
    assert call(svc, b'{not json', key=None) == (400, {'error': 'invalid_json'})


def test_idempotency_key_must_match_the_verification_id():
    svc, _ = make()
    assert call(svc, body(), key=str(uuid.uuid4())) == (400, {'error': 'idempotency_key_mismatch'})


def test_a_retry_gets_the_same_answer_without_running_the_model_again():
    v = FakeVerifier()
    svc, _ = make(v)
    b = body()
    first = call(svc, b)
    second = call(svc, b)
    assert first == second and v.calls == 1
    assert svc.monitor.metrics()['outcomes']['idempotent_replay'] == 1


def test_model_crash_is_500_and_not_cached():
    v = FakeVerifier(fail=True)
    svc, _ = make(v)
    b = body()
    assert call(svc, b)[0] == 500
    assert call(svc, b)[0] == 500 and v.calls == 2


def test_authentication_and_rate_limit():
    svc, _ = make(per_minute=2)
    assert call(svc, body(), token=None)[0] == 401
    assert call(svc, body(), token='x' * 32)[0] == 401
    assert call(svc, body())[0] == 200
    assert call(svc, body())[0] == 200
    status, resp = call(svc, body())
    assert status == 429 and resp['retry_after_s'] > 0


def test_upload_path_is_for_reviewers_only_and_returns_details():
    svc, _ = make()
    assert svc.verify_upload(f'Bearer {BACKEND}', photo())[0] == 403
    status, resp = svc.verify_upload(f'Bearer {REVIEWER}', photo(), 'pet')
    assert status == 200 and resp['decision'] == 'accept' and 'material' in resp


def test_reused_photo_shows_in_monitoring_but_is_not_rejected_here():
    svc, _ = make()
    assert call(svc, body())[1]['reason'] == 'ok'
    status, resp = call(svc, body())                 # same photo, new verification
    assert status == 200 and resp['reason'] == 'ok'  # the Engine already rejects reused photos
    assert svc.monitor.metrics()['duplicate_rate'] == 0.5


def test_metrics_and_logs_need_a_reviewer_and_never_contain_links_or_images():
    svc, _ = make()
    call(svc, body())
    assert svc.metrics(f'Bearer {BACKEND}')[0] == 403
    assert svc.metrics(f'Bearer {REVIEWER}')[0] == 200
    status, logs = svc.logs(f'Bearer {REVIEWER}', 10)
    dumped = json.dumps(logs)
    assert status == 200 and 'SECRET-SIGNING-TOKEN' not in dumped and URL not in dumped


def test_health():
    svc, _ = make()
    status, resp = svc.health()
    assert status == 200 and resp['model_version'].startswith('verifier-v1')
