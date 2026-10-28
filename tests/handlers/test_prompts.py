"""``prompts/list`` and ``prompts/get``, with nothing of drf-services in the subject.

A prompt is not a spec: ``register_prompt`` takes a ``render`` callable and
``permissions`` that judge the transport's request and token, so the kernel
cut changes nothing it asks. This file is the original with one question
left out: a ``DRFPermissionAdapter`` wrapping a DRF permission class reading the
prompt's arguments off ``request.data`` and its name off ``view.action``. A
DRF class has no kernel counterpart, and a prompt's permission on the kernel
path is an ``MCPPermission`` (or the scope gate), which is handed the request
and the token and never the arguments; the last test here holds that. The
original is kept beside this file as ``test_prompts_drfs.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest, QueryDict

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_prompts_get import handle_prompts_get
from django_spec_mcp.handlers.handle_prompts_get_async import handle_prompts_get_async
from django_spec_mcp.handlers.handle_prompts_list import handle_prompts_list
from django_spec_mcp.handlers.render_prompt_messages import normalize_render_result
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.protocol.types.prompt_argument import PromptArgument
from django_spec_mcp.protocol.types.prompt_message import PromptMessage
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


# ---------- normalize_render_result ----------


def test_normalize_string_becomes_user_text_message() -> None:
    out = normalize_render_result("hello")
    assert out == [PromptMessage.text(role="user", text="hello")]


def test_normalize_promptmessage_passthrough() -> None:
    msg = PromptMessage.text(role="assistant", text="hi")
    assert normalize_render_result(msg) == [msg]


def test_normalize_list_of_strings() -> None:
    out = normalize_render_result(["a", "b"])
    assert [m.content["text"] for m in out] == ["a", "b"]
    assert all(m.role == "user" for m in out)


def test_normalize_list_of_dicts() -> None:
    out = normalize_render_result([{"role": "system", "content": {"type": "text", "text": "sys"}}])
    assert out[0].role == "system"


def test_normalize_list_of_messages() -> None:
    msg = PromptMessage.text(role="user", text="x")
    assert normalize_render_result([msg]) == [msg]


def test_normalize_unsupported_item_raises() -> None:
    with pytest.raises(TypeError, match="Unsupported"):
        normalize_render_result([123])


def test_normalize_unsupported_root_raises() -> None:
    with pytest.raises(TypeError, match="unsupported value"):
        normalize_render_result(123)


# ---------- prompts/list ----------


def test_prompts_list_advertises_arguments() -> None:
    server = _server()
    server.register_prompt(
        name="greet",
        description="Say hi",
        render=lambda **_: "hi",
        arguments=[PromptArgument(name="who", required=True)],
    )
    out = handle_prompts_list(None, _ctx(server))
    assert isinstance(out, dict)
    prompt = out["prompts"][0]
    assert prompt["name"] == "greet"
    assert prompt["arguments"][0]["required"] is True


def test_prompts_list_paginates(settings) -> None:
    # Replacing the dict drops the suite-wide opt-out in conftest_settings, and
    # this test's throwaway prompts are unguarded; carried through so the
    # subject stays pagination.
    settings.SPEC_MCP = {"PAGE_SIZE": 2, "REQUIRE_TOOL_PERMISSIONS": False}
    server = _server()
    for i in range(3):
        server.register_prompt(name=f"p{i}", render=lambda **_: "x")
    out = handle_prompts_list(None, _ctx(server))
    assert isinstance(out, dict)
    assert len(out["prompts"]) == 2
    assert "nextCursor" in out


def test_prompts_list_rejects_bad_cursor() -> None:
    server = _server()
    out = handle_prompts_list({"cursor": "###"}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


def test_prompts_list_rejects_non_string_cursor() -> None:
    server = _server()
    out = handle_prompts_list({"cursor": 1}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


# ---------- prompts/get sync ----------


def test_prompts_get_renders_string() -> None:
    server = _server()
    server.register_prompt(name="p", render=lambda **_: "rendered")
    out = handle_prompts_get({"name": "p", "arguments": {}}, _ctx(server))
    assert isinstance(out, dict)
    assert out["messages"][0]["content"]["text"] == "rendered"


def test_prompts_get_threads_arguments_to_render() -> None:
    server = _server()
    server.register_prompt(
        name="echo",
        render=lambda *, who: f"hello {who}",
        arguments=[PromptArgument(name="who", required=True)],
    )
    out = handle_prompts_get({"name": "echo", "arguments": {"who": "world"}}, _ctx(server))
    assert isinstance(out, dict)
    assert "hello world" in out["messages"][0]["content"]["text"]


def test_prompts_get_missing_required_arg() -> None:
    server = _server()
    server.register_prompt(
        name="p",
        render=lambda *, who: who,
        arguments=[PromptArgument(name="who", required=True)],
    )
    out = handle_prompts_get({"name": "p", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602
    assert out.data == {"missing": ["who"]}


def test_prompts_get_unknown_returns_invalid_params() -> None:
    server = _server()
    out = handle_prompts_get({"name": "nope", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


def test_prompts_get_rejects_non_dict_params() -> None:
    server = _server()
    out = handle_prompts_get(None, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


def test_prompts_get_rejects_missing_name() -> None:
    server = _server()
    out = handle_prompts_get({}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


def test_prompts_get_rejects_non_dict_arguments() -> None:
    server = _server()
    server.register_prompt(name="p", render=lambda **_: "x")
    out = handle_prompts_get({"name": "p", "arguments": [1]}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


def test_prompts_get_handles_unsupported_render_value() -> None:
    server = _server()
    server.register_prompt(name="bad", render=lambda **_: 123)
    out = handle_prompts_get({"name": "bad", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32603


class _DenyAll:
    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:
        return False

    def required_scopes(self) -> list[str]:
        return ["s"]


def test_prompts_get_permission_denied() -> None:
    server = _server()
    server.register_prompt(name="p", render=lambda **_: "x", permissions=[_DenyAll()])
    out = handle_prompts_get({"name": "p", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32006


def test_decorator_uses_function_name_and_docstring() -> None:
    server = _server()

    @server.prompt()
    def greeting(*, name: str) -> str:
        """Say hello."""
        return f"Hello, {name}!"

    binding = server.prompts.get("greeting")
    assert binding is not None
    assert binding.description == "Say hello."


# ---------- prompts/get async ----------


async def test_async_prompts_get_dispatches_async_render() -> None:
    server = _server()

    async def render(**_: Any) -> str:
        return "async-rendered"

    server.register_prompt(name="p", render=render)
    out = await handle_prompts_get_async({"name": "p", "arguments": {}}, _ctx(server))
    assert isinstance(out, dict)
    assert out["messages"][0]["content"]["text"] == "async-rendered"


async def test_async_prompts_get_unknown_returns_invalid_params() -> None:
    server = _server()
    out = await handle_prompts_get_async({"name": "nope", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


async def test_async_prompts_get_rejects_non_dict_params() -> None:
    server = _server()
    out = await handle_prompts_get_async(None, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


async def test_async_prompts_get_rejects_missing_name() -> None:
    server = _server()
    out = await handle_prompts_get_async({}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


async def test_async_prompts_get_rejects_non_dict_arguments() -> None:
    server = _server()
    server.register_prompt(name="p", render=lambda **_: "x")
    out = await handle_prompts_get_async({"name": "p", "arguments": [1]}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


async def test_async_prompts_get_missing_required_arg() -> None:
    server = _server()
    server.register_prompt(
        name="p",
        render=lambda *, who: who,
        arguments=[PromptArgument(name="who", required=True)],
    )
    out = await handle_prompts_get_async({"name": "p", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32602


async def test_async_prompts_get_permission_denied() -> None:
    server = _server()
    server.register_prompt(name="p", render=lambda **_: "x", permissions=[_DenyAll()])
    out = await handle_prompts_get_async({"name": "p", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32006


async def test_async_prompts_get_handles_unsupported_render_value() -> None:
    server = _server()
    server.register_prompt(name="bad", render=lambda **_: 123)
    out = await handle_prompts_get_async({"name": "bad", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError) and out.code == -32603


# ---------- Prompt + PromptArgument shapes ----------


def test_prompt_argument_minimal_to_dict() -> None:
    assert PromptArgument(name="x").to_dict() == {"name": "x"}


def test_prompt_argument_full_to_dict() -> None:
    out = PromptArgument(name="x", description="d", required=True).to_dict()
    assert out == {"name": "x", "description": "d", "required": True}


def test_prompt_minimal_to_dict() -> None:
    from django_spec_mcp.protocol.types.prompt import Prompt

    assert Prompt(name="p").to_dict() == {"name": "p"}


def test_prompt_full_to_dict() -> None:
    from django_spec_mcp.protocol.types.prompt import Prompt

    p = Prompt(
        name="p",
        title="T",
        description="d",
        arguments=[PromptArgument(name="x", required=True)],
        annotations={"k": 1},
    )
    out = p.to_dict()
    assert out["title"] == "T"
    assert out["description"] == "d"
    assert out["arguments"] == [{"name": "x", "required": True}]
    assert out["annotations"] == {"k": 1}


def test_get_prompt_result_to_dict_minimal() -> None:
    from django_spec_mcp.protocol.types.get_prompt_result import GetPromptResult

    out = GetPromptResult(messages=[PromptMessage.text("user", "hi")]).to_dict()
    assert "description" not in out
    assert out["messages"][0]["role"] == "user"


def test_get_prompt_result_to_dict_with_description() -> None:
    from django_spec_mcp.protocol.types.get_prompt_result import GetPromptResult

    out = GetPromptResult(messages=[], description="d").to_dict()
    assert out["description"] == "d"


def test_prompt_message_text_helper() -> None:
    msg = PromptMessage.text(role="assistant", text="hi")
    assert msg.role == "assistant"
    assert msg.content == {"type": "text", "text": "hi"}


class _Recording:
    """An ``MCPPermission`` refusing every call, recording what each check was handed."""

    def __init__(self) -> None:
        self.seen: list[tuple[Any, Any]] = []

    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:
        self.seen.append((request, token))
        return False


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
async def test_a_prompt_permission_is_judged_on_the_request_and_token_alone(
    is_async: bool,
) -> None:
    # The whole of what a non-DRF prompt permission sees: the transport's own
    # request and the resolved token, once per get, and nothing of the
    # arguments, which reach ``render`` and no check.
    permission = _Recording()
    server = _server()
    server.register_prompt(name="p", render=lambda **_: "hello", permissions=[permission])
    context = _ctx(server)
    params: dict[str, Any] = {"name": "p", "arguments": {"deny": "1"}}

    if is_async:
        out = await handle_prompts_get_async(params, context)
    else:
        out = await sync_to_async(handle_prompts_get)(params, context)

    assert isinstance(out, JsonRpcError) and out.code == JsonRpcErrorCode.FORBIDDEN
    assert out.data is None
    assert permission.seen == [(context.http_request, context.token)]


@pytest.mark.parametrize("is_async", [False, True])
async def test_a_render_declaring_request_receives_the_transports_http_request(
    is_async: bool,
) -> None:
    # The plain ``HttpRequest`` the kernel tool path hands a spec, with the
    # resolved token on it as ``mcp_token``; no DRF ``Request`` is built. Its
    # ``user`` is the token's principal, as the ``user`` seed is, and never the
    # one the endpoint's session resolved.
    seen: dict[str, Any] = {}

    def render(request: Any, user: Any) -> str:
        seen.update(request=request, user=user)
        return "hello"

    server = _server()
    server.register_prompt(name="p", render=render)
    http_request = HttpRequest()
    session_user = object()
    http_request.user = session_user
    context = MCPCallContext(
        http_request=http_request,
        token=TokenInfo(user=object()),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )

    params: dict[str, Any] = {"name": "p"}
    if is_async:
        out = await handle_prompts_get_async(params, context)
    else:
        out = await sync_to_async(handle_prompts_get)(params, context)

    assert not isinstance(out, JsonRpcError), out
    assert seen["request"] is http_request
    assert seen["request"].mcp_token is context.token
    assert seen["user"] is context.token.user
    assert seen["request"].user is context.token.user


def test_render_reads_the_endpoints_query_string_and_no_drf_attributes() -> None:
    """``request.GET`` is the MCP endpoint's own query string, and the request
    is a Django one: no ``data`` or ``query_params`` to read arguments from."""
    seen: dict[str, Any] = {}
    server = _server()

    def render(*, request: Any) -> str:
        seen.update(
            get=dict(request.GET.items()),
            drf=[name for name in ("data", "query_params") if hasattr(request, name)],
        )
        return "ok"

    server.register_prompt(name="p", description="d", render=render)
    context = _ctx(server)
    context.http_request.GET = QueryDict("on=endpoint")

    handle_prompts_get({"name": "p", "arguments": {}}, context)

    assert seen == {"get": {"on": "endpoint"}, "drf": []}


async def test_user_and_request_user_agree_for_a_token_naming_no_one() -> None:
    """A token naming no one seeds ``user`` as the anonymous principal
    ``request.user`` is, on both dispatchers, so declaring ``user`` and reading
    ``request.user`` answer alike."""
    seen: list[tuple[Any, Any]] = []
    server = _server()

    def render(*, request: Any, user: Any) -> str:
        seen.append((user, request.user))
        return "ok"

    server.register_prompt(name="p", description="d", render=render)

    handle_prompts_get({"name": "p", "arguments": {}}, _ctx(server))
    await handle_prompts_get_async({"name": "p", "arguments": {}}, _ctx(server))

    assert len(seen) == 2
    for user, request_user in seen:
        assert isinstance(user, AnonymousUser)
        assert user is request_user
