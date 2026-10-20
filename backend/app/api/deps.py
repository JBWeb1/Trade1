from __future__ import annotations

import secrets
from functools import lru_cache

from fastapi import Depends, Header, HTTPException, status

from ..config import Settings, get_settings
from ..mt5.gateway import MT5Gateway


def require_token(authorization: str | None = Header(default=None),
                  settings: Settings = Depends(get_settings)) -> str:
    """Bearer-token auth. Fails closed: if no token is configured, every
    protected route is refused."""
    expected = settings.api_token.get_secret_value() if settings.api_token else ""
    if not expected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "TRADE1_API_TOKEN is not configured on the server")
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing bearer token",
                            headers={"WWW-Authenticate": "Bearer"})
    return "api-user"


@lru_cache
def _gateway() -> MT5Gateway:
    return MT5Gateway(get_settings())


def get_gateway() -> MT5Gateway:
    return _gateway()
