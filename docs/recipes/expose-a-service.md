# Expose a service

The smallest possible end-to-end. A single service callable plus the arguments
it reads, declared as kernel `Parameters`, is all the MCP server needs.

```python
from django_service_specs import Parameter, Parameters, ServiceSpec, Unrestricted

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend


def calculate_total(*, items: list[dict]) -> dict:
    """Return the total of a list of line-item subtotals."""
    return {"total": sum(item["amount"] for item in items)}


LINE_ITEM = Parameters([Parameter("amount", "integer", required=True)])

server = MCPServer(name="cart", auth_backend=AllowAnyBackend())
server.register_tool(
    ServiceSpec(
        service=calculate_total,
        reads=Parameters([Parameter("items", "array", items=LINE_ITEM, required=True)]),
        permissions=[Unrestricted()],
    ),
    name="cart.calculate_total",
    description="Sum the `amount` field across a list of line items.",
)
```

Mount in `urls.py`:

```python
from django.urls import path

urlpatterns = [path("mcp/", server.urls)]
```

Validation, error mapping (refused arguments and `ServiceValidationError` → an
`isError` `validation_error` result the model can correct from), and JSON-Schema
publication on `tools/list` come for free. The service callable still works as
a plain Python function — call it directly from your tests or from elsewhere
in the codebase, no MCP awareness required.
