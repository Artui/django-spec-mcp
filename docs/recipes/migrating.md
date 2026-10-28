# Migrating to `django-spec-mcp`

This page covers two common starting points: a hand-rolled Django MCP
server (often a single `View` that hand-parses JSON-RPC) and a
FastAPI app exposed via [`fastapi-mcp`](https://github.com/tadata-org/fastapi_mcp).

The migration target is the same in both cases: services and selectors
registered against an `MCPServer` and mounted at a single URL.

## From a hand-rolled MCP view

A typical hand-rolled implementation looks like this — one Django view
that parses JSON-RPC, dispatches by method name, and serialises a
response:

```python
# Before — custom DIY
class MCPView(View):
    def post(self, request):
        msg = json.loads(request.body)
        if msg["method"] == "tools/list":
            return JsonResponse(
                {
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "result": {
                        "tools": [
                            {"name": "create_invoice", "inputSchema": {...}, "description": "..."},
                        ]
                    },
                }
            )
        if msg["method"] == "tools/call":
            args = msg["params"]["arguments"]
            invoice = Invoice.objects.create(
                number=args["number"],
                amount_cents=args["amount_cents"],
            )
            return JsonResponse(
                {
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "result": {
                        "structuredContent": {
                            "id": invoice.id,
                            "number": invoice.number,
                            "amount_cents": invoice.amount_cents,
                        }
                    },
                }
            )
        return JsonResponse(
            {
                "jsonrpc": "2.0",
                "id": msg["id"],
                "error": {"code": -32601, "message": "method not found"},
            },
            status=400,
        )
```

Common gaps in DIY servers — each of which the package handles for you:

- **Streamable-HTTP transport** (sessions, `MCP-Protocol-Version` /
  `MCP-Session-Id` / `Origin` validation, body-size cap, 401
  challenges, notification 202) — required by the MCP spec.
- **Schema introspection** — generating the `inputSchema` /
  `outputSchema` from the spec's dataclasses, forms and reads, so
  `tools/list` is trustworthy.
- **Dispatch wiring** — argument validation, kwarg-pool resolution,
  presentation, error mapping (a validation failure, a refused
  permission, a `ServiceError` → JSON-RPC errors).
- **Auth + rate limits + permissions** — DOT integration, scope-aware
  challenges, the spec's permission checks, per-binding `scopes=` and
  `MCPRateLimit`.
- **Async + SSE + replay** — JSON dispatch in a sync stack, async
  dispatch under ASGI, GET-side server-initiated push, reconnect via
  `Last-Event-ID`.

### Step 1 — Pull the business logic into services and selectors

Mutations become **services**, reads become **selectors**. The
function bodies stay the same; what changes is that the entry points
are now plain Python (no `request` parsing, no JSON shaping) and a
kernel `ServiceSpec` / `SelectorSpec` carries the contract.

```python
# After — plain callables
from django.db.models import QuerySet


def create_invoice(*, number: str, amount_cents: int) -> Invoice:
    return Invoice.objects.create(number=number, amount_cents=amount_cents)


def list_invoices() -> QuerySet[Invoice]:
    return Invoice.objects.all()
```

A service receives each validated argument by name. One that needs the caller
declares `user` among its keywords, and the kernel's pool fills it.

### Step 2 — Describe what goes in and what comes out

The package generates `inputSchema` / `outputSchema` from these. A dataclass
is the shortest form; a Django form you already have works too, through the
kernel's `FormValidator`.

```python
from dataclasses import dataclass


@dataclass
class InvoiceInput:
    number: str
    amount_cents: int


@dataclass
class InvoiceOutput:
    id: int
    number: str
    amount_cents: int
    sent: bool
```

### Step 3 — Register on an `MCPServer`

Replace the custom view with a single `MCPServer` instance:

```python
# After — wired through MCPServer
from django_service_specs import (
    DataclassPresenter,
    DataclassValidator,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
)

from django_spec_mcp import MCPServer

server = MCPServer(name="invoicing", auth_backend=my_auth_backend)

server.register_tool(
    ServiceSpec(
        service=create_invoice,
        validator=DataclassValidator(InvoiceInput),
        presenter=DataclassPresenter(InvoiceOutput),
        permissions=[CanManageInvoices()],
    ),
    name="invoices.create",
    description="Create a new invoice.",
)

server.register_tool(
    SelectorSpec(
        kind=SelectorKind.LIST,
        selector=list_invoices,
        presenter=DataclassPresenter(InvoiceOutput),
        permissions=[CanManageInvoices()],
    ),
    name="invoices.list",
    description="List invoices.",
    paginate=True,
)
```

`CanManageInvoices` is a kernel `PermissionCheck` of your own; see
[Add a custom permission](custom-permission.md). Every spec declares its
permissions, so an operation the DIY view left open says so with
`[Unrestricted()]`.

### Step 4 — Drop the URL conf in

```python
# urls.py
urlpatterns = [path("mcp/", server.urls)]
```

That's it. Every MCP method (`initialize`, `tools/list`, `tools/call`,
`resources/*`, `prompts/*`, `ping`) is handled. Auth, sessions, origin
allowlisting, and the rest of the wire contract are routed through the
package's view.

### Step 5 — Delete the DIY plumbing

Once the new endpoint passes Inspector / your end-to-end suite, delete:

- the custom `View` and its URL
- the custom JSON-RPC dispatcher
- any hand-written input validation that the spec's validator now covers
- error-mapping code that translated exceptions to JSON-RPC errors
  (the package does this at the boundary)

## From `fastapi-mcp`

`fastapi-mcp` decorates FastAPI route handlers and exposes them as MCP
tools. The migration to `django-spec-mcp` is mostly
mechanical because both packages think in terms of "callable + schema":

| `fastapi-mcp`                                              | `django-spec-mcp`                                       |
|------------------------------------------------------------|------------------------------------------------------------------------|
| `@mcp.tool` on a FastAPI handler                           | `register_tool(ServiceSpec(...), name=...)`                             |
| Pydantic input model                                       | a `@dataclass` behind `DataclassValidator`, or a Django form behind `FormValidator` |
| Pydantic output model                                      | a `@dataclass` behind `DataclassPresenter`                              |
| `@mcp.resource("uri://{var}")`                             | `register_resource(SelectorSpec(kind=SelectorKind.RETRIEVE, ...), uri_template="uri://{var}")` |
| `@mcp.prompt`                                              | `register_prompt(name=..., render=...)`                                 |
| FastAPI's dependency injection (`request`, `db`, etc.)     | The kwarg pool — declare the keywords you need on the callable, such as `user`, and register a pool seed for anything else |
| Streamable-HTTP transport implemented inside `fastapi-mcp` | Implemented inside `django-spec-mcp`; same wire shape   |

### Mapping example

```python
# fastapi-mcp
@mcp.tool
async def create_invoice(data: InvoiceInputModel) -> InvoiceOutputModel:
    invoice = await Invoice.objects.acreate(
        number=data.number,
        amount_cents=data.amount_cents,
    )
    return InvoiceOutputModel.from_orm(invoice)
```

```python
# django-spec-mcp
async def create_invoice(*, number: str, amount_cents: int) -> Invoice:
    return await Invoice.objects.acreate(number=number, amount_cents=amount_cents)


server.register_tool(
    ServiceSpec(
        service=create_invoice,
        validator=DataclassValidator(InvoiceInput),
        presenter=DataclassPresenter(InvoiceOutput),
        permissions=[CanManageInvoices()],
    ),
    name="create_invoice",
    description="Create a new invoice.",
)
```

The `async def` works as-is under `server.async_urls`: the dispatch layer
detects an async callable and awaits it natively.

### Pydantic → dataclass

A Pydantic v2 model and a dataclass have similar shapes. For most fields a
mechanical translation is enough:

| Pydantic                   | Dataclass                                      |
|----------------------------|------------------------------------------------|
| `field: str = Field(...)`  | `field: str`                                   |
| `field: int = 0`           | `field: int = 0`                               |
| `field: list[str]`         | `field: list[str] = dataclasses.field(default_factory=list)` |
| `field: SomeModel`         | `field: SomeDataclass`, a nested `@dataclass`  |
| `Field(..., min_length=N)` | a Django form behind `FormValidator`, whose `CharField(min_length=N)` enforces the bound; the `inputSchema` does not advertise it |

Annotate every field concretely: a field typed `Any` has no JSON type to
advertise, so registering the spec raises `ImproperlyConfigured` naming it.

### Ports & deployment

- FastAPI runs under `uvicorn` directly. Django needs an ASGI server
  too — `uvicorn invoicing.asgi:application` is fine.
- `fastapi-mcp` mounts on a path of your choice; this package mounts
  via `path("mcp/", server.urls)`. Both are configurable.
- Auth: `fastapi-mcp` typically piggybacks on FastAPI's dependency
  graph for tokens. Replace with an `MCPAuthBackend` — the
  `DjangoOAuthToolkitBackend` covers most BYO-AS scenarios, or
  implement the Protocol with `httpx` against a remote IDP (see
  [`docs/recipes/async-auth-backend.md`](async-auth-backend.md)).

## Worked example to copy from

[`examples/invoicing/`](https://github.com/Artui/django-spec-mcp/tree/main/examples/invoicing)
is a full Django project that declares its operations as kernel specs and
exercises `register_tool` (a write, and a read with a `FilterSet`, ordering
and pagination), `register_resource` (templated URI), and `register_prompt`. It's a
faster way to see the end-to-end shape than reading the docs alone.
