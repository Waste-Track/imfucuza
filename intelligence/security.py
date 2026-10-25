"""Bearer-token authentication, roles and per-token rate limiting.

Tokens are never stored in code or files. They come from the SERVICE_TOKENS
environment variable (a Hugging Face Space secret), in the form
    name:role:token;name:role:token
Roles:
    backend  -> may call /v1/verify (the Engine)
    reviewer -> /v1/verify, /v1/verify-upload, /metrics and /v1/logs (paired-team test accounts)
    admin    -> everything
"""
import hashlib
import hmac
import threading
import time
from collections import defaultdict, deque

PERMISSIONS = {
    'backend': {'verify'},
    'reviewer': {'verify', 'upload', 'metrics', 'logs'},
    'admin': {'verify', 'upload', 'metrics', 'logs'},
}


class AuthError(Exception):
    def __init__(self, status, code):
        super().__init__(code)
        self.status, self.code = status, code


def parse_tokens(spec):
    tokens = []
    for part in (spec or '').split(';'):
        part = part.strip()
        if not part:
            continue
        name, role, token = part.split(':', 2)
        if role not in PERMISSIONS:
            raise ValueError(f'unknown role {role!r} for {name!r}')
        if len(token) < 24:
            raise ValueError(f'token for {name!r} is too short (need >= 24 characters)')
        tokens.append({'name': name, 'role': role, 'digest': hashlib.sha256(token.encode()).digest()})
    return tokens


def authenticate(tokens, authorization_header, permission):
    """Returns the caller's name, or raises AuthError(401/403)."""
    if not authorization_header or not authorization_header.startswith('Bearer '):
        raise AuthError(401, 'unauthorised')
    presented = hashlib.sha256(authorization_header[7:].strip().encode()).digest()
    caller = None
    for t in tokens:                       # compare against every token in constant time
        if hmac.compare_digest(presented, t['digest']):
            caller = t
    if caller is None:
        raise AuthError(401, 'unauthorised')
    if permission not in PERMISSIONS[caller['role']]:
        raise AuthError(403, 'forbidden')
    return caller['name']


class RateLimiter:
    def __init__(self, per_minute=60):
        self.per_minute = per_minute
        self.hits = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, key, now=None):
        """Returns 0 if allowed, otherwise the number of seconds to wait."""
        now = time.time() if now is None else now
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] >= 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return int(60 - (now - q[0])) + 1
            q.append(now)
            return 0
