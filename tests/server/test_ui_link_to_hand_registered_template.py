"""A ``ui=`` link meets the ambiguity refusal through a hand-registered template too.

``register_ui_resource`` refuses a templated ``uri=``, but ``server.resources.register``
still takes a ``ResourceBinding`` built by hand, with the view mime type and a
templated URI. The docs say the refusal is reachable through any template
``register_ui_resource`` did not build (a data resource's, or this), so this pins it.
"""

from __future__ import annotations

import pytest
from django_service_specs import (
    DataclassPresenter,
    SelectorKind,
    SelectorSpec,
    Unrestricted,
)

from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.registry.types.resource_binding import ResourceBinding
from django_spec_mcp.registry.types.ui_tool_meta import UIToolMeta
from django_spec_mcp.server.mcp_server import MCPServer
from django_spec_mcp.types.ui_resource_mime_type import UI_RESOURCE_MIME_TYPE
from tests.server.test_ui_tool_linkage import _Row


def test_a_hand_registered_view_template_splits_two_ways_and_names_the_tool() -> None:
    server = MCPServer(name="test", description="d", auth_backend=AllowAnyBackend())
    server.resources.register(
        ResourceBinding(
            name="pair",
            uri_template="ui://{a}-{b}",
            description=None,
            selector=lambda a, b: "<p/>",  # noqa: ARG005
            mime_type=UI_RESOURCE_MIME_TYPE,
        )
    )
    spec = SelectorSpec(
        kind=SelectorKind.LIST,
        selector=lambda: [],
        presenter=DataclassPresenter(_Row),
        permissions=[Unrestricted()],
    )

    with pytest.raises(ValueError, match="list_invoices") as raised:
        server.register_tool(
            spec,
            name="list_invoices",
            description="d",
            paginate=True,
            ui=UIToolMeta(resource_uri="ui://x-y-z"),
        )

    assert "splits more than one way" in str(raised.value)
    assert isinstance(raised.value.__cause__, ValueError)
