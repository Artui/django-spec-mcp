"""``subscriptions/listen`` over the real transport.

Asked here of kernel resources, mounted by ``kernel_urls.py``: ``gated``'s own
spec permission refuses the caller, so dropping it from the first frame is the
kernel read's gate answering the watch.
"""

from __future__ import annotations

import json
import types
from typing import Any

import pytest
from django.test import AsyncClient, override_settings
from django.urls import path

from django_spec_mcp import MCPServer, SubscriptionFilter
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from django_spec_mcp.types.subscription_id_meta_key import SUBSCRIPTION_ID_META_KEY
from tests.subscriptions.kernel_urls import SERVER, thing_spec

MODERN = "2026-07-28"
LEGACY = "2025-11-25"

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.urls("tests.subscriptions.kernel_urls"),
]


def _body(params: dict[str, Any], *, request_id: Any = 1) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "subscriptions/listen",
            "params": {
                **params,
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": MODERN,
                    "io.modelcontextprotocol/clientCapabilities": {},
                },
            },
        }
    )


async def _listen(client: AsyncClient, params: dict[str, Any], **kw: Any) -> Any:
    return await client.post(
        "/mcp/",
        data=_body(params, **kw),
        content_type="application/json",
        headers={"Mcp-Method": "subscriptions/listen", "Mcp-Protocol-Version": MODERN},
    )


async def _first_frame(response: Any) -> Any:
    async for chunk in response.streaming_content:
        return json.loads(chunk[len(b"data: ") :].strip())
    raise AssertionError("stream produced nothing")  # pragma: no cover


async def test_listening_opens_an_event_stream() -> None:
    response = await _listen(
        AsyncClient(), {"notifications": {"resourceSubscriptions": ["open://1"]}}
    )
    assert response.status_code == 200
    assert response["Content-Type"] == "text/event-stream"
    await response.streaming_content.aclose()


async def test_the_first_frame_acknowledges_what_was_granted() -> None:
    response = await _listen(
        AsyncClient(),
        {"notifications": {"resourceSubscriptions": ["open://1", "gated://1"]}},
        request_id="s1",
    )
    frame = await _first_frame(response)
    assert frame["method"] == "notifications/subscriptions/acknowledged"
    # The gated URI is dropped, and the client is told so on the first frame.
    assert frame["params"]["notifications"]["resourceSubscriptions"] == ["open://1"]
    assert frame["params"]["_meta"][SUBSCRIPTION_ID_META_KEY] == "s1"
    await response.streaming_content.aclose()


async def test_a_notification_published_elsewhere_arrives_on_the_stream() -> None:
    response = await _listen(
        AsyncClient(), {"notifications": {"resourceSubscriptions": ["open://1"]}}
    )
    stream = response.streaming_content
    await anext(stream)  # acknowledgement
    await SERVER.notify_resource_updated("open://1")
    frame = json.loads((await anext(stream))[len(b"data: ") :].strip())
    assert frame["method"] == "notifications/resources/updated"
    assert frame["params"]["uri"] == "open://1"
    await stream.aclose()


@pytest.mark.parametrize(
    "notifications",
    [
        # Strange, but exactly what was asked for: every type is opt-in.
        pytest.param({}, id="asking-for-nothing"),
        # The acknowledgement is the channel for "you will not get this", so a
        # junk filter opens an honest, empty subscription rather than erroring.
        pytest.param("nonsense", id="a-malformed-filter"),
    ],
)
async def test_an_empty_or_malformed_filter_is_acknowledged_and_silent(notifications: Any) -> None:
    response = await _listen(AsyncClient(), {"notifications": notifications})
    assert (await _first_frame(response))["params"]["notifications"] == {}
    await response.streaming_content.aclose()


async def test_a_legacy_client_gets_unknown_method_rather_than_a_stream() -> None:
    """``subscriptions/listen`` exists to *replace* the legacy GET stream, so a
    legacy caller has no way to interpret one.

    Answered ``-32601`` inside a ``200``, which is ordinary JSON-RPC — the
    ``404``-for-unknown-method rule belongs to the modern era, where it is what
    lets a client tell an MCP endpoint from a server that hosts none.
    """
    response = await AsyncClient().post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": LEGACY, "capabilities": {}},
            }
        ),
        content_type="application/json",
    )
    session = response.headers["Mcp-Session-Id"]
    listened = await AsyncClient().post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "subscriptions/listen",
                "params": {"notifications": {}},
            }
        ),
        content_type="application/json",
        headers={"Mcp-Session-Id": session, "Mcp-Protocol-Version": LEGACY},
    )
    assert listened.status_code == 200
    assert json.loads(listened.content)["error"]["code"] == -32601


async def test_a_server_with_no_broker_says_so_on_the_first_frame() -> None:
    """Rather than holding a stream open that can never carry anything."""
    server = MCPServer(
        name="brokerless", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    server.register_resource(thing_spec(), name="open", uri_template="open://{pk}")
    module = types.ModuleType("tests.subscriptions._brokerless_urls")
    module.urlpatterns = [path("mcp/", server.async_urls)]  # type: ignore[attr-defined]
    with override_settings(ROOT_URLCONF=module):
        response = await _listen(
            AsyncClient(), {"notifications": {"resourceSubscriptions": ["open://1"]}}
        )
        assert (await _first_frame(response))["params"]["notifications"] == {}
        await response.streaming_content.aclose()


async def test_an_ordinary_request_is_unaffected() -> None:
    """The subscription branch must not swallow anything else on the modern
    path."""
    response = await AsyncClient().post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "resources/list",
                "params": {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": MODERN,
                        "io.modelcontextprotocol/clientCapabilities": {},
                    }
                },
            }
        ),
        content_type="application/json",
        headers={"Mcp-Method": "resources/list", "Mcp-Protocol-Version": MODERN},
    )
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/json")


async def test_a_grant_of_nothing_closes_instead_of_holding_a_worker() -> None:
    """Otherwise the stream keeps an ASGI worker parked forever, emitting
    keepalives it can never follow with anything."""
    response = await _listen(
        AsyncClient(), {"notifications": {"resourceSubscriptions": ["gated://1"]}}
    )
    frames = [json.loads(c[len(b"data: ") :].strip()) async for c in response.streaming_content]
    assert len(frames) == 2
    assert frames[0]["method"] == "notifications/subscriptions/acknowledged"
    assert frames[0]["params"]["notifications"] == {}
    # The closing result, so the client knows it is over rather than idle.
    assert frames[1]["result"]["_meta"][SUBSCRIPTION_ID_META_KEY] == 1


async def test_the_brokerless_stream_ends_too() -> None:
    server = MCPServer(
        name="brokerless2", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    module = types.ModuleType("tests.subscriptions._brokerless2_urls")
    module.urlpatterns = [path("mcp/", server.async_urls)]  # type: ignore[attr-defined]
    with override_settings(ROOT_URLCONF=module):
        response = await _listen(AsyncClient(), {"notifications": {}})
        frames = [c async for c in response.streaming_content]
    assert len(frames) == 2


async def test_a_capped_server_refuses_a_further_subscription() -> None:
    """Each open subscription parks a worker, so past the cap a clear error
    beats a server that stops answering anything."""
    # ``REQUIRE_TOOL_PERMISSIONS`` is carried through because replacing the dict
    # drops the suite-wide opt-out, and the resource registered below is a
    # throwaway; the subject here is the subscription cap.
    with override_settings(
        SPEC_MCP={"MAX_CONCURRENT_SUBSCRIPTIONS": 0, "REQUIRE_TOOL_PERMISSIONS": False}
    ):
        import types as _t

        from django_spec_mcp.subscriptions.in_memory_subscription_broker import (
            InMemorySubscriptionBroker,
        )

        server = MCPServer(
            name="capped",
            auth_backend=AllowAnyBackend(),
            session_store=InMemorySessionStore(),
            subscription_broker=InMemorySubscriptionBroker(),
        )
        server.register_resource(thing_spec(), name="open", uri_template="open://{pk}")
        module = _t.ModuleType("tests.subscriptions._capped_urls")
        module.urlpatterns = [path("mcp/", server.async_urls)]  # type: ignore[attr-defined]
        with override_settings(ROOT_URLCONF=module):
            response = await _listen(
                AsyncClient(), {"notifications": {"resourceSubscriptions": ["open://1"]}}
            )
    assert response.status_code == 503
    assert json.loads(response.content)["error"]["code"] == -32603


async def test_a_capped_stream_closes_itself_and_forces_resubscription() -> None:
    """Permissions are checked once, at open. The cap is what stops a revoked
    principal receiving change signals indefinitely."""
    from django_spec_mcp.subscriptions.in_memory_subscription_broker import (
        InMemorySubscriptionBroker,
    )
    from django_spec_mcp.transport.subscription_stream import _stream

    frames = [
        f
        async for f in _stream(
            broker=InMemorySubscriptionBroker(),
            topics=frozenset({"t"}),
            granted=SubscriptionFilter(resource_uris=("t",)),
            request_id=5,
            max_seconds=0.0,
            keepalive=30.0,
        )
    ]
    assert len(frames) == 2
    assert json.loads(frames[1][len(b"data: ") :].strip())["id"] == 5


async def test_the_capability_advertises_what_the_broker_makes_real() -> None:
    """Without this the feature was undiscoverable — a client had to try
    ``subscriptions/listen`` and read the grant."""
    response = await AsyncClient().post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "server/discover",
                "params": {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": MODERN,
                        "io.modelcontextprotocol/clientCapabilities": {},
                    }
                },
            }
        ),
        content_type="application/json",
        headers={"Mcp-Method": "server/discover", "Mcp-Protocol-Version": MODERN},
    )
    caps = json.loads(response.content)["result"]["capabilities"]
    assert caps["resources"] == {"listChanged": True, "subscribe": True}


async def test_a_server_without_a_broker_advertises_no_subscription_support() -> None:
    from django_spec_mcp.handlers.handle_initialize import build_capabilities
    from tests.subscriptions.test_subscription_core import _server
    from tests.utils import call_context

    caps = build_capabilities(call_context(_server()), modern=True)
    assert caps.resources == {}
