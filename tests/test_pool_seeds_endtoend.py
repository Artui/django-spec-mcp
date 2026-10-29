"""``MCPServer(pool_seeds=)`` reaches every kernel dispatch and every condition the server asks.

A registered seed is what hangs off ``request`` over HTTP and has no channel off
it: a tenant, a locale, a clock. A kernel spec reading one has to receive it
wherever this server runs the spec, or it works when ``dispatch`` is called
directly and fails here. So each reach is driven on its own, because each builds
its call somewhere else:

- the four transport contexts (legacy and modern era, on the sync and the async
  view) and the in-process ``call_tool`` / ``acall_tool``;
- a selector tool and a resource read, beside a service tool;
- the availability check ``tools/list`` and ``unavailable_tools`` run;
- registration, which counts a seed as the source of a reflected read and
  refuses a URI variable a seed occupies.

Asked of kernel specs registered with ``register_tool`` and ``register_resource``.
The task-worker case is asked of a kernel tool beside the other worker
questions, as
``tests/tasks/test_task_lifecycle.py::test_a_task_worker_hands_the_seeds_to_the_tool_it_runs``.
An argument is a ``Parameter`` in ``reads``, and the kernel refuses one a seed
occupies; a kernel selector declares its reads, and a reflected one a seed fills
is the server's. A client argument named after a seed is refused as unknown.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.test import AsyncClient, Client, override_settings
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    Affordance,
    Parameter,
    Parameters,
    PoolSeeds,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer, PromptArgument
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.handlers.handle_completion_complete import handle_completion_complete
from django_spec_mcp.handlers.handle_prompts_get import handle_prompts_get
from django_spec_mcp.handlers.types.context import MCPCallContext
from django_spec_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.urlconf_for import urlconf_for

MODERN = "2026-07-28"
LEGACY = "2025-11-25"
OPEN = [Unrestricted()]

SEEDS: PoolSeeds = DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme")

_TENANT = Parameter("tenant", "string", required=True, reflected=True)
"""The seed's read, declared: ``reflected`` marks it the server's to fill."""


def _whoami(*, tenant: str) -> dict[str, str]:
    # ``tenant`` has no default on purpose: only the registered seed supplies it.
    return {"tenant": tenant}


def _card(*, slug: str, tenant: str, progress: Any) -> dict[str, str]:
    # ``progress`` too: a resource read is handed the kernel's reporter.
    progress(1, total=1)
    return {"slug": slug, "tenant": tenant}


def _scope(*, tenant: str) -> dict[str, str]:
    return {"tenant": tenant}


def _tenant_is(expected: str) -> Affordance:
    return Affordance(
        code=f"not_{expected}",
        reason=f"Only {expected} may do this.",
        when=lambda *, tenant: tenant == expected,
    )


def _service(**declared: Any) -> ServiceSpec:
    return ServiceSpec(service=_whoami, permissions=OPEN, **declared)


def _selector(**declared: Any) -> SelectorSpec:
    return SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_scope, permissions=OPEN, **declared)


def _server(**kwargs: Any) -> MCPServer:
    server = MCPServer(
        name="t",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        pool_seeds=SEEDS,
        **kwargs,
    )
    server.register_tool(_service(), name="tenant.whoami", description="d")
    server.register_tool(_selector(), name="tenant.scope", description="d")
    for code, expected in (("acme_only", "acme"), ("globex_only", "globex")):
        server.register_tool(
            _service(affordances=[_tenant_is(expected)]), name=f"tenant.{code}", description="d"
        )
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_card,
            reads=Parameters([Parameter("slug", "string", required=True), _TENANT]),
            permissions=OPEN,
        ),
        name="tenant.card",
        uri_template="tenants://{slug}",
    )
    return server


def _structured(result: Any) -> Any:
    assert not result.get("isError"), result
    return result["structuredContent"]


# ----- the transports' contexts -----


async def _awaited(response: Any) -> Any:
    return await response


def _post(era: str, is_async: bool, method: str, params: dict[str, Any]) -> Any:
    """One request to the mounted endpoint, in ``era``, on the chosen view."""
    client: Any = AsyncClient() if is_async else Client()
    headers: dict[str, str] = {"Mcp-Protocol-Version": era}
    body: dict[str, Any] = dict(params)
    if era == MODERN:
        headers["Mcp-Method"] = method
        if method == "tools/call":
            headers["Mcp-Name"] = params["name"]
        elif method == "resources/read":
            headers["Mcp-Name"] = params["uri"]
        body["_meta"] = {
            "io.modelcontextprotocol/protocolVersion": MODERN,
            "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "0"},
            "io.modelcontextprotocol/clientCapabilities": {},
        }
    else:
        opened = _send(
            client,
            is_async,
            "initialize",
            {
                "protocolVersion": LEGACY,
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
            {},
        )
        assert opened.status_code == 200, opened.content
        headers["Mcp-Session-Id"] = opened["Mcp-Session-Id"]
    response = _send(client, is_async, method, body, headers)
    assert response.status_code == 200, response.content
    return json.loads(response.content)["result"]


def _send(
    client: Any, is_async: bool, method: str, params: dict[str, Any], headers: dict[str, str]
) -> Any:
    response = client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}),
        content_type="application/json",
        headers=headers,
    )
    return async_to_sync(_awaited)(response) if is_async else response


_TRANSPORTS = pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
_ERAS = pytest.mark.parametrize("era", [LEGACY, MODERN], ids=["legacy", "modern"])


@_TRANSPORTS
@_ERAS
@pytest.mark.django_db(transaction=True)
def test_a_service_tool_reads_a_seed_over_the_wire(era: str, is_async: bool) -> None:
    server = _server()
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        result = _post(era, is_async, "tools/call", {"name": "tenant.whoami", "arguments": {}})

    assert _structured(result) == {"tenant": "acme"}


@_TRANSPORTS
@_ERAS
@pytest.mark.django_db(transaction=True)
def test_the_listing_asks_a_condition_with_the_seeds_over_the_wire(
    era: str, is_async: bool
) -> None:
    server = _server()
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        result = _post(era, is_async, "tools/list", {})

    names = [tool["name"] for tool in result["tools"]]
    assert "tenant.acme_only" in names
    assert "tenant.globex_only" not in names


@_TRANSPORTS
@_ERAS
@pytest.mark.django_db(transaction=True)
def test_a_resource_selector_reads_a_seed_over_the_wire(era: str, is_async: bool) -> None:
    server = _server()
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        result = _post(era, is_async, "resources/read", {"uri": "tenants://north"})

    assert json.loads(result["contents"][0]["text"]) == {"slug": "north", "tenant": "acme"}


# ----- in-process -----


@pytest.mark.django_db
def test_call_tool_hands_the_seeds_to_a_service_and_a_selector() -> None:
    server = _server()

    for name in ("tenant.whoami", "tenant.scope"):
        assert _structured(server.call_tool(name, {}, user=None).to_dict()) == {"tenant": "acme"}


@pytest.mark.django_db(transaction=True)
def test_acall_tool_hands_the_seeds_to_a_service_and_a_selector() -> None:
    server = _server()

    for name in ("tenant.whoami", "tenant.scope"):
        result = async_to_sync(server.acall_tool)(name, {}, user=None)
        assert _structured(result) == {"tenant": "acme"}


# ----- client input cannot occupy a seed -----


@pytest.mark.parametrize("in_process", [True, False], ids=["call_tool", "acall_tool"])
@pytest.mark.parametrize(
    "spec", [_selector(), _selector(reads=Parameters([_TENANT]))], ids=["undeclared", "reflected"]
)
@pytest.mark.django_db(transaction=True)
def test_a_client_argument_named_after_a_seed_is_refused(in_process: bool, spec: Any) -> None:
    # The kernel refuses the argument as unknown, so no client value ever
    # competes with a seed.
    # No validator stands in front of a selector, so this is the path where an
    # unreserved name would be client-controlled. Whether the selector reads the
    # seed undeclared or declares it ``reflected``, the caller's key is not one
    # the tool takes, and ``call_tool`` and the handlers dispatch from two places.
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), pool_seeds=SEEDS)
    server.register_tool(spec, name="tenant.scope", description="d")
    arguments = {"tenant": "globex"}

    result = (
        server.call_tool("tenant.scope", arguments, user=None).to_dict()
        if in_process
        else async_to_sync(server.acall_tool)("tenant.scope", arguments, user=None)
    )

    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"])["error"]["detail"] == {
        "tenant": ["Unknown argument."]
    }


# ----- availability -----


@pytest.mark.django_db
def test_list_tools_and_unavailable_tools_ask_conditions_with_the_seeds() -> None:
    server = _server()

    listed: Any = server.list_tools(user=None)
    names = [tool["name"] for tool in listed["tools"]]
    assert "tenant.acme_only" in names
    assert "tenant.globex_only" not in names
    assert set(server.unavailable_tools(user=None)) == {"tenant.globex_only"}
    assert set(async_to_sync(server.aunavailable_tools)(user=None)) == {"tenant.globex_only"}


def _tenant_of(*, user: Any) -> Any:
    # The resolver the docs show: an anonymous caller resolves to no tenant.
    return getattr(user, "tenant", None)


def _anonymous_listing_server(resolver: Any) -> MCPServer:
    server = MCPServer(
        name="t",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=resolver),
    )
    server.register_tool(_service(), name="tenant.plain", description="d")
    server.register_tool(
        _service(
            affordances=[
                Affordance(
                    code="no_tenant",
                    reason="Sign in to a tenant first.",
                    when=lambda *, tenant: tenant is not None,
                )
            ]
        ),
        name="tenant.members_only",
        description="d",
    )
    return server


@_TRANSPORTS
@_ERAS
@pytest.mark.django_db(transaction=True)
def test_the_listing_resolves_the_seeds_for_an_anonymous_caller(era: str, is_async: bool) -> None:
    # ``AllowAnyBackend`` admits an ``AnonymousUser``, and one declared
    # condition is enough for ``tools/list`` to resolve the seeds for it: the
    # gated tool is hidden because the resolver answered ``None``.
    server = _anonymous_listing_server(_tenant_of)
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        result = _post(era, is_async, "tools/list", {})

    assert [tool["name"] for tool in result["tools"]] == ["tenant.plain"]


@pytest.mark.django_db
def test_a_resolver_that_raises_for_a_caller_fails_that_callers_listing() -> None:
    server = _anonymous_listing_server(lambda *, user: user.tenant)

    with pytest.raises(AttributeError, match="tenant"):
        server.list_tools(user=AnonymousUser())


# ----- registration -----


@pytest.mark.django_db
def test_a_seed_is_the_source_of_a_reflected_read() -> None:
    # The read is required, and nothing but the seed supplies it: so the tool
    # neither advertises it nor asks the caller for it.
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), pool_seeds=SEEDS)
    server.register_tool(_selector(reads=Parameters([_TENANT])), name="tenant.scope")
    listed: Any = server.list_tools(user=None)

    assert listed["tools"][0]["inputSchema"]["properties"] == {}
    assert _structured(server.call_tool("tenant.scope", {}, user=None).to_dict()) == {
        "tenant": "acme"
    }


def test_a_uri_template_variable_named_after_a_seed_is_refused() -> None:
    server = MCPServer(name="t", pool_seeds=SEEDS)

    with pytest.raises(ImproperlyConfigured, match=r"variable name\(s\) \['tenant'\] collide"):
        server.register_resource(
            _selector(reads=Parameters([Parameter("tenant", "string", required=True)])),
            name="tenant.card",
            uri_template="tenants://{tenant}",
        )


def test_prompts_and_completion_do_not_receive_the_seeds_yet() -> None:
    # Pins what the docs state: a prompt's render and a completer are bare
    # callables, not specs, and are called with pools of their own. The day
    # either receives the seeds this fails, and the docs sentence goes.
    seen: dict[str, str] = {}

    def complete_topic(*, value: str, tenant: str = "unset") -> list[str]:
        seen["completer"] = tenant
        return [value]

    server = MCPServer(name="t", pool_seeds=SEEDS)
    server.register_prompt(
        name="brief",
        render=lambda *, topic, tenant="unset": f"{tenant}:{topic}",
        arguments=[PromptArgument(name="topic", required=True)],
        completions={"topic": complete_topic},
        permissions=[Unrestricted()],
    )
    context = MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version=LEGACY,
        pool_seeds=server.pool_seeds,
    )

    rendered: Any = handle_prompts_get({"name": "brief", "arguments": {"topic": "q3"}}, context)
    handle_completion_complete(
        {
            "ref": {"type": "ref/prompt", "name": "brief"},
            "argument": {"name": "topic", "value": ""},
        },
        context,
    )

    assert rendered["messages"][0]["content"]["text"] == "unset:q3"
    assert seen == {"completer": "unset"}


def test_the_server_and_a_bare_context_default_to_the_kernels_seeds() -> None:
    request = HttpRequest()
    bare = MCPCallContext(
        http_request=request,
        token=TokenInfo(user=None),
        tools=MCPServer(name="t").tools,
        resources=MCPServer(name="t").resources,
        prompts=MCPServer(name="t").prompts,
        protocol_version=MODERN,
    )

    assert MCPServer(name="t").pool_seeds is DEFAULT_POOL_SEEDS
    assert MCPServer(name="t", pool_seeds=SEEDS).pool_seeds is SEEDS
    assert bare.pool_seeds is DEFAULT_POOL_SEEDS
