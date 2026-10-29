"""The token bucket's own questions; ``test_rate_limit_contract.py`` asks the shared ones."""

from __future__ import annotations

import time

import pytest

from django_spec_mcp.auth.rate_limits.token_bucket_rate_limit import TokenBucketRateLimit
from django_spec_mcp.auth.types.token_info import TokenInfo
from tests.auth.rate_limits.utils import User, http_request


def test_refills_with_real_time() -> None:
    """After draining the bucket, waiting long enough refills at least one token."""
    limiter = TokenBucketRateLimit(capacity=1, refill_per_second=10.0)
    token = TokenInfo(user=User(1))
    assert limiter.consume(http_request(), token) is None
    assert limiter.consume(http_request(), token) is not None  # drained
    time.sleep(0.2)  # 0.2s * 10/s = 2 tokens worth (capped at 1)
    assert limiter.consume(http_request(), token) is None


def test_capacity_caps_refill() -> None:
    """A long pause must not over-fill beyond ``capacity``."""
    limiter = TokenBucketRateLimit(capacity=2, refill_per_second=100.0)
    token = TokenInfo(user=User(1))
    # Drain.
    assert limiter.consume(http_request(), token) is None
    assert limiter.consume(http_request(), token) is None
    # Sleep way longer than needed to refill.
    time.sleep(0.2)  # would refill 20 tokens unbounded
    assert limiter.consume(http_request(), token) is None
    assert limiter.consume(http_request(), token) is None
    # Still capped — third call is denied.
    assert limiter.consume(http_request(), token) is not None


def test_invalid_capacity_rejected() -> None:
    with pytest.raises(ValueError, match="capacity"):
        TokenBucketRateLimit(capacity=0, refill_per_second=1.0)


def test_invalid_refill_rejected() -> None:
    with pytest.raises(ValueError, match="refill_per_second"):
        TokenBucketRateLimit(capacity=1, refill_per_second=0.0)
