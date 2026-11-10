from __future__ import annotations

import pytest
from django_service_specs import (
    DataclassPresenter,
    SelectorKind,
    SelectorSpec,
    Unrestricted,
)

from django_spec_mcp.registry.resource_registry import ResourceRegistry
from django_spec_mcp.registry.types.resource_binding import ResourceBinding
from django_spec_mcp.registry.types.ui_tool_meta import UIToolMeta
from django_spec_mcp.server.mcp_server import MCPServer
from django_spec_mcp.types.ui_resource_mime_type import UI_RESOURCE_MIME_TYPE
from tests.server.test_ui_tool_linkage import _Row
from tests.utils import make_server


def _server() -> MCPServer:
    return make_server(name="test", description="d")


def _binding(uri_template: str, mime_type: str) -> ResourceBinding:
    return ResourceBinding(
        name="pair",
        uri_template=uri_template,
        description=None,
        selector=lambda *_args, **_kwargs: "<p/>",
        mime_type=mime_type,
    )


def _list_spec() -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.LIST,
        selector=lambda: [],
        presenter=DataclassPresenter(_Row),
        permissions=[Unrestricted()],
    )


def test_a_hand_registered_templated_view_is_refused_at_register() -> None:
    server = _server()

    with pytest.raises(ValueError, match=r"ui://\{a\}-\{b\}"):
        server.resources.register(_binding("ui://{a}-{b}", UI_RESOURCE_MIME_TYPE))

    assert server.resources.all() == []


def test_a_concrete_view_binding_still_registers() -> None:
    # Kills the mutant that drops the template conjunct: a view with no ``{var}``
    registry = ResourceRegistry()
    binding = _binding("ui://invoices/table.html", UI_RESOURCE_MIME_TYPE)

    registry.register(binding)

    assert registry.all() == [binding]


def test_a_templated_data_resource_still_registers() -> None:
    # Kills the mutant that drops the mime conjunct: a data resource's template
    registry = ResourceRegistry()
    binding = _binding("things://{a}-{b}", "text/plain")

    registry.register(binding)

    assert registry.all() == [binding]


def test_a_data_resource_template_splitting_the_link_two_ways_names_the_tool() -> None:
    server = _server()
    server.resources.register(_binding("things://{a}-{b}", "text/plain"))

    with pytest.raises(ValueError, match="list_invoices") as raised:
        server.register_tool(
            _list_spec(),
            name="list_invoices",
            description="d",
            paginate=True,
            ui=UIToolMeta(resource_uri="things://x-y-z"),
        )

    assert "splits more than one way" in str(raised.value)
    assert isinstance(raised.value.__cause__, ValueError)
