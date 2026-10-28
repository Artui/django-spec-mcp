# Register tools from a shared spec registry

If MCP is the only way your project exposes its specs, keep calling
`register_tool` directly — this recipe buys you nothing.

It pays off when the **same specs** are exposed over more than one transport:
MCP here, plus an agent toolset, plus HTTP views. Each transport has to be told
which specs it exposes, so you end up writing the list once per transport — and
the lists drift. A spec gets added to the MCP wiring and forgotten in the
agent's, or the same operation ends up named `create_order` here and something
else there.

`SpecRegistry`, from `django-service-specs`, is the one declaration site.
`MCPServer.register_tools` is the MCP end of it.

## Declare once

```python
# orders/registry.py
from django_service_specs import SpecRegistry

registry = SpecRegistry()


# orders/apps.py
from django.apps import AppConfig


class OrdersConfig(AppConfig):
    name = "orders"

    def ready(self) -> None:
        from orders import specs
        from orders.registry import registry

        registry.register("list_orders", specs.list_orders, tags=("read", "public"))
        registry.register("get_order", specs.get_order, tags=("read", "public"))
        registry.register("refund_order", specs.refund_order, tags=("write", "admin"))
```

The registry holds only what every transport agrees on — which spec, its
canonical name, its tags.

## Register them as tools

```python
# orders/mcp.py
from django_spec_mcp import MCPServer

from orders.registry import registry

server = MCPServer(name="orders", auth_backend=my_auth_backend)
server.register_tools(registry)
```

Each entry becomes a tool through `register_tool`, under its registry name: a
`ServiceSpec` is a service tool, a `SelectorSpec` a selector tool. Registration
order is the registry's order, so `tools/list` stays stable. An entry that is
not a kernel `ServiceSpec` or `SelectorSpec` is refused with a `TypeError`
naming the tool, as `register_tool` refuses it.

This is a **source for** the server's own `ToolRegistry`, not a replacement for
it. Every tool still lands as a normal binding, and names still share the one
MCP tool namespace — a name the server already holds is refused.

## Keep the MCP knobs where they belong

The registry is deliberately transport-agnostic: it has no idea what
`paginate` or `annotations` mean. Everything MCP-specific stays here, per tool,
via `overrides`:

```python
server.register_tools(
    registry,
    overrides={
        "list_orders": {
            "paginate": True,
            "description": "List orders, newest first by default.",
        },
        "refund_order": {
            "annotations": {"title": "Refund an order"},
            "scopes": ("orders:write",),
        },
    },
)
```

Each value is the keyword arguments of `register_tool` for that entry, so
anything it accepts works — `title`, `output_format`, `include_output_schema`,
`rate_limits`, `server_arguments`, and the rest.

A fact about the **operation** is not an MCP knob, though, even when MCP has a
hint for it. Whether repeating a refund is safe is true of the refund on every
transport, so it is declared once on the spec, where each transport reads it:

```python
# orders/specs.py
from django_service_specs import Parameter, Parameters, ServiceSpec

refund_order = ServiceSpec(
    service=refund_order_service,
    reads=Parameters([Parameter("order_pk", "integer", required=True)]),
    permissions=[IsOrderAdmin()],
    # Declared, not left unsaid: a second call refunds again.
    idempotent=False,
)
```

The server turns that declaration into `"idempotentHint": false` on the tool. A
spec that declares nothing (`idempotent=None`, the default) gets no hint, and an
`annotations=` override still wins over the derived value. See
[Tool annotations](../concepts.md#tool-annotations).

## What the entry already says

A spec declares what a caller has to send as its `reads`: a route capture, a
query value or a body field is a `Parameter` like any other, so the entry
mounted here and in an in-process agent toolset advertises the same arguments.
Every transport reads them off the spec:

```python
from django_service_specs import Parameter, Parameters, SelectorKind, SelectorSpec

orders_spec = SelectorSpec(
    kind=SelectorKind.LIST,
    selector=list_project_orders,
    reads=Parameters([Parameter("project_pk", "integer", required=True)]),
    presenter=ORDER,
    permissions=[IsProjectMember()],
)
registry.register("list_project_orders", orders_spec)

server.register_tools(
    registry,
    overrides={"list_project_orders": {"paginate": True}},
)  # the tool takes project_pk, declared once
```

To add a read to an entry registered elsewhere, re-declare the spec it holds
with `dataclasses.replace` and mount the registry `replace` returns:

```python
from dataclasses import replace

spec = registry.get("list_project_orders").spec
scoped = registry.subset("list_project_orders").replace(
    "list_project_orders",
    replace(
        spec,
        reads=Parameters([*spec.reads, Parameter("since", "string", format="date-time")]),
    ),
)

server.register_tools(
    scoped,
    overrides={"list_project_orders": {"paginate": True}},
)  # the tool takes project_pk and since
```

`register_tools` passes nothing about an entry's reads: `register_tool` reads
the spec's own, so the spec above registered with `register_tool(spec, ...)` is
the same tool, advertising the same arguments.

### Answering an argument for the caller: `server_arguments`

A mount that already knows an argument - the project this server is scoped to,
say - answers it with a
[`ServerArguments`][django_service_specs.ServerArguments] from
`django_service_specs`, as an override like any other:

```python
from django_service_specs import ServerArguments

server.register_tools(
    scoped,  # the registry above, whose entry reads project_pk and since
    overrides={
        "list_project_orders": {
            "paginate": True,
            "server_arguments": ServerArguments(
                values={"project_pk": 7},  # always this project
                withheld=frozenset({"since"}),  # never the caller's; the default applies
            ),
        },
    },
)
```

Both names leave the tool's `inputSchema` and its `required` list, so the model
is never asked for them. A caller sending one anyway has its value dropped
before anything reads it, and the server logs the tool and the argument names at
`WARNING`, never the values. Registration checks the binding: a name the spec
does not declare, a value its declared type refuses, and a withheld name with no
default each raise `ImproperlyConfigured` naming the tool. See
[server-supplied arguments](../concepts.md#server-arguments) for the full rule.

The values are fixed for the life of the server. A value computed per request,
such as a project read off the signed-in user, is not something
`server_arguments` carries; scope it in a pool seed, a `kwargs=` provider or a
permission check.

Three things fail loudly rather than quietly, and a refusal of any entry
registers none of them:

- An `overrides` key naming a spec the registry doesn't hold raises
  `ValueError`. A typo would otherwise be a silent no-op.
- A knob that belongs to the other kind of tool — `paginate` on a
  `ServiceSpec`, `invalidates` on a `SelectorSpec` — raises
  `ImproperlyConfigured` naming the tool.
- A key `register_tool` does not take raises `TypeError`.

## Two mounts, two surfaces

The registry does not force one surface. Filtered views (`by_tag`, `subset`)
each return a **new** registry, so a multi-mount deployment feeds each server
its own projection with no shared state:

```python
internal = MCPServer(name="orders-internal", auth_backend=my_auth_backend)
public = MCPServer(name="orders-public", auth_backend=my_auth_backend)

internal.register_tools(registry)  # everything
public.register_tools(registry.by_tag("public"))  # reads only
```

Independent registries work too, when the surfaces have nothing in common:
names are unique *within* a registry, so two registries may reuse one.

## Permissions are not bypassed

A spec declaring no permissions at all never reaches this server:
`SpecRegistry.register` refuses it, because no transport has a policy it could
inherit. `[Unrestricted()]` registers, and says the operation is open out loud.
`register_tools` calls the same `register_tool` you would have called, so every
check a direct registration runs applies to each entry.

Bulk registration is a convenience, not a way to register a surface you
haven't secured. A `scopes=` override adds an OAuth scope gate to an entry,
beside the spec's own permissions rather than in place of them:

```python
server.register_tools(
    registry,
    overrides={"refund_order": {"scopes": ("orders:write",)}},
)
```
