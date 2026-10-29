"""The sliding window's own questions; ``test_rate_limit_contract.py`` asks the shared ones."""

from __future__ import annotations

import time

import pytest

from django_spec_mcp.auth.rate_limits.sliding_window_rate_limit import SlidingWindowRateLimit
from django_spec_mcp.auth.types.token_info import TokenInfo
from tests.auth.rate_limits.utils import User, http_request


def test_window_slides_with_real_time() -> None:
    """Pruning expires old timestamps so capacity returns smoothly."""
    limiter = SlidingWindowRateLimit(max_calls=2, per_seconds=1)
    token = TokenInfo(user=User(1))
    assert limiter.consume(http_request(), token) is None
    assert limiter.consume(http_request(), token) is None
    assert limiter.consume(http_request(), token) is not None  # at the cap
    # Wait for the window to slide past the first two timestamps.
    time.sleep(1.1)
    assert limiter.consume(http_request(), token) is None


def test_invalid_max_calls_rejected() -> None:
    with pytest.raises(ValueError, match="max_calls"):
        SlidingWindowRateLimit(max_calls=0, per_seconds=60)


def test_invalid_per_seconds_rejected() -> None:
    with pytest.raises(ValueError, match="per_seconds"):
        SlidingWindowRateLimit(max_calls=1, per_seconds=0)
