# `invoicing/` — service tools, selector tools, resource, prompt

A representative Django project that exposes an `Invoice` model over
MCP. Every operation is a `django_service_specs` spec (`ServiceSpec` or
`SelectorSpec`), mounted with `register_tool` and `register_resource`:

| Surface                        | Demonstrated by                                              |
|--------------------------------|--------------------------------------------------------------|
| `ServiceSpec` + `FormValidator` | `invoices.create` — creates a new invoice (atomic mutation), its arguments validated by a Django form. |
| Argument validation            | `invoices.create` with a negative `amount_cents` — an `isError` result of type `validation_error` with the form's errors under `detail`, not a JSON-RPC error.|
| `ServiceSpec(reads=)`          | `invoices.mark_sent` — flips the `sent` flag; `pk` is a read with `minimum=1`, and marking a sent invoice again is a `service_error` result.|
| `instance_selector_spec`       | `invoices.set_amount` — targets an invoice by number, so the lookup's `number` is in its `inputSchema`, required because the read says so, and a call without it is a `validation_error` result; `idempotent=True` lists `idempotentHint: true`; with no output re-read its `outputSchema` keeps a strict `required`.|
| A spec with no Validator       | `invoices.apply_credit` — its `reads` are the service's own keywords, so its `inputSchema` lists `credit_cents` beside the lookup's `number` and, under the default `unknown_arguments=REJECT`, is closed (`additionalProperties: false`): a call adding `memo` is a `validation_error` keyed by it, not an argument dropped unread.|
| `ServiceSpec(allow_none=True)` | `invoices.apply_credit` — no output re-read, so the service's own return is presented; a credit that settles the invoice returns `None`, served as `structuredContent: {}`, which its `outputSchema` admits.|
| `SelectorSpec(filter_set=, shapes=)` | `invoices.list` — a `FilterSet` (its ordering included), pagination, and a `fields` shape the presenter reads for field selection.|
| `server_arguments=`            | `invoices.unsent` — the mount binds the selector's `sent` read to `False`, so the tool's `inputSchema` does not list it and a client `sent: true` is dropped, never read.|
| `SelectorSpec(allow_none=True)` | `invoices.find` — a `RETRIEVE` that may find nothing: a miss is `structuredContent: {}`, which its `outputSchema` admits.|
| `MCPServer(pool_seeds=)`       | `invoices.outstanding` — reads the mount's `currency` seed, which its `inputSchema` does not advertise; a client `currency` argument is refused, not read in its place.|
| `MCPServer(conventions=)`      | `invoices.set_amount` without `number` — the refusal's message is the server's own `missing_arguments` wording, which points the model at `invoices.list`; every sentence it does not override keeps the package's words.|
| `register_resource`            | `invoice` — single invoice by PK via `invoices://{pk}` URI, the template's `pk` being the spec's read.|
| `register_prompt`              | `compose_invoice_email` — render an email body for an invoice.|

Every spec declares `permissions=[Unrestricted()]`, which says "deliberately
open" out loud; a spec declaring none is refused at registration. The prompt
is a render callable rather than a spec, so it is gated by an `MCPPermission`
instead, `OpenPrompt` in `invoices/permissions.py`.

The whole MCP wiring lives in `invoices/mcp.py`. Models, forms, the
dataclasses results are presented through, the presenter, filters, services
and selectors are split into their own modules so each piece is small.

**Field selection is an array of field names.** The `fields` shape declares
the names an invoice has as its `choices`, so the shape check refuses any
other before the selector runs, keyed by its position in the list.

## Layout

```
invoicing/
├── README.md                ← you are here
├── manage.py                ← standard Django entry point
├── invoicing/               ← Django project package
│   ├── __init__.py
│   ├── settings.py
│   ├── urls.py
│   ├── asgi.py
│   └── wsgi.py
└── invoices/                ← single Django app
    ├── __init__.py
    ├── apps.py
    ├── models.py
    ├── forms.py             ← the form invoices.create validates with
    ├── shapes.py            ← the dataclasses results are presented through
    ├── presenters.py        ← field selection for invoices.list
    ├── permissions.py       ← the prompt's gate
    ├── filters.py
    ├── services.py
    ├── selectors.py
    ├── mcp.py               ← MCPServer factory + registrations
    └── migrations/
        ├── __init__.py
        └── 0001_initial.py
```

## Run

```bash
cd examples/invoicing

# Install the package, and django-filter for the selector tool's FilterSet
uv pip install -e "../.." "django-service-specs[filter]"

# Database
python manage.py migrate

# Serve
python manage.py runserver
```

## Drive it

`initialize` to get a session id:

```bash
curl -i -X POST http://localhost:8000/mcp/ \
  -H 'Content-Type: application/json' \
  -H 'Mcp-Protocol-Version: 2025-11-25' \
  -H 'Origin: http://localhost' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2025-11-25","capabilities":{},
                 "clientInfo":{"name":"curl","version":"0"}}}'
# → Mcp-Session-Id: <uuid>
```

Export the session id and walk through the surfaces:

```bash
SID=<paste from above>
# An array, so each header stays one argument once the shell expands it
H=(-H 'Content-Type: application/json'
   -H 'Mcp-Protocol-Version: 2025-11-25'
   -H 'Origin: http://localhost'
   -H "Mcp-Session-Id: $SID")

# Create three invoices
for n in A B C; do
  curl -s -X POST http://localhost:8000/mcp/ "${H[@]}" \
    -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",
         \"params\":{\"name\":\"invoices.create\",
                     \"arguments\":{\"number\":\"INV-$n\",\"amount_cents\":${RANDOM:0:4}00}}}"
done

# A refused argument is a tool result the model can correct from, not a
# JSON-RPC error: `"isError": true`, and in the text block
# {"error": {"type": "validation_error", "message": "Invalid arguments",
#            "detail": {"amount_cents": ["Ensure this value is greater than or equal to 0."]}}}
curl -s -X POST http://localhost:8000/mcp/ "${H[@]}" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call",
       "params":{"name":"invoices.create",
                 "arguments":{"number":"INV-X","amount_cents":-5}}}'

# List with the selector tool — filter, sort, paginate. Ordering reads one
# key holding several names, so it is an array; a number filter is a decimal
# string, as its form field reads one.
curl -s -X POST http://localhost:8000/mcp/ "${H[@]}" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call",
       "params":{"name":"invoices.list",
                 "arguments":{"sent":false,"min_amount":"100","ordering":["-created"],
                              "page":1,"limit":2}}}'

# Select fields. The tool pages, so `fields` names an invoice's fields and
# applies to each item in `items`; its advertised description says so.
curl -s -X POST http://localhost:8000/mcp/ "${H[@]}" \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call",
       "params":{"name":"invoices.list","arguments":{"fields":["id","number"]}}}'

# Selecting the page envelope instead is refused as an `isError` result with
# `"type": "validation_error"` and `detail.fields`, which a model can correct
# from, rather than answered with a page of empty rows
curl -s -X POST http://localhost:8000/mcp/ "${H[@]}" \
  -d '{"jsonrpc":"2.0","id":5,"method":"tools/call",
       "params":{"name":"invoices.list","arguments":{"fields":["items"]}}}'

# Mark one as sent
curl -s -X POST http://localhost:8000/mcp/ "${H[@]}" \
  -d '{"jsonrpc":"2.0","id":6,"method":"tools/call",
       "params":{"name":"invoices.mark_sent","arguments":{"pk":1}}}'

# Take a credit off an invoice. The spec has no Validator: its reads are the
# service's own keywords, and the schema is closed, so adding e.g. "memo" is
# refused as an `isError` `validation_error` keyed by it.
# A credit covering the whole amount deletes the invoice and answers `{}`.
curl -s -X POST http://localhost:8000/mcp/ "${H[@]}" \
  -d '{"jsonrpc":"2.0","id":7,"method":"tools/call",
       "params":{"name":"invoices.apply_credit",
                 "arguments":{"number":"INV-B","credit_cents":100}}}'

# Read it back via the resource template
curl -s -X POST http://localhost:8000/mcp/ "${H[@]}" \
  -d '{"jsonrpc":"2.0","id":8,"method":"resources/read",
       "params":{"uri":"invoices://1"}}'

# Render the email prompt
curl -s -X POST http://localhost:8000/mcp/ "${H[@]}" \
  -d '{"jsonrpc":"2.0","id":9,"method":"prompts/get",
       "params":{"name":"compose_invoice_email","arguments":{"pk":"1"}}}'
```

## Where the patterns are documented

- Service tool basics — [`docs/recipes/expose-a-service.md`](../../docs/recipes/expose-a-service.md)
- Selector tool with FilterSet — [`docs/recipes/selector-tool-with-filterset.md`](../../docs/recipes/selector-tool-with-filterset.md)
- Concepts (`ServiceSpec` / `SelectorSpec`, dispatch flow) — [`docs/concepts.md`](../../docs/concepts.md)
- Auth (this example uses `AllowAnyBackend`; production swaps in DOT) — [`docs/auth.md`](../../docs/auth.md)
