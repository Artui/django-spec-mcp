"""What fills a kernel callable's required parameter, and what registration does when nothing does.

A kernel spec's callable receives, by name, what ``dispatch`` puts in its pool:
the dispatcher's seeds (``user``, ``data``, ``call_context`` and the rest), the
call's own ``request`` and ``view``, the spec's declared ``reads`` and
Validator fields, and what a ``kwargs=`` provider returns. Each of those is
asked here to fill a required parameter of a kernel spec registered with
``register_tool``, end to end through ``call_tool``.

A required parameter none of them fills was refused at registration on the
drf-services path, because every call to such a tool raises ``TypeError`` out of
dispatch. The kernel route registers it, lists it, and raises on every call:
the strict xfail below holds the cases until the kernel refuses such a spec when
it is built, which is where the pool is known, rather than in a check of this
package's own beside it.

The drf-services original asked this of trust mode, ``ArgumentBinding``,
``UrlKwarg`` sources and an ``input_serializer``'s fields under each binding;
none has a kernel counterpart, since a kernel spec declares its arguments. It
is kept beside this file as ``test_required_param_sources_drfs.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend

_PK = Parameters([Parameter("pk", "integer", required=True)])


def _service(service: Any, **declared: Any) -> ServiceSpec:
    return ServiceSpec(service=service, atomic=False, permissions=[Unrestricted()], **declared)


def _retrieve(selector: Any, **declared: Any) -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=selector, permissions=[Unrestricted()], **declared
    )


def _server(spec: Any) -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_tool(spec, name="t", description="t")
    return server


def _served(spec: Any, arguments: dict[str, Any]) -> Any:
    out = _server(spec).call_tool("t", arguments, user=None).to_dict()
    assert out.get("isError") is not True, out
    return out["structuredContent"]


def test_a_required_seed_parameter_is_filled_by_dispatch() -> None:
    assert _served(_service(lambda *, user: user.is_anonymous), {}) is True


def test_a_required_data_parameter_without_a_validator_receives_an_empty_payload() -> None:
    # Unlike the drf-services path, which refused a bundled service requiring
    # ``data`` with no ``input_serializer``: the kernel always seeds ``data``.
    assert _served(_service(lambda *, data: dict(data)), {}) == {}


def test_the_calls_own_view_and_context_reach_a_required_parameter() -> None:
    served = _served(_service(lambda *, view, call_context: [view, sorted(call_context)]), {})

    assert served == [None, ["action", "format", "request", "view"]]


def test_a_required_parameter_a_read_declares_is_the_callers() -> None:
    assert _served(_retrieve(lambda *, pk: pk, reads=_PK), {"pk": 7}) == 7


def test_a_required_parameter_a_provider_fills_needs_no_argument() -> None:
    assert _served(_retrieve(lambda *, pk: pk, kwargs=lambda: {"pk": 3}), {}) == 3


def test_a_defaulted_parameter_nothing_fills_runs_on_its_default() -> None:
    assert _served(_retrieve(lambda *, pk=5: pk), {}) == 5


@pytest.mark.xfail(
    strict=True,
    # "DID NOT RAISE" only, so no other error can keep the test quietly xfailed.
    raises=pytest.fail.Exception,
    reason=(
        "register_tool accepts a callable whose required parameter nothing in the pool "
        "fills, and every call raises TypeError out of dispatch. Fix: the kernel refuses "
        "such a spec when it is built, naming the parameter, as it refuses a parameter "
        "named after a reserved seed."
    ),
)
@pytest.mark.parametrize(
    "build",
    [
        # Factories, built inside the block: a spec refused when it is built
        # must neither fail collection nor stay xfailed for the wrong reason.
        pytest.param(lambda: _service(lambda *, other: other), id="service"),
        pytest.param(lambda: _retrieve(lambda *, pk: pk), id="selector"),
        pytest.param(
            lambda: _service(lambda *, instance: instance), id="instance-without-a-lookup"
        ),
        pytest.param(lambda: _retrieve(lambda pk, /: pk, reads=_PK), id="positional-only"),
    ],
)
def test_a_required_parameter_nothing_fills_is_refused_at_registration(
    build: Callable[[], Any],
) -> None:
    with pytest.raises(ImproperlyConfigured):
        _server(build())
