"""The awaitable-in-a-decision guard, and every hook it covers.

Two halves. The first exercises :func:`reject_awaitable` directly. The second
is the point of the file: **one parametrized case per consumer-supplied hook
that a sync path consults**, so the family is asserted as a family. Adding a
sixth hook and forgetting to guard it should look like a missing row here, not
like nothing at all.

Three of these fail *open* without the guard — an ``async def`` returns a
truthy coroutine and the caller is authenticated, granted, or shown every
binding. That is why refusing is not a downgrade: the behaviour it replaces is
a silent, total bypass.
"""

from __future__ import annotations

import inspect
from collections.abc import Generator
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django_service_specs import PermissionCheck

from django_spec_mcp._compat.reject_awaitable import reject_awaitable
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.is_binding_listable import is_binding_listable
from django_spec_mcp.handlers.judge_binding import judge_binding
from django_spec_mcp.handlers.utils import consume_rate_limits
from django_spec_mcp.registry.types.prompt_binding import PromptBinding

_TOKEN = TokenInfo(user=None, scopes=())


def _request() -> HttpRequest:
    return HttpRequest()


# ----- the guard itself -----


def test_a_plain_value_passes_through_unchanged() -> None:
    sentinel = object()
    assert reject_awaitable(sentinel, call="c()", remedy="r", hazard="h") is sentinel


def test_a_falsy_value_passes_through_too() -> None:
    # The guard must not conflate "awaitable" with "truthy": a permission that
    # legitimately returns ``False`` still has to reach the caller as ``False``.
    assert reject_awaitable(False, call="c()", remedy="r", hazard="h") is False


def test_a_coroutine_is_refused_and_closed() -> None:
    async def _hook() -> None: ...

    coroutine = _hook()
    with pytest.raises(ImproperlyConfigured):
        reject_awaitable(coroutine, call="Thing.hook()", remedy="r", hazard="h")

    # Closed, so CPython does not tack a "never awaited" RuntimeWarning onto
    # the traceback at collection time and bury the actionable error.
    assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED


def test_a_non_coroutine_awaitable_is_refused() -> None:
    """Detection is on the value, not the function — a future-like counts."""

    class _Awaitable:
        def __await__(self) -> Generator[Any, None, None]:
            # Never driven; the method exists only to satisfy ``isawaitable``.
            yield  # pragma: no cover

    with pytest.raises(ImproperlyConfigured):
        reject_awaitable(_Awaitable(), call="Thing.hook()", remedy="r", hazard="h")


def test_the_message_carries_all_three_parts() -> None:
    async def _hook() -> None: ...

    with pytest.raises(ImproperlyConfigured) as excinfo:
        reject_awaitable(
            _hook(),
            call="Thing.hook()",
            remedy="Make it a plain def.",
            hazard="everyone gets in.",
        )

    message = str(excinfo.value)
    assert "Thing.hook()" in message
    assert "Make it a plain def." in message
    assert "everyone gets in." in message


# ----- the family -----


class _HandsBackACoroutine(PermissionCheck):
    """A plain ``def`` handing back a coroutine, which no binding can refuse when built."""

    def has_permission(self, principal: Any, spec: Any) -> Any:  # noqa: ARG002
        async def later() -> bool:
            return False  # pragma: no cover — never awaited, which is the defect

        return later()


class _AsyncRateLimit:
    async def consume(self, request: HttpRequest, token: TokenInfo) -> int | None:  # noqa: ARG002
        return None  # pragma: no cover — never awaited, which is the defect


def _prompt() -> PromptBinding:
    return PromptBinding(
        name="p", description=None, render=lambda: "", permissions=(_HandsBackACoroutine(),)
    )


def _call_judge_binding() -> None:
    judge_binding(_prompt(), _TOKEN)


def _call_listable_has_permission() -> None:
    is_binding_listable(_prompt(), _request(), _TOKEN)


def _call_consume_rate_limits() -> None:
    consume_rate_limits((_AsyncRateLimit(),), _request(), _TOKEN)


@pytest.mark.parametrize(
    ("call", "named"),
    [
        (_call_judge_binding, "_HandsBackACoroutine.has_permission()"),
        (_call_listable_has_permission, "_HandsBackACoroutine.has_permission()"),
        (_call_consume_rate_limits, "_AsyncRateLimit.consume()"),
    ],
    ids=["prompt or view permission", "list has_permission", "rate limit"],
)
def test_an_async_hook_is_refused_and_named(call: Any, named: str) -> None:
    with pytest.raises(ImproperlyConfigured) as excinfo:
        call()

    message = str(excinfo.value)
    # Naming the offending class is the whole value: "returned an awaitable"
    # alone leaves an operator grepping a settings module.
    assert named in message
    assert "async_to_sync" in message


def test_the_rate_limiter_refusal_explains_that_it_fails_closed() -> None:
    """The one that denies rather than allows still gets a legible cause.

    Left unguarded this is a permanent outage whose only symptom is an
    unserialisable ``retryAfter`` — worse to diagnose than the fail-open ones,
    even though it is safer.
    """
    with pytest.raises(ImproperlyConfigured) as excinfo:
        _call_consume_rate_limits()

    assert "retryAfter" in str(excinfo.value)
