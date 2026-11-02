from __future__ import annotations

import hashlib

import redis.asyncio

from django_spec_mcp._compat.utils import namespace_digest, resolve_async_redis
from django_spec_mcp.types import NAMESPACE_DIGEST_CHARS


def test_namespace_digest_is_the_leading_sha256_hex() -> None:
    full = hashlib.sha256(b"my-server").hexdigest()
    assert namespace_digest("my-server") == full[:12]


def test_namespace_digest_length_is_the_shared_constant() -> None:
    assert NAMESPACE_DIGEST_CHARS == 12
    assert len(namespace_digest("anything")) == NAMESPACE_DIGEST_CHARS


def test_namespace_digest_encodes_non_ascii_as_utf8() -> None:
    assert namespace_digest("serveur-é") == hashlib.sha256("serveur-é".encode()).hexdigest()[:12]


def test_resolve_async_redis_returns_the_asyncio_client_class() -> None:
    assert resolve_async_redis() is redis.asyncio.Redis
