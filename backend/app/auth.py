"""Verify Supabase access tokens.

Supabase signs tokens with asymmetric keys published as a JWKS. Shared-secret
(HS256) tokens are rejected: that signing method is being retired.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings

ALGORITHMS = ["ES256", "RS256", "EdDSA"]

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    auth_user_id: UUID
    phone: str | None


@lru_cache
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=600)


def verify_token(token: str, settings: Settings, jwks: jwt.PyJWKClient | None = None) -> Principal:
    try:
        key = (jwks or _jwks_client(settings.jwks_url)).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key=key,
            algorithms=ALGORITHMS,
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "sub", "aud", "iss"]},
        )
        return Principal(auth_user_id=UUID(claims["sub"]), phone=claims.get("phone") or None)
    except (jwt.PyJWTError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal:
    if credentials is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return verify_token(credentials.credentials, settings)
