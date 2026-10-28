"""``resources/read`` renders a kernel resource through its spec's ``DataclassPresenter``.

The original asked that a resource whose drf-services selector declares a raw
dataclass as its ``output_serializer`` renders it, since the read resolved the
output itself rather than through ``render_spec_output``. A kernel resource has
no such step: ``register_resource(spec, uri_template=..., name=...)`` reads
through the kernel's ``dispatch`` and the spec's presenter renders the row, so
what is asked here is that both handlers serve what the presenter presents.

The original's other questions, in their kernel form:

- a ``LIST`` of dataclasses: a kernel resource reads one row, so a ``LIST`` is
  refused at registration and served as a selector tool instead;
- an output no read could render: a presenter that is not a ``Presenter`` is
  refused when the spec is built, and ``output_serializer=`` beside a kernel
  spec is refused at registration, since the spec declares its own presenter;
- a declaration every read would fail on, refused at registration rather than
  at the first read: a template variable no read declares.

The original is kept beside this file as ``test_resources_read_dataclass_output_drfs.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    DataclassPresenter,
    Parameter,
    Parameters,
    PoolSeeds,
    SelectorKind,
    SelectorSpec,
    Unrestricted,
)
from typing_extensions import TypedDict

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from django_spec_mcp.handlers.types.context import MCPCallContext

OPEN = [Unrestricted()]
_PK = Parameters([Parameter("pk", "integer", required=True)])


@dataclass
class Invoice:
    number: str
    amount_cents: int


def _invoice(*, pk: int) -> Invoice:
    return Invoice(number=f"INV-{pk}", amount_cents=100)


def _server() -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend())
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_invoice,
            reads=_PK,
            presenter=DataclassPresenter(Invoice),
            permissions=OPEN,
        ),
        name="invoice",
        uri_template="invoices://{pk}",
    )
    return server


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


def _body(out: Any) -> Any:
    assert isinstance(out, dict), out
    return json.loads(out["contents"][0]["text"])


@pytest.mark.django_db
def test_sync_read_renders_a_dataclass_retrieve() -> None:
    out = handle_resources_read({"uri": "invoices://7"}, _ctx(_server()))
    assert _body(out) == {"number": "INV-7", "amount_cents": 100}


@pytest.mark.django_db(transaction=True)
async def test_async_read_renders_a_dataclass_retrieve() -> None:
    out = await handle_resources_read_async({"uri": "invoices://7"}, _ctx(_server()))
    assert _body(out) == {"number": "INV-7", "amount_cents": 100}


def test_a_list_of_dataclasses_is_a_tool_not_a_resource() -> None:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend())
    with pytest.raises(ImproperlyConfigured, match="RETRIEVE, not a LIST"):
        server.register_resource(
            SelectorSpec(
                kind=SelectorKind.LIST,
                selector=lambda: [Invoice("INV-1", 100)],
                presenter=DataclassPresenter(Invoice),
                permissions=OPEN,
            ),
            name="invoices",
            uri_template="invoices://all",
        )


def test_a_presenter_no_read_could_render_through_is_refused_when_the_spec_is_built() -> None:
    with pytest.raises(ImproperlyConfigured, match="not a Presenter"):
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda: {},
            presenter=Invoice,  # ty: ignore[invalid-argument-type] - the refusal under test
            permissions=OPEN,
        )


def test_an_output_serializer_beside_a_kernel_spec_is_refused() -> None:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend())
    with pytest.raises(TypeError, match="output_serializer"):
        server.register_resource(
            SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: {}, permissions=OPEN),
            name="bad",
            uri_template="bad://x",
            output_serializer=Invoice,
        )


def test_a_template_variable_no_read_declares_is_refused_at_registration() -> None:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend())
    with pytest.raises(ImproperlyConfigured, match="pk"):
        server.register_resource(
            SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: {}, permissions=OPEN),
            name="bad",
            uri_template="bad://{pk}",
        )


def _requires_an_owner(*, pk: int, owner: str) -> Invoice:
    return Invoice(number=f"{owner}-{pk}", amount_cents=100)


@pytest.mark.parametrize(
    ("spec", "seeds", "offender"),
    [
        pytest.param(
            SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=_requires_an_owner,
                reads=_PK,
                permissions=OPEN,
            ),
            DEFAULT_POOL_SEEDS,
            "owner",
            id="a-keyword-nothing-fills",
        ),
        pytest.param(
            SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=lambda *, pk, tenant: Invoice(number=tenant, amount_cents=pk),
                reads=Parameters([*_PK, Parameter("tenant", "string", required=True)]),
                permissions=OPEN,
            ),
            DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme"),
            "tenant",
            id="a-read-a-registered-seed-occupies",
        ),
    ],
)
def test_a_selector_no_read_can_run_is_refused_at_registration(
    spec: SelectorSpec, seeds: PoolSeeds, offender: str
) -> None:
    # Neither name is a template variable, so the variable checks above pass
    # it; the kernel's ``check_seeds``, asked with the server's seeds, does not.
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), pool_seeds=seeds)
    with pytest.raises(ImproperlyConfigured, match=rf"Resource 'bad': .*\['{offender}'\]"):
        server.register_resource(spec, name="bad", uri_template="bad://{pk}")
    assert len(server.resources) == 0


def test_the_refusal_names_the_resource_and_each_variable_no_read_declares() -> None:
    """``pk`` is declared and so is left out. The other six are listed sorted. The template
    names them in reverse sorted order, so a list built in template order fails, and six
    names make a set that happens to iterate sorted a one-in-720 chance per hash seed
    (checked over ``PYTHONHASHSEED`` 0 to 7), so a list built from a set fails too."""
    server = MCPServer(name="t", auth_backend=AllowAnyBackend())
    with pytest.raises(ImproperlyConfigured) as raised:
        server.register_resource(
            SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_invoice, reads=_PK, permissions=OPEN
            ),
            name="bad",
            uri_template="bad://{zone}/{yard}/{pk}/{mid}/{lane}/{bay}/{area}",
        )
    message = str(raised.value)
    assert "Resource 'bad'" in message
    assert "['area', 'bay', 'lane', 'mid', 'yard', 'zone']" in message
    assert "'pk'" not in message
    assert "fills" not in message


class _PkScope(TypedDict):
    pk: int


def _provides_the_pk() -> _PkScope:
    return {"pk": 1}


class _ManyScope(TypedDict):
    zone: str
    yard: str
    mid: str
    lane: str
    bay: str


def _provides_many() -> _ManyScope:
    return {"zone": "z", "yard": "y", "mid": "m", "lane": "l", "bay": "b"}


def test_the_variables_a_provider_fills_are_listed_sorted() -> None:
    """Five provider-filled names, in reverse sorted order in the template: the same two
    ways to get the order wrong as the undeclared list, held for this one too."""
    server = MCPServer(name="t", auth_backend=AllowAnyBackend())
    names = ["zone", "yard", "mid", "lane", "bay"]
    with pytest.raises(ImproperlyConfigured) as raised:
        server.register_resource(
            SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=_invoice,
                reads=Parameters([Parameter(name, "string") for name in names]),
                kwargs=_provides_many,
                permissions=OPEN,
            ),
            name="bad",
            uri_template="bad://" + "/".join("{" + name + "}" for name in names),
        )
    assert "['bay', 'lane', 'mid', 'yard', 'zone']" in str(raised.value)


def test_a_variable_a_reflected_read_the_server_fills_is_refused() -> None:
    """A read named ``view`` and marked ``reflected`` is declared, and dispatch fills it from
    the call and refuses a caller's argument of its name, so a ``{view}`` variable would
    be answered ``Unknown argument.`` at every read."""
    server = MCPServer(name="t", auth_backend=AllowAnyBackend())
    with pytest.raises(ImproperlyConfigured) as raised:
        server.register_resource(
            SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=_invoice,
                reads=Parameters(
                    [
                        Parameter("pk", "integer", required=True),
                        Parameter("view", "string", reflected=True),
                    ]
                ),
                permissions=OPEN,
            ),
            name="bad",
            uri_template="bad://{pk}/{view}",
        )
    message = str(raised.value)
    assert "Resource 'bad'" in message
    assert "['view']" in message
    assert "server fills" in message
    # Declared, so not the other clause.
    assert "not among" not in message


def test_a_variable_a_provider_always_fills_is_refused() -> None:
    """A typed ``kwargs=`` provider that always returns ``pk`` replaces whatever the URI
    carries, so the read would serve the provider's row for every URI."""
    server = MCPServer(name="t", auth_backend=AllowAnyBackend())
    with pytest.raises(ImproperlyConfigured) as raised:
        server.register_resource(
            SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=_invoice,
                reads=_PK,
                kwargs=_provides_the_pk,
                permissions=OPEN,
            ),
            name="bad",
            uri_template="bad://{pk}",
        )
    message = str(raised.value)
    assert "['pk']" in message
    assert "server fills" in message
    assert "not among" not in message


def test_a_template_naming_both_kinds_of_bad_variable_says_both() -> None:
    """The two clauses are independent: one undeclared variable and one the server fills."""
    server = MCPServer(name="t", auth_backend=AllowAnyBackend())
    with pytest.raises(ImproperlyConfigured) as raised:
        server.register_resource(
            SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=_invoice,
                reads=_PK,
                kwargs=_provides_the_pk,
                permissions=OPEN,
            ),
            name="bad",
            uri_template="bad://{pk}/{other}",
        )
    message = str(raised.value)
    assert "['other']" in message
    assert "not among" in message
    assert "['pk']" in message
    assert "server fills" in message


@pytest.mark.django_db
def test_a_template_variable_naming_a_shape_registers_and_is_read() -> None:
    """A shape is a parameter of the spec too (``parameters()`` is its reads then its
    shapes), which dispatch takes as an argument, so a variable naming one is not
    refused: the check reads ``parameters()`` and not ``reads`` alone."""
    server = MCPServer(name="t", auth_backend=AllowAnyBackend())
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_invoice,
            reads=_PK,
            shapes=Parameters([Parameter("fields", "string")]),
            presenter=DataclassPresenter(Invoice),
            permissions=OPEN,
        ),
        name="invoice",
        uri_template="invoices://{pk}/{fields}",
    )
    out = handle_resources_read({"uri": "invoices://7/number"}, _ctx(server))
    assert _body(out)["number"] == "INV-7"
