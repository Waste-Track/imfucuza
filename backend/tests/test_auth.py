import time
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException

from app.auth import current_principal, verify_token
from app.config import Settings

SETTINGS = Settings(supabase_url="https://example.supabase.co")
PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())


class StubJwks:
    """Stands in for the project's JWKS endpoint."""

    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK:
        public = jwt.algorithms.ECAlgorithm.to_jwk(PRIVATE_KEY.public_key(), as_dict=True)
        return jwt.PyJWK({**public, "kid": "test-key", "alg": "ES256"})


def token(**overrides) -> str:
    now = int(time.time())
    claims = {
        "sub": str(uuid4()),
        "aud": "authenticated",
        "iss": "https://example.supabase.co/auth/v1",
        "iat": now,
        "exp": now + 3600,
        "phone": "233241234567",
        **overrides,
    }
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, PRIVATE_KEY, algorithm="ES256", headers={"kid": "test-key"})


def test_valid_token_gives_the_supabase_user():
    sub = str(uuid4())

    principal = verify_token(token(sub=sub), SETTINGS, StubJwks())

    assert str(principal.auth_user_id) == sub
    assert principal.phone == "233241234567"


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": int(time.time()) - 60},
        {"aud": "anon"},
        {"iss": "https://attacker.example/auth/v1"},
        {"sub": "not-a-uuid"},
        {"sub": None},
    ],
    ids=["expired", "wrong-audience", "wrong-issuer", "bad-subject", "no-subject"],
)
def test_bad_tokens_are_rejected(claims):
    with pytest.raises(HTTPException) as exc:
        verify_token(token(**claims), SETTINGS, StubJwks())

    assert exc.value.status_code == 401


def test_token_signed_by_another_key_is_rejected():
    other_key = ec.generate_private_key(ec.SECP256R1())
    now = int(time.time())
    forged = jwt.encode(
        {
            "sub": str(uuid4()),
            "aud": "authenticated",
            "iss": "https://example.supabase.co/auth/v1",
            "exp": now + 60,
        },
        other_key,
        algorithm="ES256",
        headers={"kid": "test-key"},
    )

    with pytest.raises(HTTPException) as exc:
        verify_token(forged, SETTINGS, StubJwks())

    assert isinstance(exc.value.__cause__, jwt.InvalidSignatureError)


def test_shared_secret_tokens_are_rejected_for_their_algorithm():
    forged = jwt.encode(
        {
            "sub": str(uuid4()),
            "aud": "authenticated",
            "iss": "https://example.supabase.co/auth/v1",
            "exp": int(time.time()) + 60,
        },
        "a-leaked-shared-secret-of-32-bytes!",
        algorithm="HS256",
    )

    with pytest.raises(HTTPException) as exc:
        verify_token(forged, SETTINGS, StubJwks())

    assert isinstance(exc.value.__cause__, jwt.InvalidAlgorithmError)


def test_missing_bearer_token_is_rejected():
    with pytest.raises(HTTPException) as exc:
        current_principal(None, SETTINGS)

    assert exc.value.status_code == 401
