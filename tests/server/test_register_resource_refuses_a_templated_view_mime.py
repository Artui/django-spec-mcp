from __future__ import annotations

import pytest
from django_service_specs import (
    DataclassPresenter,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    Unrestricted,
)

from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.server.mcp_server import MCPServer
from django_spec_mcp.types.resource_encoding import ResourceEncoding
from django_spec_mcp.types.ui_resource_mime_type import UI_RESOURCE_MIME_TYPE
from tests.server.test_ui_tool_linkage import _Row


def _thing_spec() -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda **reads: _Row(pk=int(reads["pk"])),
        reads=Parameters([Parameter("pk", "string", required=True)]),
        presenter=DataclassPresenter(_Row),
        permissions=[Unrestricted()],
    )


def test_register_resource_with_the_view_mime_type_and_a_template_is_refused() -> None:
    server = MCPServer(name="test", description="d", auth_backend=AllowAnyBackend())

    with pytest.raises(ValueError, match=r"open://\{pk\}"):
        server.register_resource(
            _thing_spec(),
            name="open",
            uri_template="open://{pk}",
            mime_type=UI_RESOURCE_MIME_TYPE,
            encoding=ResourceEncoding.TEXT,
        )

    assert server.resources.all() == []
