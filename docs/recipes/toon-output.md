# Ship TOON for large lists

[TOON](https://github.com/iaminawe/python-toon) is a token-oriented object
notation that compresses uniform list-of-objects payloads dramatically. For
tools that return long lists of similar dicts (catalog listings, search
results, log slices), TOON cuts token usage in half or better.

```bash
pip install "django-spec-mcp[toon]"
```

Pick the format per binding:

```python
from django_service_specs import DataclassPresenter, SelectorKind, SelectorSpec, Unrestricted

from django_spec_mcp import OutputFormat

server.register_tool(
    SelectorSpec(
        kind=SelectorKind.LIST,
        selector=search_invoices,  # returns the fifty best matches
        presenter=DataclassPresenter(InvoiceOutput),
        permissions=[Unrestricted()],
    ),
    name="invoices.search",
    output_format=OutputFormat.AUTO,  # picks TOON when the payload is uniform
)
```

`OutputFormat.AUTO` uses TOON when the payload is a non-empty list whose
elements all share the same key set, and JSON otherwise. Force it explicitly
with `OutputFormat.TOON` if you always want TOON, accepting the JSON fallback
warning when the extra is missing.

`AUTO` looks at the payload as a whole. A tool registered with `paginate=True`
answers with the pagination envelope, an object whose `items` hold the rows, so
`AUTO` renders it as JSON; pass `OutputFormat.TOON` on such a tool to have the
envelope encoded as TOON.

`structuredContent` is always JSON — only the human-readable `content[0]` text
block changes shape. Clients that don't understand TOON natively still see a
fenced code block:

```text
# format: toon
```toon
…compact representation…
```
```

Per-call override is supported too: a client can pass
`{"outputFormat": "json"}` in `tools/call.params` to override the binding's
default for a single invocation.
