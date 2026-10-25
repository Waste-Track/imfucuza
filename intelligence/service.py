"""The classifier's request handling, independent of the web framework, so every
rule in spec/classifier-contract.md can be tested without a server or a model.

Status codes follow the contract exactly:
    200  decision
    422  ONLY when the photo itself can't be used -> the Engine asks for a retake
    400  malformed request or a photo link outside our storage -> Engine treats as unavailable
    401  wrong or missing token; 403 token lacks the permission
    429  rate limited (the Engine retries once)
    503  the photo couldn't be fetched
    500  unexpected failure
"""
import hashlib
import json
import threading
import time
import traceback
import uuid
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as SchemaError

from security import AuthError, authenticate
from validation import ValidationError, dhash, load_image, photo_url_allowed

CONTRACT = {
    'accept': ('accept', 'ok'),
    'reject_contaminated': ('reject', 'contaminated'),
    'reject_wrong_type': ('reject', 'wrong_type'),
}


class PhotoFetchError(Exception):
    """The photo link could not be fetched (timeout, connection error, non-200)."""


class PhotoTooLarge(Exception):
    """The fetched photo exceeded the size limit."""


class PhotoRef(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(min_length=1, max_length=4096)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class VerifyBody(BaseModel):
    # extra='forbid' enforces the boundary: a request carrying any other field
    # (a name, a phone number, a location...) is refused, not silently accepted.
    model_config = ConfigDict(extra='forbid')
    verification_id: UUID
    request_id: UUID
    declared_type: Literal['pet', 'hdpe', 'pp', 'mixed_plastic']
    photo: PhotoRef
    captured_at: datetime
    capture_source: Literal['rider', 'household']


class IdempotencyCache:
    """Remembers recent answers by verification_id so an Engine retry gets the same
    answer without running the model again (the free server's CPU is slow)."""

    def __init__(self, ttl_s=600, max_items=2000):
        self.ttl_s, self.max_items = ttl_s, max_items
        self.items = {}
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            hit = self.items.get(key)
            if hit and time.time() - hit[0] < self.ttl_s:
                return hit[1], hit[2]
            return None

    def put(self, key, status, body):
        with self.lock:
            if len(self.items) >= self.max_items:
                del self.items[min(self.items, key=lambda k: self.items[k][0])]
            self.items[key] = (time.time(), status, body)


class ClassifierService:
    def __init__(self, verifier, monitor, tokens, limiter, fetch_photo, allowed_hosts):
        self.verifier = verifier
        self.monitor = monitor
        self.tokens = tokens
        self.limiter = limiter
        self.fetch_photo = fetch_photo
        self.allowed_hosts = {h.strip().lower() for h in allowed_hosts if h.strip()}
        self.cache = IdempotencyCache()

    # ---------------------------------------------------------------- helpers
    def _guard(self, authorization, permission):
        try:
            caller = authenticate(self.tokens, authorization, permission)
        except AuthError as e:
            self.monitor.record({'outcome': 'auth_failed', 'error': e.code, 'endpoint': permission})
            return None, (e.status, {'error': e.code})
        wait = self.limiter.check(caller)
        if wait:
            self.monitor.record({'outcome': 'rate_limited', 'caller': caller, 'endpoint': permission})
            return None, (429, {'error': 'rate_limited', 'retry_after_s': wait})
        return caller, None

    def _classify(self, data, verification_id, caller, extra_log, t0):
        """Shared by the contract endpoint and the test-upload endpoint.
        Returns (status, body, detail) where detail holds the head probabilities."""
        try:
            img = load_image(data)
        except ValidationError as e:
            self.monitor.record({'outcome': 'unusable_photo', 'error': e.code, 'caller': caller, **extra_log})
            return 422, {'error': 'photo_unusable', 'reason': e.code, 'detail': e.detail}, None
        sha = hashlib.sha256(data).hexdigest()
        dup = self.monitor.check_duplicate(sha, dhash(img), verification_id)
        m0 = time.perf_counter()
        out = self.verifier.predict(img)
        model_ms = round((time.perf_counter() - m0) * 1000, 1)
        latency = round((time.perf_counter() - t0) * 1000, 1)
        suggested = out['suggested_decision']
        if suggested == 'retake_photo':
            self.monitor.record({'outcome': 'unusable_photo', 'error': 'nothing_recognisable', 'caller': caller,
                                 'confidence': out['confidence'], 'latency_ms': latency, **extra_log})
            return 422, {'error': 'photo_unusable', 'reason': 'nothing_recognisable',
                         'detail': 'no recognisable waste in frame; please retake'}, out
        decision, reason = CONTRACT[suggested]
        body = {'verification_id': str(verification_id), 'decision': decision, 'confidence': out['confidence'],
                'reason': reason, 'model_version': self.verifier.version}
        self.monitor.record({'outcome': 'predicted', 'caller': caller, 'decision': decision, 'reason': reason,
                             'confidence': out['confidence'], 'image_sha256': sha, 'duplicate_of': dup,
                             'latency_ms': latency, 'model_ms': model_ms, 'model_version': self.verifier.version,
                             **extra_log})
        return 200, body, dict(out, duplicate_of=dup)

    # -------------------------------------------------------------- endpoints
    def health(self):
        return 200, {'status': 'ok', 'model_version': self.verifier.version,
                     'weights_sha256': self.verifier.weights_sha256}

    def verify_json(self, authorization, idempotency_key, raw_body):
        """POST /v1/verify, exactly as in spec/classifier-contract.md."""
        t0 = time.perf_counter()
        caller, err = self._guard(authorization, 'verify')
        if err:
            return err
        try:
            body = VerifyBody.model_validate(json.loads(raw_body or b'null'))
        except json.JSONDecodeError:
            self.monitor.record({'outcome': 'bad_request', 'error': 'invalid_json', 'caller': caller})
            return 400, {'error': 'invalid_json'}
        except SchemaError as e:
            fields = sorted({'.'.join(str(p) for p in err['loc']) for err in e.errors()})
            self.monitor.record({'outcome': 'bad_request', 'error': 'invalid_request', 'fields': fields,
                                 'caller': caller})
            return 400, {'error': 'invalid_request', 'fields': fields}
        vid = str(body.verification_id)
        if idempotency_key and idempotency_key != vid:
            self.monitor.record({'outcome': 'bad_request', 'error': 'idempotency_key_mismatch', 'caller': caller})
            return 400, {'error': 'idempotency_key_mismatch'}
        cached = self.cache.get(vid)
        if cached:
            self.monitor.record({'outcome': 'idempotent_replay', 'caller': caller, 'verification_id': vid})
            return cached
        log = {'verification_id': vid, 'request_id': str(body.request_id), 'declared_type': body.declared_type,
               'capture_source': body.capture_source, 'captured_at': body.captured_at.isoformat()}
        if not photo_url_allowed(body.photo.url, self.allowed_hosts):
            self.monitor.record({'outcome': 'url_blocked', 'caller': caller, **log})
            return 400, {'error': 'photo_url_not_allowed'}
        try:
            data = self.fetch_photo(body.photo.url)
        except PhotoTooLarge:
            self.monitor.record({'outcome': 'unusable_photo', 'error': 'file_too_large', 'caller': caller, **log})
            return 422, {'error': 'photo_unusable', 'reason': 'file_too_large'}
        except PhotoFetchError as e:
            self.monitor.record({'outcome': 'photo_unavailable', 'error': str(e)[:80], 'caller': caller, **log})
            return 503, {'error': 'photo_unavailable'}
        if hashlib.sha256(data).hexdigest() != body.photo.sha256:
            self.monitor.record({'outcome': 'unusable_photo', 'error': 'photo_hash_mismatch', 'caller': caller, **log})
            return 422, {'error': 'photo_unusable', 'reason': 'photo_hash_mismatch'}
        try:
            status, resp, _ = self._classify(data, vid, caller, log, t0)
        except Exception:
            return self._internal(caller, log)
        if status in (200, 422):
            self.cache.put(vid, status, resp)
        return status, resp

    def verify_upload(self, authorization, data, declared_type=None):
        """POST /v1/verify-upload: test and demo path for reviewers and admins only.
        Same model and checks, but the photo is uploaded directly instead of linked."""
        t0 = time.perf_counter()
        caller, err = self._guard(authorization, 'upload')
        if err:
            return err
        vid = str(uuid.uuid4())
        log = {'verification_id': vid, 'declared_type': declared_type, 'capture_source': 'test_upload'}
        try:
            status, resp, detail = self._classify(data, vid, caller, log, t0)
        except Exception:
            return self._internal(caller, log)
        if detail:
            resp = dict(resp, material=detail['material'], cleanliness=detail['cleanliness'],
                        duplicate_of=detail['duplicate_of'])
        return status, resp

    def metrics(self, authorization, window_hours=24.0):
        _, err = self._guard(authorization, 'metrics')
        if err:
            return err
        return 200, dict(self.monitor.metrics(window_hours), model_version=self.verifier.version)

    def logs(self, authorization, limit=100):
        _, err = self._guard(authorization, 'logs')
        if err:
            return err
        return 200, {'events': self.monitor.recent(limit)}

    def _internal(self, caller, log):
        ref = uuid.uuid4().hex[:12]
        print(f'[internal_error {ref}]', traceback.format_exc(), flush=True)
        self.monitor.record({'outcome': 'internal_error', 'caller': caller, 'ref': ref, **log})
        return 500, {'error': 'internal', 'ref': ref}
