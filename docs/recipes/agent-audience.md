# Hide plumbing from the model

A presenter written for your own pages is handed to the model verbatim when the
same spec is exposed as a tool. Everything in it is equally visible, and the
model has no way to tell which fields are for it and which are for the person it
is talking to — so records get named by primary key, and an ETag gets narrated
as if it were content.

Declare the difference on the presenter's dataclass, once, and this server
applies it to both the payload and the advertised `outputSchema`.

## Mark the fields

The marking is the kernel's `FieldMarking`, carried in each field's
`Annotated` extras:

```python
from dataclasses import dataclass
from typing import Annotated

from django_service_specs import FieldMarking


@dataclass
class InvoiceOutput:
    id: Annotated[int, FieldMarking.handle("Invoice handle.")]
    number: Annotated[str, FieldMarking.label()]
    status: str
    etag: Annotated[str, FieldMarking.hidden()]
    amount_cents: int
```

Nothing else changes at registration. A tool whose spec presents through
`DataclassPresenter(InvoiceOutput)` now returns:

```json
{
  "id": 8821,
  "number": "FV/2026/0043",
  "status": "PENDING_REVIEW",
  "amount_cents": 124000
}
```

`etag` is gone. `id` is untouched — a handle is another tool's input, so its
value is never re-spelled — and its `outputSchema` entry says what it is:

```json
"id": {"type": "integer", "description": "Invoice handle."}
```

The tool description gains one generated line, and only when the tool actually
has a handle to explain:

> Identify records by `number`. Fields described as opaque identifiers are for
> other tool calls, not for the reader: pass them to other tools that ask for
> one; refer to records by their name in anything you say, never by the
> identifier.

A handle that declares no wording of its own is described as "An opaque
identifier. Pass it to other tools that ask for one; refer to the record by its
name in anything you say, never by this value." Both sentences are the server's
to change, per server, through
[`MCPServer(conventions=...)`](../concepts.md#agent-conventions).

The markings say which fields the model reads and how; they do not rewrite a
value. A status the model should read the way a person says it is presented
that way: give the dataclass the display value, from the model's
`get_status_display()` or a property of your own.

## Why the payload and not just the description

Both. The description is read once per listing and the schema sits next to the
field it describes, so that is where the wording belongs. But a field the model
should never use is *removed* rather than relabelled: a tool result is emitted
as `structuredContent` **and** rendered into a text content block, so the model
reads every byte twice. Relocating a field under some reserved subtree would
cost its keys twice over and hide nothing.

## Override it for one tool

The presenter stays authoritative — it is the one declaration this server, any
in-process toolset and any other transport serving the spec all read. When a
single tool genuinely needs what its sibling hides, that is a different
presenter, and it belongs on the spec the registry holds rather than on this
mount:

```python
from dataclasses import dataclass, replace

from django_service_specs import DataclassPresenter


@dataclass
class InvoiceWithEtag(InvoiceOutput):
    etag: str  # this tool returns the etag after all


entry = registry.get("lookup_invoice")
billing = registry.by_tag("billing").replace(
    "lookup_invoice",
    replace(entry.spec, presenter=DataclassPresenter(InvoiceWithEtag)),
)

server.register_tools(billing)
```

Registration takes no marking of its own: `register_tool` and every
`register_tools` entry read the markings off the spec's presenter, so a server
registering the spec directly gets the same projection from the same
declaration. Declaring it on the spec is what keeps an in-process Pydantic-AI
toolset and this server projecting the *same* field set: an audience is not a
transport, so a field hidden from one agent caller and visible to another is a
bug you would find in a transcript rather than in a test.

Two fields marked `FieldMarking.label()` raise `ImproperlyConfigured` naming the
tool when it is registered: a record has one name, and picking one silently is
the kind of thing you find in a transcript weeks later.

## Pagination

For a paginated list the projection lands on the items, never on the envelope —
`page`, `totalPages` and `hasNext` are this server's keys and belong to no
presenter.
