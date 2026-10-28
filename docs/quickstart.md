# Quickstart

Take an existing service and expose it as an MCP tool in under five minutes.

## 1. Install

```bash
pip install django-spec-mcp
```

That brings in `django-service-specs`, the kernel this package serves: every
tool and resource is one of its specs. Nothing else is needed for a first tool.

## 2. Define a service

A service is a regular Python callable that names the keywords it wants. Nothing
about MCP is leaking in here — the kernel hands it each validated argument by
name, and the same callable can be served by any other transport on the kernel.

```python title="invoices/services.py"
from invoices.models import Invoice


def create_invoice(*, number: str, amount_cents: int) -> Invoice:
    """Create a new invoice from the validated arguments."""
    return Invoice.objects.create(number=number, amount_cents=amount_cents)
```

## 3. Describe its input and output

A `Validator` checks the arguments and a `Presenter` renders the result. Both
drive the JSON Schema `tools/list` advertises: the validator the
`inputSchema`, the presenter the `outputSchema`. A dataclass is the shortest
way to state either; a Django form, through `FormValidator`, states rules a
dataclass cannot, such as `max_length`.

```python title="invoices/shapes.py"
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

## 4. Wire the MCP server

```python title="invoices/mcp.py"
from django_service_specs import (
    DataclassPresenter,
    DataclassValidator,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from invoices.services import create_invoice
from invoices.shapes import InvoiceInput, InvoiceOutput

# Dev only: AllowAnyBackend accepts every caller. See step 6.
server = MCPServer(name="invoices", auth_backend=AllowAnyBackend())

server.register_tool(
    ServiceSpec(
        service=create_invoice,
        validator=DataclassValidator(InvoiceInput),
        presenter=DataclassPresenter(InvoiceOutput),
        permissions=[Unrestricted()],
    ),
    name="invoices.create",
    description="Create a new invoice.",
)
```

`register_tool` takes the spec positionally and the MCP knobs by keyword; the
spec's class says what kind of tool it is, a `ServiceSpec` a mutation and a
`SelectorSpec` a read. The service's arguments sit at the top level of the
call's `arguments`, as the kernel's schema advertises them.

`permissions` is required: a spec declaring none is refused at registration,
so an omission is an error rather than an open tool. `Unrestricted()` says
"deliberately open" out loud; swap it for a real kernel `PermissionCheck` (see
[Authentication](auth.md#who-may-call-a-tool)) in anything real.

A read is a `SelectorSpec`, registered the same way:

```python
from django_service_specs import Parameter, Parameters, SelectorKind, SelectorSpec

server.register_tool(
    SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=get_invoice,  # def get_invoice(*, pk): ...
        reads=Parameters([Parameter("pk", "integer", required=True)]),
        presenter=DataclassPresenter(InvoiceOutput),
        permissions=[Unrestricted()],
    ),
    name="invoices.get",
    description="Fetch one invoice by id.",
)
```

## 5. Mount the URLs

```python title="urls.py"
from django.urls import path

from invoices.mcp import server

urlpatterns = [
    path("mcp/", server.urls),
]
```

`server.urls` is a namespaced `(patterns, app_name, namespace)` triple `path()`
mounts directly (like `admin.site.urls` — no `include()`), so the endpoints
reverse as `reverse("mcp:endpoint")` / `reverse("mcp:protected-resource-metadata")`.
Override the namespace with `MCPServer(url_namespace="…")`.

That mounts:

- `POST /mcp/` — Streamable HTTP endpoint.
- `GET /mcp/.well-known/oauth-protected-resource` — RFC 9728 metadata.

## 6. Try it

In dev, the easiest way to drive the server is the official MCP Inspector:

```bash
npx @modelcontextprotocol/inspector --url http://localhost:8000/mcp/
```

Inspector lets you list tools, fill in arguments from the generated JSON Schema,
and watch the response. For automated checks, a single `curl` works too:

```bash
# 1) initialize → the response carries an Mcp-Session-Id header
curl -i http://localhost:8000/mcp/ \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2025-11-25",
      "capabilities": {},
      "clientInfo": {"name": "curl", "version": "0"}
    }
  }'

# 2) re-use that session id on every subsequent call
SESSION="<paste from response header>"
curl http://localhost:8000/mcp/ \
  -H 'Content-Type: application/json' \
  -H "Mcp-Protocol-Version: 2025-11-25" \
  -H "Mcp-Session-Id: $SESSION" \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {
      "name": "invoices.create",
      "arguments": {"number": "INV-1", "amount_cents": 100}
    }
  }'
```

The server above passes `AllowAnyBackend`, which accepts every caller and is
for local development only. A server built with no `auth_backend=` gets
`DjangoOAuthToolkitBackend`, which needs the `[oauth]` extra and refuses
unauthenticated requests. Before going to production, use that backend or
plug in your own, and set an `ALLOWED_ORIGINS` allowlist — see
[Authentication](auth.md) for the recipe.
