"""A server with somewhere for each ``AgentConventions`` line to land, on kernel specs.

- ``invoices.list`` is a paged selector tool declaring a read-shaping ``fields``
  shape, so its param carries the scope sentence.
- ``invoices.rename`` is a service tool resolving its target through a lookup
  reading a required ``pk``, so a call leaving ``pk`` out earns the
  missing-argument message.
- Both present ``_Row``, whose ``id`` is a handle with no wording of its own, so
  each tool's ``outputSchema`` carries the handle description and its
  ``description`` the handle line.

The drf-services server this replaces is kept in ``conventions_drfs.py`` until
the drfs path is deleted. It also lands the scope sentence in the refusal of a
selection its serializer rejects while rendering, which is a ``QueryParam``
answer with no kernel counterpart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from django_service_specs import (
    DataclassPresenter,
    DataclassValidator,
    FieldMarking,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import AgentConventions, MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from tests.testapp.models import Invoice

OPEN = [Unrestricted()]


@dataclass
class _Row:
    """An invoice, its ``id`` a handle with no wording of its own."""

    id: Annotated[int, FieldMarking.handle()]
    number: str


@dataclass
class _Rename:
    number: str


def _invoices() -> Any:
    return Invoice.objects.order_by("pk")


def _by_pk(*, pk: int) -> Invoice | None:
    return Invoice.objects.filter(pk=pk).first()


def _rename(*, instance: Invoice, number: str) -> Invoice:
    instance.number = number
    instance.save()
    return instance


def conventions_server(
    conventions: AgentConventions | None = None, **server_kwargs: Any
) -> MCPServer:
    """A fresh server speaking ``conventions``, or the defaults when ``None``.

    ``server_kwargs`` reach ``MCPServer`` as given: a task store and executor
    for a test that runs a call as a task.
    """
    server = MCPServer(
        name="conventions",
        auth_backend=AllowAnyBackend(),
        session_store=None,
        conventions=conventions,
        **server_kwargs,
    )
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_invoices,
            shapes=Parameters([Parameter("fields", "string", help="Fields to return.")]),
            presenter=DataclassPresenter(_Row),
            permissions=OPEN,
        ),
        name="invoices.list",
        description="List invoices.",
        paginate=True,
    )
    server.register_tool(
        ServiceSpec(
            service=_rename,
            atomic=False,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=_by_pk,
                reads=Parameters([Parameter("pk", "integer", required=True)]),
                permissions=OPEN,
            ),
            validator=DataclassValidator(_Rename),
            presenter=DataclassPresenter(_Row),
            permissions=OPEN,
        ),
        name="invoices.rename",
        description="Rename an invoice.",
    )
    return server


__all__ = ["conventions_server"]
