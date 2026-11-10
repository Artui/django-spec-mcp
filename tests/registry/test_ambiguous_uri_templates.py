from __future__ import annotations

from typing import Any

import pytest
from django_service_specs import (
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer, SubscriptionFilter
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.registry.resource_registry import ResourceRegistry
from django_spec_mcp.registry.types.resource_binding import ResourceBinding
from django_spec_mcp.subscriptions.grant_subscription import grant_subscription
from django_spec_mcp.subscriptions.utils import topic_for_resource
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode
from tests.utils import call_context, plain_context


def _pair(*, a: str, b: str) -> dict[str, str]:
    return {"a": a, "b": b}


def _file(*, name: str, ext: str) -> dict[str, str]:
    return {"name": name, "ext": ext}


def _spec(selector: Any, *names: str) -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=selector,
        reads=Parameters([Parameter(name, "string", required=True) for name in names]),
        permissions=[Unrestricted()],
    )


def _server() -> MCPServer:
    server = MCPServer(name="uris", auth_backend=AllowAnyBackend())
    server.register_resource(_spec(_pair, "a", "b"), name="pair", uri_template="things://{a}-{b}")
    server.register_resource(
        _spec(_file, "name", "ext"), name="file", uri_template="files://{name}.{ext}"
    )
    return server


def _read(uri: str, protocol_version: str = "2025-11-25") -> Any:
    server = _server()
    return handle_resources_read(
        {"uri": uri}, plain_context(server, protocol_version=protocol_version)
    )


@pytest.mark.parametrize("protocol_version", ["2025-11-25", "2026-07-28"])
def test_a_read_its_template_splits_two_ways_is_refused(protocol_version: str) -> None:
    """-32602 in both eras, where an unknown URI is -32002 in the older one."""
    out = _read("things://1-2-3", protocol_version)
    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.INVALID_PARAMS
    assert "ambiguous" in out.message.lower()
    assert "things://{a}-{b}" in out.message
    assert "'a': '1-2', 'b': '3'" in out.message
    assert "'a': '1', 'b': '2-3'" in out.message
    assert out.data == {"uri": "things://1-2-3"}


def test_the_same_template_still_serves_a_uri_it_splits_one_way() -> None:
    out = _read("things://1-2")
    assert not isinstance(out, JsonRpcError)
    assert '"a": "1"' in out["contents"][0]["text"]
    assert '"b": "2"' in out["contents"][0]["text"]


def test_a_file_template_serves_report_pdf_and_refuses_a_tar_gz() -> None:
    """The docs' worked example, both halves."""
    served = _read("files://report.pdf")
    assert not isinstance(served, JsonRpcError)
    assert '"name": "report"' in served["contents"][0]["text"]
    assert '"ext": "pdf"' in served["contents"][0]["text"]

    refused = _read("files://a.tar.gz")
    assert isinstance(refused, JsonRpcError)
    assert refused.code == JsonRpcErrorCode.INVALID_PARAMS
    assert "files://{name}.{ext}" in refused.message
    assert "'name': 'a.tar', 'ext': 'gz'" in refused.message
    assert "'name': 'a', 'ext': 'tar.gz'" in refused.message


def test_a_subscription_drops_an_ambiguous_uri_and_keeps_its_unambiguous_sibling() -> None:
    """Refused the way every other unwatchable URI is: omitted from the grant, not an error."""
    granted, topics = grant_subscription(
        SubscriptionFilter(resource_uris=("things://1-2-3", "things://1-2")),
        call_context(_server()),
    )
    assert granted.resource_uris == ("things://1-2",)
    assert topics == frozenset({topic_for_resource("things://1-2")})


def _binding(uri_template: str) -> ResourceBinding:
    return ResourceBinding(
        name="r", uri_template=uri_template, description=None, selector=lambda: None
    )


def test_resolve_raises_naming_the_uri_template_and_both_splits() -> None:
    reg = ResourceRegistry()
    reg.register(_binding("things://{a}-{b}"))
    with pytest.raises(ValueError, match=r"Ambiguous resource URI 'things://1-2-3'"):
        reg.resolve("things://1-2-3")


def test_three_variables_split_one_way_resolve() -> None:
    reg = ResourceRegistry()
    reg.register(_binding("v://{a}.{b}-{c}"))
    found = reg.resolve("v://x.y-z")
    assert found is not None
    assert found[1] == {"a": "x", "b": "y", "c": "z"}


def test_variables_separated_by_a_slash_split_one_way() -> None:
    """A value never holds a slash, so a slash between two variables leaves one split."""
    reg = ResourceRegistry()
    reg.register(_binding("v://{a}/{b}"))
    found = reg.resolve("v://x-y.z/w.v-u")
    assert found is not None
    assert found[1] == {"a": "x-y.z", "b": "w.v-u"}


def test_a_trailing_newline_is_no_false_ambiguity_and_no_value() -> None:
    """``$`` matches before a final newline, so a greedy ``{pk}`` would take it."""
    reg = ResourceRegistry()
    template = _binding("invoices://{pk}")
    reg.register(template)
    reg.register(_binding("reports://"))
    assert reg.resolve("invoices://7") == (template, {"pk": "7"})
    assert reg.resolve("invoices://7\n") is None
    assert reg.resolve("reports://\n") is None


def test_an_ambiguous_uri_is_refused_rather_than_handed_to_a_later_template() -> None:
    """Which binding answers must not turn on whether values repeat a template's literals."""
    reg = ResourceRegistry()
    reg.register(_binding("things://{a}-{b}"))
    reg.register(_binding("things://{x}"))
    with pytest.raises(ValueError, match="things://\\{a\\}-\\{b\\}"):
        reg.resolve("things://1-2-3")


def test_three_variables_split_more_than_one_way_are_refused() -> None:
    reg = ResourceRegistry()
    reg.register(_binding("v://{a}-{b}-{c}"))
    with pytest.raises(ValueError, match="Ambiguous resource URI 'v://1-2-3-4'"):
        reg.resolve("v://1-2-3-4")
