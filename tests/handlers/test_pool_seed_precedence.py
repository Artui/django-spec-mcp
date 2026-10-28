"""Client-supplied names never outrank the transport's own seeds, on a kernel resource too.

Asks what the original, kept beside this file as
``test_pool_seed_precedence_drfs.py``, asks. Every method that lets a caller
route a value into a pool, a prompt argument, a URI-template variable or a
completion sibling, has to keep the dispatcher's own names out of the caller's
reach, or an argument called ``user`` binds to the parameter the dispatcher owns
and the callable runs under a principal the caller named.

The prompt and completion cases name no spec, and are asked here as the
original asks them so they outlive it. The resource cases are asked of a kernel
``RETRIEVE`` spec. ``register_resource`` refuses a template variable named after
a reserved seed, and the kernel refuses a spec declaring a read of that name, so
the runtime lock is reached only by a binding built by hand: there the kernel's
argument check refuses ``user`` as an argument the spec does not declare, and
the selector never runs under the name the URI carried: the read is answered
``-32602`` with the kernel's detail, as any admitted read of arguments the check
refuses is. That is the only outcome on this path: a resource read dispatches
under the kernel's default ``REJECT``, and ``register_resource`` takes no
``unknown_arguments`` knob, so the ``IGNORE`` case the tool path asks (in
``test_handler_branches.py``) has no resource counterpart.
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django_service_specs import (
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    UnknownArguments,
    Unrestricted,
)

from django_spec_mcp import MCPServer, PromptArgument
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_completion_complete import handle_completion_complete
from django_spec_mcp.handlers.handle_prompts_get import handle_prompts_get
from django_spec_mcp.handlers.handle_prompts_get_async import handle_prompts_get_async
from django_spec_mcp.handlers.handle_resources_read import handle_resources_read
from django_spec_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.protocol.types.json_rpc_error import JsonRpcError
from django_spec_mcp.registry.resource_registry import ResourceRegistry
from django_spec_mcp.registry.types.kernel_resource_binding import KernelResourceBinding
from django_spec_mcp.types.json_rpc_error_code import JsonRpcErrorCode

_CALLER = "alice"


class _AlwaysAllow:
    """Minimal MCP permission: a prompt registration must declare one."""

    def has_permission(self, *_args: object, **_kwargs: object) -> bool:
        return True


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _ctx(server: MCPServer, *, resources: ResourceRegistry | None = None) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=_CALLER),
        tools=server.tools,
        resources=resources if resources is not None else server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


# ---------- prompts/get ----------


def _server_with_scoped_prompt() -> MCPServer:
    server = _server()
    server.register_prompt(
        name="scoped",
        render=lambda *, user, topic: f"{user}:{topic}",
        arguments=[PromptArgument(name="topic", required=True)],
        permissions=[_AlwaysAllow()],
    )
    return server


def _rendered(out: Any) -> str:
    assert isinstance(out, dict), out
    return out["messages"][0]["content"]["text"]


def test_prompt_argument_named_user_cannot_replace_the_caller() -> None:
    """The documented ``def render(user, topic)`` shape: ``user`` is the
    authenticated principal, never an argument the client chose."""
    server = _server_with_scoped_prompt()

    out = handle_prompts_get(
        {"name": "scoped", "arguments": {"topic": "x", "user": "victim"}}, _ctx(server)
    )

    assert _rendered(out) == f"{_CALLER}:x"


def test_prompt_argument_named_request_cannot_replace_the_request() -> None:
    """The same key works for ``request``: an argument of that name does not
    replace the transport's request, whose ``user`` is the caller, so a
    ``render`` reading ``request.user`` is not fooled by it."""
    seen: dict[str, Any] = {}

    server = _server()

    def render(*, request: Any, user: Any, topic: str) -> str:
        seen.update(request=request, user=user)
        return topic

    server.register_prompt(
        name="scoped",
        render=render,
        arguments=[PromptArgument(name="topic", required=True)],
        permissions=[_AlwaysAllow()],
    )
    context = _ctx(server)

    handle_prompts_get(
        {"name": "scoped", "arguments": {"topic": "x", "request": "spoofed"}}, context
    )

    assert seen["request"] is context.http_request
    assert seen["request"].user == _CALLER
    assert seen["user"] == _CALLER


async def test_async_prompt_argument_named_user_cannot_replace_the_caller() -> None:
    server = _server_with_scoped_prompt()

    out = await handle_prompts_get_async(
        {"name": "scoped", "arguments": {"topic": "x", "user": "victim"}}, _ctx(server)
    )

    assert _rendered(out) == f"{_CALLER}:x"


def test_ordinary_prompt_arguments_still_reach_render() -> None:
    """The precedence must not cost a prompt its own arguments."""
    server = _server_with_scoped_prompt()

    out = handle_prompts_get({"name": "scoped", "arguments": {"topic": "weekly"}}, _ctx(server))

    assert _rendered(out) == f"{_CALLER}:weekly"


# ---------- resources/read ----------

ran: list[Any] = []


def _notes(*, user: Any, pk: str) -> dict[str, Any]:  # pragma: no cover - never reached
    ran.append(user)
    return {"scoped_to": str(user), "pk": pk}


def _notes_spec() -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_notes,
        reads=Parameters([Parameter("pk", "string", required=True)]),
        permissions=[Unrestricted()],
    )


def test_register_resource_refuses_a_template_variable_named_user() -> None:
    with pytest.raises(ImproperlyConfigured, match="reserved transport keys"):
        _server().register_resource(_notes_spec(), name="notes", uri_template="notes://{user}/{pk}")


def test_a_kernel_spec_declaring_a_read_named_user_is_refused() -> None:
    with pytest.raises(ImproperlyConfigured, match="dispatch seeds itself"):
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_notes,
            reads=Parameters([Parameter("user", "string", required=True)]),
            permissions=[Unrestricted()],
        )


def test_a_resource_takes_no_unknown_argument_policy() -> None:
    """Holds the module's claim that a resource read is always ``REJECT``."""
    with pytest.raises(TypeError, match="unknown_arguments"):
        _server().register_resource(
            _notes_spec(),
            name="notes",
            uri_template="notes://{pk}",
            unknown_arguments=UnknownArguments.IGNORE,
        )


def _handbuilt_notes() -> ResourceRegistry:
    """The binding ``register_resource`` refuses, built by hand to reach the second lock."""
    spec = _notes_spec()
    resources = ResourceRegistry()
    resources.register(
        KernelResourceBinding(
            name="notes",
            uri_template="notes://{user}/{pk}",
            description=None,
            selector=spec.selector,
            spec=spec,
        )
    )
    return resources


@pytest.mark.parametrize(
    "read",
    [handle_resources_read, async_to_sync(handle_resources_read_async)],
    ids=["sync", "async"],
)
def test_a_uri_template_variable_named_user_cannot_replace_the_caller(read: Any) -> None:
    """The kernel's argument check refuses ``user`` as undeclared, and the read is
    answered ``-32602`` naming it, so the selector never runs under the name the
    URI carried."""
    # Changed by design: the original ran the selector with the caller's own
    # ``user`` and the URI's value dropped. The kernel declares no such read, so
    # the argument check refuses the hand-built binding outright: the outcome
    # that matters, that the selector never runs under the URI's name, holds
    # either way, and an error is the louder way to hold it.
    ran.clear()
    refused = read({"uri": "notes://victim/1"}, _ctx(_server(), resources=_handbuilt_notes()))
    assert isinstance(refused, JsonRpcError), refused
    assert refused.code == JsonRpcErrorCode.INVALID_PARAMS
    assert refused.data["detail"] == {"user": ["Unknown argument."]}
    assert ran == []


def test_ordinary_uri_template_variables_still_reach_the_selector() -> None:
    server = _server()
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, user, owner, pk: {"scoped_to": user, "owner": owner, "pk": pk},
            reads=Parameters(
                [
                    Parameter("owner", "string", required=True),
                    Parameter("pk", "string", required=True),
                ]
            ),
            permissions=[Unrestricted()],
        ),
        name="notes",
        uri_template="notes://{owner}/{pk}",
    )
    out = handle_resources_read({"uri": "notes://bob/42"}, _ctx(server))
    text = out["contents"][0]["text"]
    assert '"owner": "bob"' in text
    assert '"pk": "42"' in text
    assert f'"scoped_to": "{_CALLER}"' in text


# ---------- completion/complete ----------


def _server_with_completer(completer: Any) -> MCPServer:
    server = _server()
    server.register_prompt(
        name="p",
        render=lambda **_: "x",
        arguments=[PromptArgument(name="id"), PromptArgument(name="other")],
        completions={"id": completer},
        permissions=[_AlwaysAllow()],
    )
    return server


def _complete(server: MCPServer, siblings: dict[str, Any]) -> Any:
    return handle_completion_complete(
        {
            "ref": {"type": "ref/prompt", "name": "p"},
            "argument": {"name": "id", "value": ""},
            "context": {"arguments": siblings},
        },
        _ctx(server),
    )


def test_a_sibling_named_instance_does_not_reach_the_completer() -> None:
    """``instance`` is one of the three reserved seeds this handler never
    supplies, so re-seeding a hand-picked few left it reachable."""
    seen: dict[str, Any] = {}

    def completer(*, value: str, **kwargs: Any) -> list[str]:
        seen.update(kwargs)
        return ["a"]

    out = _complete(_server_with_completer(completer), {"instance": {"attacker": True}})

    assert isinstance(out, dict)
    assert "instance" not in seen


def test_a_sibling_named_data_does_not_reach_the_completer() -> None:
    seen: dict[str, Any] = {}

    def completer(*, value: str, data: Any = "seed-untouched") -> list[str]:
        seen["data"] = data
        return ["a"]

    _complete(_server_with_completer(completer), {"data": "attacker"})

    assert seen["data"] == "seed-untouched"


@pytest.mark.parametrize("name", ["result", "queryset"])
def test_a_sibling_named_as_a_kernel_seed_does_not_reach_the_completer(name: str) -> None:
    """``result`` and ``queryset`` are reserved seeds too, so stripping tracks
    the whole set rather than the names it held when the strip was written."""
    seen: dict[str, Any] = {}

    def completer(*, value: str, **kwargs: Any) -> list[str]:
        seen.update(kwargs)
        return ["a"]

    out = _complete(_server_with_completer(completer), {name: "attacker", "other": "kept"})

    assert isinstance(out, dict)
    assert name not in seen
    assert seen["other"] == "kept"


def test_a_sibling_named_serializer_reaches_the_completer_as_on_a_tool() -> None:
    """``serializer`` is no seed, and a kernel tool receives a caller's key of
    that name, so a completer is handed it as any other sibling."""
    seen: dict[str, Any] = {}

    def completer(*, value: str, **kwargs: Any) -> list[str]:
        seen.update(kwargs)
        return ["a"]

    out = _complete(_server_with_completer(completer), {"serializer": "sent", "other": "kept"})

    assert isinstance(out, dict)
    assert seen["serializer"] == "sent"
    assert seen["other"] == "kept"


def test_a_sibling_named_request_is_outranked_by_the_seed() -> None:
    """``request`` is no kernel seed and is not stripped, so it is the seed
    written over the spread that keeps a caller's value out."""
    seen: dict[str, Any] = {}

    def completer(*, value: str, request: Any) -> list[str]:
        seen["request"] = request
        return ["a"]

    _complete(_server_with_completer(completer), {"request": "spoofed"})

    assert isinstance(seen["request"], HttpRequest)


def test_an_ordinary_sibling_still_reaches_the_completer_by_name() -> None:
    """Stripping is scoped to the reserved names; a real sibling still binds."""
    seen: dict[str, Any] = {}

    def completer(*, value: str, other: str) -> list[str]:
        seen["other"] = other
        return ["a"]

    _complete(_server_with_completer(completer), {"other": "kept"})

    assert seen["other"] == "kept"


def test_the_arguments_seed_still_carries_the_clients_whole_mapping() -> None:
    """``arguments`` is documented as the siblings the client resolved, and a
    completer reading it knows it is reading caller input."""
    seen: dict[str, Any] = {}

    def completer(*, value: str, arguments: dict[str, Any]) -> list[str]:
        seen.update(arguments)
        return ["a"]

    _complete(_server_with_completer(completer), {"instance": "raw", "other": "kept"})

    assert seen == {"instance": "raw", "other": "kept"}
