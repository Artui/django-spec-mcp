"""What the three limiters promise alike, asked of each.

The schemes differ in how they count (their own files ask that); a deployment
swapping one for another may rely on the rest, so it is held for all three. Each
factory takes the number of calls it admits before it refuses, with a refill slow
enough that nothing comes back during a test.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.http import HttpRequest

from django_spec_mcp.auth.rate_limits.fixed_window_rate_limit import FixedWindowRateLimit
from django_spec_mcp.auth.rate_limits.sliding_window_rate_limit import SlidingWindowRateLimit
from django_spec_mcp.auth.rate_limits.token_bucket_rate_limit import TokenBucketRateLimit
from django_spec_mcp.auth.rate_limits.types.mcp_rate_limit import MCPRateLimit
from django_spec_mcp.auth.types.token_info import TokenInfo
from tests.auth.rate_limits.utils import User, http_request

_FACTORIES: dict[str, Callable[..., Any]] = {
    "fixed": lambda calls, **kw: FixedWindowRateLimit(max_calls=calls, per_seconds=60, **kw),
    "sliding": lambda calls, **kw: SlidingWindowRateLimit(max_calls=calls, per_seconds=60, **kw),
    "bucket": lambda calls, **kw: TokenBucketRateLimit(
        capacity=calls, refill_per_second=0.01, **kw
    ),
}


@pytest.fixture(params=list(_FACTORIES), ids=list(_FACTORIES))
def make(request: pytest.FixtureRequest) -> Callable[..., Any]:
    return _FACTORIES[request.param]


def test_calls_under_the_limit_are_allowed(make: Callable[..., Any]) -> None:
    limiter = make(3)
    token = TokenInfo(user=User(1))
    assert [limiter.consume(http_request(), token) for _ in range(3)] == [None, None, None]


def test_the_call_over_the_limit_returns_a_positive_retry_after(make: Callable[..., Any]) -> None:
    limiter = make(2)
    token = TokenInfo(user=User(1))
    limiter.consume(http_request(), token)
    limiter.consume(http_request(), token)
    retry = limiter.consume(http_request(), token)
    assert isinstance(retry, int)
    assert retry > 0


def test_buckets_are_per_user(make: Callable[..., Any]) -> None:
    limiter = make(1)
    a = TokenInfo(user=User(1))
    b = TokenInfo(user=User(2))
    assert limiter.consume(http_request(), a) is None
    assert limiter.consume(http_request(), b) is None
    assert limiter.consume(http_request(), a) is not None  # a is over its bucket
    assert limiter.consume(http_request(), b) is not None  # b is over its bucket


def test_anonymous_falls_back_to_remote_addr(make: Callable[..., Any]) -> None:
    limiter = make(1)
    token = TokenInfo(user=None)
    assert limiter.consume(http_request(), token) is None
    assert limiter.consume(http_request(), token) is not None


def test_anonymous_unknown_remote_addr(make: Callable[..., Any]) -> None:
    """Missing ``REMOTE_ADDR`` falls back to the literal ``unknown`` bucket."""
    limiter = make(1)
    token = TokenInfo(user=None)
    req = http_request(None)
    assert limiter.consume(req, token) is None
    assert limiter.consume(req, token) is not None


def test_custom_key_callable(make: Callable[..., Any]) -> None:
    seen: list[str] = []

    def key(request: HttpRequest, token: TokenInfo) -> str:
        seen.append("called")
        return "static"

    limiter = make(1, key=key)
    assert limiter.consume(http_request(), TokenInfo(user=User(1))) is None
    # Another user, but the custom key collapses both into one bucket.
    assert limiter.consume(http_request(), TokenInfo(user=User(2))) is not None
    assert seen  # the key callable was actually invoked


def test_namespace_isolates_counters(make: Callable[..., Any]) -> None:
    burst = make(1, namespace="burst")
    steady = make(1, namespace="steady")
    token = TokenInfo(user=User(1))
    assert burst.consume(http_request(), token) is None
    assert steady.consume(http_request(), token) is None  # separate counter
    assert burst.consume(http_request(), token) is not None


def test_satisfies_mcprate_limit_protocol(make: Callable[..., Any]) -> None:
    assert isinstance(make(1), MCPRateLimit)
