"""``register_ui_resource`` takes a concrete URI: a host fetches a view once and
expands nothing, so a ``{var}`` in ``uri=`` names a view no host can ask for."""

from __future__ import annotations

import pytest

from django_spec_mcp.server.mcp_server import MCPServer
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.utils import make_server


def _server() -> MCPServer:
    return make_server(
        name="test",
        description="d",
        session_store=InMemorySessionStore(),
    )


class TestATemplatedViewUri:
    @pytest.mark.parametrize(
        "uri",
        ["ui://invoices/{pk}/detail.html", "ui://{a}-{b}", "{pk}"],
    )
    def test_is_refused_naming_the_uri(self, uri: str) -> None:
        server = _server()

        with pytest.raises(ValueError, match="register_ui_resource") as raised:
            server.register_ui_resource(name="detail", uri=uri, html="<h1>x</h1>")

        assert repr(uri) in str(raised.value)
        assert "concrete" in str(raised.value)

    def test_leaves_nothing_registered(self) -> None:
        server = _server()

        with pytest.raises(ValueError):
            server.register_ui_resource(
                name="detail", uri="ui://invoices/{pk}.html", html="<h1>x</h1>"
            )

        assert len(server._resources) == 0

    def test_is_refused_before_the_content_source_is_judged(self) -> None:
        """A templated URI is the first thing wrong with a call that also has no
        content source, so the author is told about the URI."""
        with pytest.raises(ValueError, match="ui://invoices/\\{pk\\}"):
            _server().register_ui_resource(name="detail", uri="ui://invoices/{pk}")

    def test_a_concrete_uri_registers(self) -> None:
        binding = _server().register_ui_resource(
            name="table", uri="ui://invoices/table.html", html="<h1>x</h1>"
        )

        assert binding.uri_template == "ui://invoices/table.html"
