"""A kernel spec dispatch refuses on every call is refused when its tool is registered.

A kernel spec is checked as it is constructed, so most of what drf-services
refused only when it compiled a spec on first read (an output selector
declaring ``preconditions``, a ``reads`` entry the dispatcher seeds, such as
``user``) never reaches a registrar. One refusal still arrives late: a read
named after a seed this server's calls carry, the call's own ``request`` and
``view`` or a seed registered on ``pool_seeds=``. The kernel refuses it at
dispatch, so the tool registers, is listed with the read as an argument, and
fails every call with ``ImproperlyConfigured``. Registration should refuse it.

The kernel decides which reads a seed may share a name with (a ``reflected``
read, a top-level Validator field) in ``dispatch.utils.seed_named_parameters``,
which it does not export, and every local copy of such a reader has drifted
from dispatch, so the refusal waits on that export and the tests are strict
xfails until then.

The drf-services original is kept beside this file as
``test_registration_compiles_the_spec_drfs.py``; its ``QueryParam`` and
``field_audiences`` cases have no kernel counterpart, because a kernel spec
declares reads with ``Parameter`` and carries its markings on its presenter.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    DataclassPresenter,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend

_AWAITS_THE_KERNEL = (
    "the kernel's seed_named_parameters is not exported; registration calls it "
    "for every kernel tool once it is"
)


@dataclass
class _Row:
    pk: int


def _rows() -> list[_Row]:
    return [_Row(pk=1)]


def _server() -> MCPServer:
    return MCPServer(
        name="t",
        auth_backend=AllowAnyBackend(),
        session_store=None,
        pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme"),
    )


def _spec(read: str) -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.LIST,
        selector=_rows,
        reads=Parameters([Parameter(read, "string", required=False)]),
        permissions=[Unrestricted()],
        presenter=DataclassPresenter(_Row),
    )


@pytest.mark.xfail(strict=True, reason=_AWAITS_THE_KERNEL)
@pytest.mark.parametrize("read", ["request", "view", "tenant"])
def test_a_read_a_seed_occupies_is_refused_at_registration(read: str) -> None:
    server = _server()
    with pytest.raises(ImproperlyConfigured, match=rf"'sel'.*\['{read}'\]"):
        server.register_tool(_spec(read), name="sel", description="d", paginate=True)
    assert "sel" not in server.tools


def test_a_read_named_otherwise_registers_and_is_called() -> None:
    server = _server()
    server.register_tool(_spec("serializer"), name="sel", description="d", paginate=True)
    result = server.call_tool("sel", {"serializer": "x"}, user=None)
    assert result.is_error is False
