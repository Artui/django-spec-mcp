# Registries

Tool, resource, and prompt lookup, plus session storage and SSE infrastructure.

`register_tool` holds a kernel spec in a `KernelToolBinding`: a `ServiceSpec`
for a mutation tool, a `SelectorSpec` for a read. The spec carries what the
operation takes, who may run it and what it returns; the binding carries the
MCP knobs, the facts of the binding rather than of the operation. A selector
tool's read-shaped pipeline is the spec's: `filter_set` is read from it, and
carries ordering with it via an `OrderingFilter` (a sort read the spec
declares is the other ordering route, and likewise needs no knob);
`paginate` is the one binding-level MCP mechanic. `register_resource` holds a
kernel `RETRIEVE` `SelectorSpec` in a `KernelResourceBinding`. See
[Kernel specs](../concepts.md#kernel-specs).

A tool binding carries `server_arguments`, a
[`ServerArguments`][django_service_specs.ServerArguments] naming the declared
parameters the server answers for, so no caller does. `ToolRegistry.register`
checks it. See [Server-supplied arguments](../concepts.md#server-arguments).

`paginate` also decides what a spec's `shapes` apply to. On a paged binding
the presenter renders each row and the page envelope (`items`, `page`,
`totalPages`, `hasNext`) is built around the result, so a shape applies to
each item in `items`, never to the envelope — and its description in the
`inputSchema` says so. See
[Query params on a paged tool](../concepts.md#query-param-per-item).

::: django_spec_mcp.registry.types.kernel_tool_binding.KernelToolBinding
::: django_spec_mcp.registry.types.kernel_resource_binding.KernelResourceBinding
::: django_spec_mcp.registry.tool_registry.ToolRegistry
::: django_spec_mcp.registry.types.resource_binding.ResourceBinding
::: django_spec_mcp.registry.resource_registry.ResourceRegistry

A URI-template variable may not take a name the server fills for the read: a
pool seed, the kernel's `RESERVED_POOL_SEEDS`, or its `CALL_SEEDS` (`request`
and `view`, the call's own). Registration refuses one that does.

::: django_spec_mcp.types.ResourceEncoding
::: django_spec_mcp.registry.types.prompt_binding.PromptBinding
::: django_spec_mcp.registry.prompt_registry.PromptRegistry

## Interactive views (MCP Apps)

`MCPServer.register_ui_resource(...)` declares an HTML view for an MCP host to
render inline in the chat. The view is an ordinary `ResourceBinding` with the
Apps mime type, `TEXT` encoding, and a `_meta` bundle built from
`UIResourceMeta`; `UIToolMeta` then links a tool to it, so the host renders that
tool's result inside the view. See
[Interactive views](../concepts.md#interactive-views-mcp-apps) for the
host/server split, the three refused-link cases, and the keep-tenant-data-out
rule.

::: django_spec_mcp.registry.types.ui_resource_meta.UIResourceMeta
::: django_spec_mcp.registry.types.ui_csp.UICsp
::: django_spec_mcp.types.UIPermission
::: django_spec_mcp.registry.types.ui_tool_meta.UIToolMeta
::: django_spec_mcp.types.UIVisibility

### The view document

`register_ui_resource(body_template_name=...)` composes the document through
this, so most projects never call it directly. It is exported for the case that
assembles the markup some other way and still wants the packaged shell and the
`ui/*` bridge — a bridge whose failure modes are all silent, and which
[Writing the bridge yourself](../concepts.md#writing-the-bridge-yourself)
describes for anyone declining it.

::: django_spec_mcp.ui.build_app_document.build_app_document

## Bulk registration

`MCPServer.register_tools(registry, *, overrides=None)` registers every spec in
a kernel [`SpecRegistry`][django_service_specs.SpecRegistry] as a tool, in
order, each under its registry name and through `register_tool`, so an entry
and a direct registration of the same spec are the same tool. `overrides` maps
a tool name to the `register_tool` knobs for it. A key the registry does not
hold raises `ValueError`, and a refusal of any entry registers none of them.
See [Register tools from a shared spec registry](../recipes/register-from-spec-registry.md).

`unknown_arguments=` takes the kernel's `UnknownArguments`, imported from
`django_service_specs`: `REJECT`, the default, closes the advertised schema and
refuses a call naming an undeclared argument.

::: django_service_specs.types.unknown_arguments.UnknownArguments

## Tool schemas

What a kernel tool advertises, built from the kernel's own `spec_input_schema`
and `spec_output_schema` with the MCP binding's answers applied: the names the
server fills itself leave the `inputSchema`, a paginated tool gains `page` and
`limit`, and its `outputSchema` describes the page envelope. Exposed for
projects that want to introspect a tool's schema outside the listing.
`tools/list` passes a tool that can present nothing (each binding answers it as
`can_present_nothing`) an `outputSchema` whose root stays an object schema and
admits the `{}` such a call is served as
([Concepts](../concepts.md#dispatch-flow)).

::: django_spec_mcp.schema.kernel_tool_input_schema.kernel_tool_input_schema
::: django_spec_mcp.schema.kernel_tool_output_schema.kernel_tool_output_schema

## Session stores

::: django_spec_mcp.transport.types.session_store.SessionStore
::: django_spec_mcp.transport.in_memory_session_store.InMemorySessionStore
::: django_spec_mcp.transport.django_cache_session_store.DjangoCacheSessionStore

## Server-initiated push

::: django_spec_mcp.transport.types.sse_broker.SSEBroker
::: django_spec_mcp.transport.in_memory_sse_broker.InMemorySSEBroker
::: django_spec_mcp.transport.redis_sse_broker.RedisSSEBroker

## SSE replay (resume)

::: django_spec_mcp.transport.types.sse_replay_buffer.SSEReplayBuffer
::: django_spec_mcp.transport.in_memory_sse_replay_buffer.InMemorySSEReplayBuffer
::: django_spec_mcp.transport.redis_sse_replay_buffer.RedisSSEReplayBuffer
