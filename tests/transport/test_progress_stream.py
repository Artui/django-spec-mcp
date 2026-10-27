"""Progress over an SSE response stream, for kernel tools: the streaming half of the transport.

A client asks by putting a ``progressToken`` in the request's ``_meta``; the
server answers with ``text/event-stream`` carrying ``notifications/progress``
frames and then the response. Everything here drives the real async URL conf,
because the subject is what comes back over HTTP: content type, frame order,
which status was committed.

The tools are kernel specs registered with ``register_tool``, and a kernel
service asks for the reporter by declaring ``progress``, which ``adispatch``
fills. The drf-services original asked the same of ``register_service_tool``
and ``register_selector_tool``, and is kept beside this file as
``test_progress_stream_drfs.py``.

What the kernel adds is a second permission stack. A kernel binding's own
``permissions`` hold only its ``scopes=`` gate, and the spec's
permission checks run inside ``adispatch``, after a stream has committed
its ``200``. So the pre-flight runs the kernel's own prelude as well
(``test_a_spec_check_denying_a_streamed_call_keeps_its_403_and_challenge`` and
the cases after it). The drf-services original's pre-flight of a ``UrlKwarg``
route has no kernel counterpart, since a kernel spec declares its reads as
arguments; ``test_a_check_reading_arguments_is_judged_on_the_servers_value``
asks the kernel's form of that question.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.http import HttpRequest
from django.test import AsyncClient, override_settings
from django_service_specs import (
    DataclassValidator,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServerArguments,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.auth_backend import MCPAuthBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from django_spec_mcp.transport.progress_dispatch import preflight_permissions
from django_spec_mcp.transport.response_stream import (
    _stream,
    _StreamReporter,
    build_response_stream,
)
from tests.testapp.auth_backend import HeaderAuthBackend
from tests.testapp.urlconf_for import urlconf_for

MODERN = "2026-07-28"
LEGACY = "2025-11-25"
OPEN = [Unrestricted()]


class _Deny(PermissionCheck):
    def has_permission(self, *args: Any, **kwargs: Any) -> bool:
        return False


class _OnlyAcme(PermissionCheck):
    """Admits a call whose ``tenant`` argument is ``acme``, and no other."""

    def has_permission(self, principal: Any, spec: Any, *, arguments: Any) -> bool:
        return arguments.get("tenant") == "acme"


class _NeedsWriteScope(PermissionCheck):
    """Admits a token carrying ``mcp:write``, read off the call's request."""

    def has_permission(self, principal: Any, spec: Any, *, call_context: Any) -> bool:
        return "mcp:write" in call_context["request"].mcp_token.scopes


@dataclass
class _Tenanted:
    tenant: str


def _meta(token: Any = "abc123", *, modern: bool = True) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if token is not None:
        meta["progressToken"] = token
    if modern:
        meta["io.modelcontextprotocol/protocolVersion"] = MODERN
        meta["io.modelcontextprotocol/clientCapabilities"] = {}
    return meta


def _bare(*, auth_backend: MCPAuthBackend | None = None, **config: Any) -> MCPServer:
    return MCPServer(
        name="p",
        auth_backend=auth_backend or AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        config=build_mcp_config(**config) if config else None,
    )


def _server(*, auth_backend: MCPAuthBackend | None = None) -> MCPServer:
    server = _bare(auth_backend=auth_backend)

    def export(*, progress: Any) -> dict[str, Any]:
        progress(1, total=3, message="starting")
        progress(2, total=3, meta={"com.example/stage": "middle"})
        progress(3, total=3, message="done")
        return {"rows": 3}

    server.register_tool(ServiceSpec(service=export, permissions=OPEN), name="p.export")
    server.register_tool(
        SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: None, permissions=OPEN),
        name="p.gated",
        scopes="mcp:admin",
    )
    server.register_tool(ServiceSpec(service=lambda: {}, permissions=[_Deny()]), name="p.denied")
    server.register_tool(
        ServiceSpec(
            service=lambda *, tenant: {"tenant": tenant},
            validator=DataclassValidator(_Tenanted),
            permissions=[_OnlyAcme()],
        ),
        name="p.tenanted",
        server_arguments=ServerArguments(values={"tenant": "acme"}),
    )
    server.register_tool(
        ServiceSpec(service=lambda: {"wrote": True}, permissions=[_NeedsWriteScope()]),
        name="p.write",
    )
    server.register_tool(
        SelectorSpec(kind=SelectorKind.LIST, selector=lambda: [], permissions=[_Deny()]),
        name="p.pages",
        paginate=True,
    )
    return server


async def _post(
    server: MCPServer,
    *,
    name: str = "p.export",
    token: Any = "abc123",
    modern: bool = True,
    arguments: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    params: dict[str, Any] = {
        "name": name,
        "arguments": arguments or {},
        "_meta": _meta(token, modern=modern),
    }
    sent: dict[str, str] = {"Mcp-Protocol-Version": MODERN if modern else LEGACY}
    if modern:
        sent |= {"Mcp-Method": "tools/call", "Mcp-Name": name}
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=True)):
        return await AsyncClient().post(
            "/mcp/",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}),
            content_type="application/json",
            headers=sent | (headers or {}),
        )


async def _frames(response: Any) -> list[dict[str, Any]]:
    """Parse the ``data:`` lines of an SSE body into JSON-RPC messages."""
    body = b"".join([chunk async for chunk in response]).decode()
    return [
        json.loads(line[len("data: ") :]) for line in body.splitlines() if line.startswith("data: ")
    ]


async def _answer(response: Any) -> dict[str, Any]:
    """The JSON-RPC response a call was answered with, streamed or not."""
    if response.streaming:
        return (await _frames(response))[-1]
    return response.json()


# ----- the happy path -----


@pytest.mark.django_db(transaction=True)
async def test_a_progress_token_turns_the_response_into_a_stream() -> None:
    response = await _post(_server())
    assert response.status_code == 200
    assert response["Content-Type"] == "text/event-stream"
    # Without this nginx buffers the whole body and flushes on close, which
    # defeats the point of streaming at all.
    assert response["X-Accel-Buffering"] == "no"


@pytest.mark.django_db(transaction=True)
async def test_progress_frames_arrive_before_the_response() -> None:
    frames = await _frames(await _post(_server()))
    assert [f.get("method") for f in frames] == [
        "notifications/progress",
        "notifications/progress",
        "notifications/progress",
        None,
    ]
    assert frames[0]["params"] == {
        "progressToken": "abc123",
        "progress": 1,
        "total": 3,
        "message": "starting",
    }
    # The final frame is the ordinary JSON-RPC response, and it terminates the
    # stream.
    assert frames[-1]["id"] == 1
    assert frames[-1]["result"]["structuredContent"] == {"rows": 3}


@pytest.mark.django_db(transaction=True)
async def test_structured_detail_rides_in_the_notification_meta() -> None:
    """Where the protocol puts extension data, so `message` stays prose."""
    frames = await _frames(await _post(_server()))
    assert frames[1]["params"]["_meta"] == {"com.example/stage": "middle"}
    assert "message" not in frames[1]["params"]


@pytest.mark.django_db(transaction=True)
async def test_progress_streams_for_a_legacy_client_too() -> None:
    """``_meta.progressToken`` sits in the same place in both eras."""
    server = _server()
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=True)):
        client = AsyncClient()
        init = await client.post(
            "/mcp/",
            data=json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}}),
            content_type="application/json",
        )
        session = init.headers["Mcp-Session-Id"]
        response = await client.post(
            "/mcp/",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "p.export",
                        "arguments": {},
                        "_meta": {"progressToken": 7},
                    },
                }
            ),
            content_type="application/json",
            headers={"Mcp-Protocol-Version": LEGACY, "Mcp-Session-Id": session},
        )
        assert response["Content-Type"] == "text/event-stream"
        frames = await _frames(response)
    assert frames[0]["params"]["progressToken"] == 7


# ----- when not to stream -----


@pytest.mark.django_db(transaction=True)
async def test_no_token_means_no_stream() -> None:
    """A stream whose only event is the final response buys nothing."""
    response = await _post(_server(), token=None)
    assert response["Content-Type"].startswith("application/json")
    assert response.json()["result"]["structuredContent"] == {"rows": 3}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("token", [True, 1.5, {"a": 1}, None])
async def test_a_token_that_is_not_a_string_or_int_is_treated_as_absent(token: Any) -> None:
    """Declining to send progress is always legal; failing the call is not."""
    response = await _post(_server(), token=token)
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/json")


# ----- the status streaming would otherwise cost -----


@pytest.mark.django_db(transaction=True)
async def test_a_denied_tool_still_gets_403_and_a_challenge() -> None:
    """The reason permissions are pre-flighted, for the ``scopes=`` gate.

    A streaming response commits its status before the handler runs, so a
    denial found inside would ride as an in-stream error inside a ``200``,
    losing the ``403`` the authorization spec makes normative and the
    challenge telling the client what to ask for.
    """
    response = await _post(_server(), name="p.gated")
    assert response.status_code == 403
    assert response["Content-Type"].startswith("application/json")
    assert "mcp:admin" in response["WWW-Authenticate"]


async def _both(server: MCPServer, **post: Any) -> tuple[Any, Any]:
    """One call answered with a progress token and without, in that order."""
    return await _post(server, **post), await _post(server, token=None, **post)


def _wire(response: Any, body: dict[str, Any]) -> tuple[Any, ...]:
    return (
        response.status_code,
        response["Content-Type"],
        response.get("WWW-Authenticate"),
        body,
    )


@pytest.mark.django_db(transaction=True)
async def test_a_spec_check_denying_a_streamed_call_keeps_its_403_and_challenge() -> None:
    """The spec's own ``PermissionCheck`` is pre-flighted, not only the scope gate.

    It used to run only inside ``adispatch``, so asking for progress turned a
    ``403`` with a challenge into a ``200`` stream carrying ``-32006``: the
    status a client is told to act on depended on whether it wanted a
    progress bar.
    """
    streamed, plain = await _both(_server(), name="p.denied")
    assert streamed.status_code == 403
    assert _wire(streamed, streamed.json()) == _wire(plain, plain.json())
    assert streamed.json()["error"] == {"code": -32006, "message": "Insufficient permission"}


@pytest.mark.django_db(transaction=True)
async def test_a_deactivated_principal_is_refused_before_the_stream() -> None:
    """The kernel refuses an inactive user ahead of any check, as ``-32006``,
    so the pre-flight does too, even for a spec every caller may run."""
    await get_user_model().objects.acreate(username="gone", is_active=False)
    server = _server(auth_backend=HeaderAuthBackend())
    streamed, plain = await _both(server, headers={"X-Test-User": "gone"})
    assert streamed.status_code == 403
    assert _wire(streamed, streamed.json()) == _wire(plain, plain.json())


@pytest.mark.django_db(transaction=True)
async def test_a_check_reading_arguments_is_judged_on_the_servers_value() -> None:
    """The pre-flight runs before the handler lays the server's arguments over
    the caller's, so it lays them itself: a check reading ``tenant`` judged the
    caller's ``evil`` there, and the server's ``acme`` in dispatch."""
    response = await _post(_server(), name="p.tenanted", arguments={"tenant": "evil"})
    assert response.status_code == 200
    assert response["Content-Type"] == "text/event-stream"
    assert (await _frames(response))[-1]["result"]["structuredContent"] == {"tenant": "acme"}


@pytest.mark.django_db(transaction=True)
async def test_the_server_arguments_are_laid_without_a_second_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The handler warns once that it dropped the caller's ``tenant``; the
    pre-flight lays the same values without saying so again."""
    caplog.set_level(logging.WARNING, logger="django_spec_mcp")
    await _frames(await _post(_server(), name="p.tenanted", arguments={"tenant": "evil"}))
    dropped = [r for r in caplog.records if "dropped the caller's value" in r.getMessage()]
    assert len(dropped) == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("paging", [{"page": 2}, {"limit": 5}])
async def test_a_paged_call_its_spec_denies_keeps_its_403(paging: dict[str, int]) -> None:
    """``page`` and ``limit`` are the transport's, split off before dispatch, so
    the pre-flight splits them too: left in, the kernel refused them as unknown
    arguments ahead of the permission check, the pre-flight stood aside, and
    the denial streamed inside a ``200``."""
    streamed, plain = await _both(_server(), name="p.pages", arguments=paging)
    assert streamed.status_code == 403
    assert _wire(streamed, streamed.json()) == _wire(plain, plain.json())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(("scopes", "streamed"), [("mcp:write", True), ("", False)])
async def test_a_check_reading_the_token_sees_the_calls_request(
    scopes: str, streamed: bool
) -> None:
    """A check depending on the token reads ``call_context["request"].mcp_token``,
    as the plan's call context promises, in the pre-flight as in dispatch."""
    server = _server(auth_backend=HeaderAuthBackend())
    response = await _post(server, name="p.write", headers={"X-Test-Scopes": scopes})
    assert response.streaming is streamed
    assert response.status_code == (200 if streamed else 403)


@pytest.mark.django_db(transaction=True)
async def test_a_refusal_that_is_not_a_denial_is_left_to_the_handler() -> None:
    """An unknown argument is an ``isError`` result on both paths, never a ``403``.

    The pre-flight answers only the refusals ``KERNEL_REFUSALS`` answers
    ``-32006``. The kernel checks the arguments before the permissions, so
    even a caller ``_Deny`` would refuse is told about the argument first, as
    dispatch tells it.
    """
    streamed, plain = await _both(_server(), name="p.denied", arguments={"stray": 1})
    assert streamed.status_code == 200
    assert streamed["Content-Type"] == "text/event-stream"
    answer = await _answer(streamed)
    assert answer["result"]["isError"] is True
    assert answer == plain.json()


@pytest.mark.django_db(transaction=True)
async def test_an_unguarded_spec_streams_under_the_grant_its_call_carries() -> None:
    """With ``REQUIRE_TOOL_PERMISSIONS`` off, the pre-flight hands the kernel
    the same ``Grant`` the call does; without it the kernel refuses the
    unguarded spec as a configuration error, and the call is a ``500``."""
    server = _bare(require_tool_permissions=False)
    with pytest.warns(UserWarning):
        server.register_tool(ServiceSpec(service=lambda: {"ok": True}), name="p.open")
    response = await _post(server, name="p.open")
    assert response["Content-Type"] == "text/event-stream"
    assert (await _frames(response))[-1]["result"]["structuredContent"] == {"ok": True}


# ----- bounds -----


@pytest.mark.django_db(transaction=True)
async def test_notifications_are_capped() -> None:
    """The spec asks both parties to rate-limit; a per-row reporter floods."""
    server = _bare(max_progress_notifications=2)

    def chatty(*, progress: Any) -> dict[str, Any]:
        for i in range(1, 50):
            progress(i, total=50)
        return {"ok": True}

    server.register_tool(ServiceSpec(service=chatty, permissions=OPEN), name="p.export")
    frames = await _frames(await _post(server))
    assert sum(1 for f in frames if f.get("method") == "notifications/progress") == 2
    # Capping the *reports* must not cap the work: the result still arrives.
    assert frames[-1]["result"]["structuredContent"] == {"ok": True}


@pytest.mark.django_db(transaction=True)
async def test_a_non_increasing_report_is_dropped() -> None:
    """The spec makes increase a MUST, so forwarding one would violate it here."""
    server = _bare()

    def backwards(*, progress: Any) -> dict[str, Any]:
        progress(5, total=10)
        progress(3, total=10)  # dropped
        progress(5, total=10)  # dropped: equal is not an increase
        progress(9, total=10)
        return {"ok": True}

    server.register_tool(ServiceSpec(service=backwards, permissions=OPEN), name="p.export")
    frames = await _frames(await _post(server))
    reported = [f["params"]["progress"] for f in frames if f.get("method")]
    assert reported == [5, 9]


# ----- failure inside a committed stream -----


@pytest.mark.django_db(transaction=True)
async def test_an_exception_becomes_an_in_stream_error_not_a_truncated_body() -> None:
    """There is no status left to change once the stream is open."""
    server = _bare()

    def boom(*, progress: Any) -> dict[str, Any]:
        progress(1, total=2)
        raise RuntimeError("nope")

    server.register_tool(ServiceSpec(service=boom, permissions=OPEN), name="p.export")
    frames = await _frames(await _post(server))
    assert frames[0]["method"] == "notifications/progress"
    assert frames[-1]["error"]["code"] == -32603
    # Generic, with the exception's own text in the log only: see
    # ``test_dispatch_backstop.py`` for the log half.
    assert frames[-1]["error"]["message"] == "Internal error"
    assert "nope" not in json.dumps(frames)


# ----- the stream's own mechanics -----


async def _drain(response: Any) -> bytes:
    return b"".join([chunk async for chunk in response])


async def test_a_quiet_dispatch_still_emits_keepalives() -> None:
    """Something has to hold the connection open past an idle proxy timeout."""

    async def slow(reporter: Any) -> dict[str, Any]:
        await asyncio.sleep(0.05)
        reporter(1, total=1)
        return {"done": True}

    response = build_response_stream(
        dispatch=slow, request_id=1, progress_token="t", max_notifications=10, keepalive=0.01
    )
    body = (await _drain(response)).decode()
    assert ": keepalive" in body
    assert '"done":true' in body.replace(" ", "")


async def test_a_report_with_no_total_omits_the_field() -> None:
    """`total` is omitted rather than guessed: a wrong one renders a wrong bar."""

    async def one(reporter: Any) -> dict[str, Any]:
        reporter(1)
        return {}

    response = build_response_stream(
        dispatch=one, request_id=1, progress_token="t", max_notifications=10, keepalive=5
    )
    frames = await _frames(response)
    assert frames[0]["params"] == {"progressToken": "t", "progress": 1}


async def test_closing_the_stream_cancels_the_dispatch() -> None:
    """Cancellation-by-disconnect: the client going away *is* the signal.

    It cancels the await, not the work: a thread parked in a driver's socket
    read is not interruptible by asyncio, the same caveat the dispatch
    deadline carries.
    """
    seen: list[str] = []

    async def never(reporter: Any) -> dict[str, Any]:
        reporter(1, total=100)
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            seen.append("cancelled")
            raise
        return {}  # pragma: no cover - the sleep never completes

    # Driving the generator rather than the response: Django wraps the
    # iterator, and closing the wrapper does not reach through to the
    # generator's ``finally``, which is the whole subject here.
    iterator = _stream(
        dispatch=never, request_id=1, progress_token="t", max_notifications=10, keepalive=5
    )
    assert b"progress" in await iterator.__anext__()
    # The client hangs up: Django closes the generator, whose ``finally``
    # cancels the dispatch.
    await iterator.aclose()
    await asyncio.sleep(0.05)
    assert seen == ["cancelled"]


async def test_a_report_after_the_client_left_is_dropped_not_raised() -> None:
    """A reporter must not raise into domain code that cannot defend itself."""
    closed = asyncio.new_event_loop()
    closed.close()
    reporter = _StreamReporter(loop=closed, queue=asyncio.Queue(), token="t", remaining=5)
    assert reporter(1, total=2) is None


# ----- the permission pre-flight only speaks for tool calls -----


def test_preflight_stands_aside_for_everything_it_cannot_speak_for() -> None:
    """An unknown tool, or a bad name, is the handler's error to report, with
    a better message than a pre-flight could give."""
    server = _server()
    context = MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version=MODERN,
        config=server.config,
    )
    assert preflight_permissions("resources/read", {"uri": "x://y"}, context) is None
    assert preflight_permissions("tools/call", "not-a-dict", context) is None
    assert preflight_permissions("tools/call", {"name": 7}, context) is None
    assert preflight_permissions("tools/call", {"name": "nope"}, context) is None
