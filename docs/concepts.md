# Concepts

A short tour of every moving part. Read this once; the rest of the docs assume
you have these in your head.

## A kernel spec is the unit of registration { #kernel-specs }

The MCP server does **not** wrap, walk, or otherwise reach into views or
routers. It serves [`django_service_specs`](https://artui.github.io/django-service-specs/)
specs: a `ServiceSpec` for an operation that writes, a `SelectorSpec` for one
that reads. The spec is the whole declaration of the operation (what it takes,
who may run it, how its result is presented), so the same spec serves every
transport built on the kernel, and a project needs no view at all to expose it
over MCP.

`MCPServer.register_tool` takes a spec positionally. The spec's class decides
the kind of tool: a `ServiceSpec` is a service tool, a `SelectorSpec` a selector
tool. `register_resource` takes a `RETRIEVE` `SelectorSpec` the same way.

```python
from django_service_specs import (
    DataclassPresenter,
    DataclassValidator,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

server.register_tool(
    ServiceSpec(
        service=record_invoice,
        validator=DataclassValidator(InvoiceInput),
        presenter=DataclassPresenter(InvoiceOutput),
        permissions=[Unrestricted()],
    ),
    name="invoices.create",
    description="Create an invoice.",
)
server.register_tool(
    SelectorSpec(
        kind=SelectorKind.LIST,
        selector=list_invoices,
        presenter=DataclassPresenter(InvoiceOutput),
        permissions=[Unrestricted()],
    ),
    name="invoices.list",
    description="List invoices.",
    paginate=True,
)
server.register_resource(
    SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=invoice_by_pk,
        reads=Parameters([Parameter("pk", "integer", required=True)]),
        presenter=DataclassPresenter(InvoiceOutput),
        permissions=[Unrestricted()],
    ),
    name="invoice",
    uri_template="invoices://{pk}",
)
```

Anything that is not a kernel spec is refused with a `TypeError` at
registration.

### What a spec carries through to MCP

Every field a spec declares is the kernel's to run, so it means on this
transport what it means on any other:

- **`permissions`** are kernel `PermissionCheck`s, and they are the spec's
  guard. The call asks them of the principal the auth backend resolved for the
  request. An operation open to every caller declares `[Unrestricted()]`.
- **`reads`** are the arguments the callable takes, as kernel `Parameters`;
  **`shapes`** are the read-shaping values its presenter reads
  ([Read-shaping values](#read-shaping-values-shapes)). Both are advertised in
  the tool's `inputSchema`, and a `Parameter`'s `help` is its property's
  `description`.
- **`validator`** checks a service's input. Its fields are the tool's
  arguments, spread at the top level of `arguments` as the kernel's
  `spec_input_schema` advertises them, with no `data` key around them. A
  `many=True` service takes a list instead ([A list payload](#list-payload)).
- **`presenter`** renders the result, and its output schema, through the
  kernel's `spec_output_schema`, is the tool's `outputSchema`.
- **`kind`** is the required `SelectorKind`, `LIST` or `RETRIEVE`. It decides
  whether the tool serves a list or one object, and which knobs the
  registration accepts: a `RETRIEVE` takes no `paginate=`.
- **Queryset shaping** (`select_related`, `prefetch_related`, `annotations`,
  `extend_queryset`, `filter_set`), the target lookups
  (`instance_selector_spec`, `collection_selector_spec`), the output re-read
  (`output_selector_spec`), `preconditions`, `affordances`, `atomic` and
  `allow_none` all run inside the kernel's `dispatch`.
- **`kwargs=`** is a per-spec provider of extra keyword arguments, called
  through the keyword pool, so it declares the entries it needs by name
  (`user`, the call's `request`, a registered [seed](#pool-seeds)). A key a
  provider whose return annotation is a `TypedDict` always fills is the
  server's, and is left out of the `inputSchema`.
- **`idempotent`** reaches the tool's annotations
  ([Tool annotations](#tool-annotations)).

### How a tool is called

- **The call is the kernel's `dispatch` / `adispatch`.** The principal is the
  token's user, an `AnonymousUser` for a token naming no one and for
  `call_tool(..., user=None)`. The call context holds `request`, `view` (always
  `None` here), `format` and `action`, the tool's name. Over HTTP,
  `call_context["request"]` is the transport's Django `HttpRequest`, with the
  resolved token as `request.mcp_token` and the token's principal as
  `request.user`. An in-process call has no HTTP request of its own:
  `acall_tool` puts a token built from its `user=` on the request it works on,
  and sync `call_tool` puts none there, even when the request handed to it
  carried one, so a check reading `request.mcp_token` finds nothing under
  `call_tool`
  ([Transport-neutral invocation](#transport-neutral-invocation-call_tool)).
- **The spec's own `permissions` answer the call, before the rate limit is
  charged.** A refused principal is `-32006` "Insufficient permission",
  uncharged, and `call_tool`, which charges nothing, raises the kernel's
  `NotPermitted` before it checks the arguments. A check declaring `arguments`
  is shown them as the kernel's argument check reads them, so `"8"` sent for an
  `integer` reaches it as `8`, and a URI's `true` for a `boolean` as `True`. The
  checks are asked once per call whose arguments pass that check, twice when
  the call streams progress (only the async transport streams). A call whose
  arguments fail that check is never shown them: it is judged as a filtered
  listing judges the spec, its checks asked on no arguments, and is refused
  `-32006` before the charge when they refuse; otherwise a tool call is told
  which argument is wrong after the charge. A `many` call whose top level passes
  and whose rows do not is the exception: its checks answer on the checked top
  level, a refusal as `-32006` before the charge, and an admitted caller is told
  the rows after it. A resource's completion has no arguments and is
  judged the same way. A tool registered with a `task_policy=` other than the
  default has the checks asked before its task is created, and again by the
  task's worker ([Opting a tool in](#opting-a-tool-in)).
- **The schemas are the kernel's** `spec_input_schema` / `spec_output_schema`.
  `unknown_arguments=` reaches both the schema and the call, and
  [`server_arguments=`](#server-arguments) names are left out of the schema and
  filled by the server.
- **A declaration no call could run is refused at registration.**
  `register_tool`, `register_tools` and `register_resource` ask the kernel's
  `check_seeds` with the server's [`pool_seeds=`](#pool-seeds) and raise
  `ImproperlyConfigured`, naming the tool or resource, for a parameter named
  like a registered seed or the call's own `request` or `view`, a
  positional-only parameter with no default, a keyword the callable requires
  that nothing fills, and an optional read over a keyword the callable
  requires. A read always sent is checked as required and registers: one the
  tool's [`server_arguments=`](#server-arguments) binds a value to, and one a
  resource's URI template names, which the matcher fills on every read. An
  optional read neither covers is still refused.
- **`scopes=` is the transport's gate.** A token must carry every scope named;
  one that is missing any is answered `-32006` with every declared scope in
  `requiredScopes`, not only the missing ones. A bare string is one scope.
  `scopes=()` is refused, since it reads as a gate and admits everyone. A
  `KernelToolBinding` or `KernelResourceBinding` built by hand and passed to
  `server.tools.register` or `server.resources.register` derives the same gate
  from its own `scopes` and refuses `()` the same way.
- **A spec declaring `permissions=None` is unguarded.** With
  `REQUIRE_TOOL_PERMISSIONS` on, the default, registering one is refused. With
  it off, the tool or resource registers with one `UnguardedToolWarning` and
  every call runs without a permission check, under a kernel `Grant` minted for
  that spec and the calling principal. A spec with permissions of its own is
  never granted: its checks run whatever the setting says. Only
  `register_tool` and `register_resource` grant; a kernel `SpecRegistry`
  refuses such a spec when it is built, so `register_tools` never receives
  one. A `KernelToolBinding` or `KernelResourceBinding` built by hand and
  passed to `server.tools.register` or `server.resources.register` is never
  granted, whatever the setting, so the kernel refuses its unguarded spec at
  the call and the operation does not run. An operation open to every caller
  declares `[Unrestricted()]` instead. The scope gate does not count as a
  guard.
- **Registration refuses what a tool cannot mean**: `paginate=` /
  `max_page_size=` on a service tool or a `RETRIEVE`, `invalidates=` on a
  selector tool, and `UnknownArguments.PASSTHROUGH`. `task_policy=` is taken by
  either kind of tool, as [Opting a tool in](#opting-a-tool-in) describes.

### Project-wide seeds: `pool_seeds=` { #pool-seeds }

A spec's `kwargs=` provider is per spec. What every spec on a mount may read —
a tenant, a locale, a clock — is a seed, registered once on the server as a
kernel [`PoolSeeds`][django_service_specs.PoolSeeds], the same registry the
kernel's `dispatch(pool_seeds=)` takes:

```python
from django_service_specs import DEFAULT_POOL_SEEDS


def tenant_of(*, user):
    # An anonymous caller has no tenant, and resolves to none rather than raising.
    return getattr(user, "tenant", None)


server = MCPServer(
    name="billing",
    pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=tenant_of),
)


def list_invoices(*, tenant):  # receives the resolved tenant on every call
    return Invoice.objects.filter(tenant=tenant)
```

A resolver is called through the keyword pool like every other provider, so it
declares the entries it needs (`user`, `request`) and is resolved per call
against the caller. The default is the kernel's own `DEFAULT_POOL_SEEDS`, which
registers nothing, so a server that passes none adds nothing to the pool.

**A resolver must answer for every caller the mount admits**, an anonymous one
included whenever the auth backend lets one through, because the listing reads
it too. Once any tool declares an operation condition, `tools/list` resolves
the seeds for its caller (see the second bullet below), so a resolver that
raises for a caller fails that caller's whole listing, not only its calls.
`lambda *, user: user.tenant` is the form that does: an `AnonymousUser` has no
`tenant`.

- **Every spec the server runs receives them.** A service or selector tool over
  either transport and either era, `call_tool` / `acall_tool`, a task the worker
  runs, and a resource's spec on `resources/read`.
- **The availability check asks with them too.** `tools/list`, `list_tools` and
  `unavailable_tools` resolve the seeds into the pool they ask an operation
  condition against, so `when=lambda *, tenant: tenant.can_close_books` is asked
  the same question at the listing as at the call
  ([A tool that cannot run now is not listed](#a-tool-that-cannot-run-now-is-not-listed)).
- **A registered name is never the caller's.** It is left out of the tool's
  `inputSchema` and never required, and a caller sending it anyway is answered
  as for any argument the spec does not declare: refused under
  `UnknownArguments.REJECT`, dropped under `IGNORE`. The seed's value is the
  only one the callable receives.
- **Registration agrees with dispatch.** A callable declaring a seed with no
  default registers, since the seed is always supplied. A read named after a
  seed, and a URI-template variable named after one, are refused, since the
  argument and the seed would meet in one pool.
- **One set per mount, with no per-call or per-tool override.** What varies per
  call belongs in the resolver; a mount needing different seeds is a second
  `MCPServer`.

Prompt rendering and argument completion call bare callables rather than specs,
and do not receive the seeds.

### Which parameters a client is asked for { #selector-requiredness }

A tool advertises its spec's parameters: the `reads`, the `shapes`, a
service's validator fields, and a target lookup's reads. The kernel lists every
parameter as declared, `required` meaning the call needs the value from
someone, and the server leaves out of the schema what it answers itself:

- a name the server fills whatever the caller sends is left out of
  `properties` and `required`: the tool's
  [`server_arguments`](#server-arguments), a registered
  [seed](#pool-seeds), and a key a typed `kwargs=` or `input_data=` provider
  always fills (the kernel's `server_owned_keys`);
- a parameter a provider may fill is offered and not required, because
  dispatch does not demand it of the caller (the kernel's
  `provider_reached`). A key whose `TypedDict` value admits `UnsetType` is one
  the provider may decline, and a provider whose return is not a `TypedDict`,
  a plain `-> dict` or a lambda included, may fill any parameter, so beside
  one nothing it reaches is required. Annotate the provider to have the rest
  inferred.

```python
from typing_extensions import TypedDict

from django_service_specs import Parameter, Parameters, SelectorKind, SelectorSpec


class Scope(TypedDict):
    tenant: str


def scope(*, request) -> Scope:  # annotated, so `tenant` is known to be filled
    return {"tenant": request.user.tenant}


spec = SelectorSpec(
    kind=SelectorKind.RETRIEVE,
    selector=get_invoice,  # def get_invoice(*, pk: int, tenant: str)
    reads=Parameters([Parameter("pk", "integer", required=True)]),
    kwargs=scope,
    permissions=[IsBillingStaff()],
)  # inputSchema: {"pk"}, required ["pk"]
```

**A call that leaves out a required argument is refused before the callable
runs**, with a `validation_error` result naming what is missing, keyed in
`detail` by the missing name, on every route (both transports and eras,
`call_tool` and `acall_tool`):

```json
{"error": {"type": "validation_error", "message": "Missing required argument(s): `pk`.",
           "detail": {"pk": ["This field is required."]}}}
```

The message is the server's
[`conventions.missing_arguments`](#agent-conventions), worded with every name
the kernel's `InvalidArguments.missing` carries, sorted and in backticks, and
the `detail` is the kernel's per-field detail. A call that left one argument out
and sent another wrong names the missing one in the message and both in the
detail; a refusal naming nothing missing answers `"Invalid arguments"`.

The check runs **after the permission answer**: a caller the spec's checks
refuse is refused for the permission first, and never learns from a
missing-argument answer that the tool exists. An admitted caller is told about
the argument after the rate limit is charged.

### Read-shaping values: `shapes`

A spec's `shapes` are the caller's values its presenter reads while it renders
the result: a field selection, an expansion flag. They are `Parameters` like
`reads`, advertised in the tool's `inputSchema` beside them, and the
presenter receives them on its `PresentationContext.shapes`:

```python
from django_service_specs import Parameter, Parameters, SelectorKind, SelectorSpec

server.register_tool(
    SelectorSpec(
        kind=SelectorKind.LIST,
        selector=list_invoices,
        shapes=Parameters(
            [Parameter("fields", "array", items="string", help="Fields to return, e.g. id, number")]
        ),
        presenter=FieldSelectingPresenter(InvoiceOutput),
        permissions=[IsBillingStaff()],
    ),
    name="invoices.list",
    description="List invoices.",
    paginate=True,
)
```

#### On a paged tool it shapes each item, never the page { #query-param-per-item }

With `paginate=True` the tool serves the `{items, page, totalPages, hasNext}`
envelope, and the presenter never sees it: the envelope is built *around* the
rows the presenter rendered, so a shape reaches only a row. A selection
therefore names the fields of one item, never the envelope's: `["id", "number"]`
rather than `["items"]`. That is the easy mistake to make, because the
`outputSchema` a model reads describes the envelope. So on a paged tool, each
shape's description in the `inputSchema` ends with:

> On a paged result it applies to each item in `items`, never to the page
> envelope (`items`, `page`, `totalPages`, `hasNext`).

A service tool and an unpaginated list have no envelope, and get no sentence.
The words are the server's [`conventions.query_param_on_pages`](#agent-conventions),
which can change them or drop them; whether the sentence appears stays the
server's call.

The package does not rewrite a selection written the other way: a shape is
opaque to the transport, so correcting one would mean knowing every grammar a
presenter might parse. What it controls is what the caller hears when the
presenter refuses a value, and that depends on how the presenter selects:

- **Strict selection**, a presenter that raises the kernel's
  `ServiceValidationError` for a name it does not know. That comes back as an
  `isError: true` tool result of type `validation_error`, the same shape a
  service's `ServiceValidationError` produces ([Dispatch flow](#dispatch-flow)),
  and its message names the argument, then gives the presenter's own words:
  "`fields` was rejected while rendering the result: Unknown field `items`." The
  transport adds nothing about any one grammar. On a paged tool the per-item
  sentence follows it. The detail is keyed under the shape's name; when a call
  supplied several shapes it sits under `non_field_errors` and the message
  names them all, because nothing says which one the presenter refused. A model
  reads the result and retries with a corrected selection.
- **Tolerant selection**, a presenter that drops the names it does not know,
  answers `["items"]` with a page of `{}`: one empty object per row, a
  well-formed result no transport can tell from a real one. It opts out of the
  error signal entirely, so **tools an agent calls should select strictly** —
  that is the setting in which the model can correct itself.

The tool result is reserved for the failures the caller caused. A presenter's
`ServiceValidationError` becomes one only when the call **sent** a non-null
value for at least one of the spec's shapes. A shape the tool's
[`server_arguments`](#server-arguments) binds does not count, because a value
the server supplied is not the caller's to correct, and neither does a `null`.
With nothing sent the presenter refused on its own, no retry could change the
outcome, and it stays a server error — over HTTP a JSON-RPC `-32603` reading
`Internal error` under a `500`, with the exception in the log; from `call_tool`
and `acall_tool`, the exception itself. Anything else a presenter raises is a
server error whatever the caller sent: an `AttributeError` in a presenter is a
bug, not a bad argument. Every path a tool is presented on answers alike:
both transports, sync and async, and the in-process `call_tool`.

A [resource](#a-resource-is-a-retrieve-selector) whose URI template names a
shape answers the same refusal of a value its URI carried as the read answers
a bad URI variable: JSON-RPC `-32602` "Invalid arguments", with the detail
keyed by the shape under `data.detail`, or under `non_field_errors` when the
URI carried several. With no shape in the URI it is the internal error.

### Server-supplied arguments: `server_arguments=` { #server-arguments }

When the mount already knows an argument - a server scoped to one project, a
deployment that always reads one tenant - asking the model for it is both
wasted tokens and a door left open: the model can send another project's id.
`server_arguments=` answers the argument for the caller instead. It takes a
[`ServerArguments`][django_service_specs.ServerArguments] from
`django_service_specs`, on `register_tool` and as a `register_tools` override,
and each route checks it the same way:

```python
from django_service_specs import ServerArguments

server.register_tool(
    vendors_spec,  # reads: project_pk, draw_request_pk
    name="list_document_vendors",
    description="List the vendors on a project's documents.",
    server_arguments=ServerArguments(
        values={"project_pk": 7},  # this server's project, always
        withheld={"draw_request_pk"},  # never the caller's; the default applies
    ),
)
```

- **The names leave the tool's `inputSchema`** and its `required` list, on
  `tools/list` and wherever else the schema is built, so the model is never
  asked for them. A bound value can fill a read the spec marks
  `required=True`.
- **A caller's value for one is dropped before anything reads it**: the spec's
  permission checks, the missing-argument check and the callable all see the
  server's value. A value an elicitation answer carries is dropped the same
  way. The server logs the tool's name and the dropped argument names at
  `WARNING`, never the values; a caller restating the bound value is not
  logged. A withheld name is absent, so the callable runs on its own default.
- **It is checked at registration.** A name the spec does not declare, a value
  its declared type refuses, a name the server fills as a pool seed, and a
  withheld name the callable cannot do without each raise
  `ImproperlyConfigured` naming the tool. A binding built by hand and passed to
  `server.tools.register` is checked there too.

The values are **static** for the life of the binding. A value computed per
request - the project the signed-in user belongs to - is not something
`server_arguments` carries: compute it in a `kwargs=` provider, or refuse the
call in a permission check.

### A list payload: `many=True` service tools { #list-payload }

A `ServiceSpec` with `many=True` validates its input as a list, item by item
through its validator, and hands the service that list as `data`. A
`tools/call`'s `arguments` is always a JSON object, so here the list travels
under one argument, named by the spec's `many_argument`, `items` unless the
spec says otherwise:

```python
from django_service_specs import DataclassValidator, ServiceSpec


def create_invoices(*, data):
    return [create_invoice(item) for item in data]


server.register_tool(
    ServiceSpec(
        service=create_invoices,
        validator=DataclassValidator(InvoiceInput),  # describes one item
        many=True,
        many_argument="invoices",  # optional; the default is "items"
        permissions=[IsBillingStaff()],
    ),
    name="invoices.bulk_create",
    description="Create several invoices at once.",
)
```

The tool advertises the argument as an array of the item schema, bounded by the
`maxItems` the kernel's schema declares:

```json
{
  "type": "object",
  "properties": {
    "invoices": {"type": "array", "maxItems": 1000,
                 "items": {"type": "object", "properties": {"number": {"type": "string"}}}}
  },
  "required": ["invoices"],
  "additionalProperties": false
}
```

What a caller should know:

- **The tool's `unknown_arguments` decides what travels beside the list.**
  Under `REJECT`, the default, any other argument is refused and the top level
  of the schema is closed; under `IGNORE` it is dropped and the top level is
  open.
- **`unknown_arguments` applies inside each item too.** Each item is checked as
  a single-item tool's arguments are, and each item's own
  `additionalProperties` says whether an undeclared key in it is refused.
- **Errors are keyed under the argument, and item errors by index.** An invalid
  second item is a `validation_error` result whose `error.detail` is
  `{"invoices": {"1": {"amount": ["Expected an integer."]}}}`. JSON object keys
  are strings, so the index arrives as `"1"`; with `INCLUDE_VALIDATION_VALUE`
  on, `error.value["invoices"][1]` is the item it names. A call with no list is
  refused under the argument as missing.
- **The result is the service's list.** It is presented as a list, and the
  `outputSchema` is an array of the item schema. `structuredContent` is that
  bare array, as for every unpaginated list result this server returns, not an
  object wrapping it.

### A resource is a `RETRIEVE` selector { #a-resource-is-a-retrieve-selector }

`register_resource(spec, *, uri_template, name, ...)` takes a kernel
`SelectorSpec` of kind `RETRIEVE` positionally, as `register_tool` does. The
read runs the spec through the kernel's `dispatch` / `adispatch`, with the
URI-template variables as its arguments, so the spec's `reads` name them and
its presenter renders the row:

```python
from django_service_specs import (
    DataclassPresenter,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
)

server.register_resource(
    SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=get_invoice,  # def get_invoice(*, pk): ...
        reads=Parameters([Parameter("pk", "integer", required=True)]),
        presenter=DataclassPresenter(InvoiceOutput),
        permissions=[IsBillingStaff()],
    ),
    name="invoice",
    uri_template="invoices://{pk}",
    description="One invoice, by primary key.",
)
```

- **A reader the spec's checks refuse is answered `-32006`** and is not
  charged. A reader they admit is charged, and a URI variable that fails the
  spec's argument check is then answered `-32602` with message
  `Invalid arguments` and the kernel's detail tree under `data.detail`, as an
  admitted tool call is told which argument is wrong;
  `INCLUDE_VALIDATION_VALUE` adds the variables sent under `data.value`.
  `scopes=` gates a read as it gates a tool.
- **A `uri_template` variable must be a read or shape of the spec that the
  server does not fill.** Registration raises `ImproperlyConfigured` naming the
  resource and the variable when no read or shape declares it, when the read
  seeds the name itself (the call's `request`, or a seed the server
  registers), and when a reflected read the
  server fills or a key a typed `kwargs=` provider always fills makes it the
  server's, since every read would be refused or would serve the
  server's value for any URI.

#### What a resource carries, and what registration refuses { #what-a-resource-cannot-carry-and-why-registration-refuses-it }

The read dispatches the spec itself, so everything it declares runs on a read
as on a tool call: its permissions, its queryset shaping and its
`allow_none` alike. What a resource cannot take is a shape the read has no
room for, and registration refuses each one rather than serving something
other than what was declared:

| Refused | Why |
| --- | --- |
| A `LIST` spec | A resource reads one row by URI. Register a `LIST` as a selector tool, which paginates. |
| `encoding=TEXT` or `BLOB` beside a spec declaring `affordances` or `allow_none` | The kernel adds affordances to a presented object, and under `allow_none` a missing row presents as `None` without the presenter running; neither is a document or bytes. Serve either as JSON. |
| `permissions=` beside a spec | The spec's own `permissions` are the resource's guard. |
| A URI variable no read or shape declares, or one the server fills | See above: every read would be refused, or would serve the server's value for any URI. |

#### The body a read serves { #the-body-a-read-serves }

`encoding=` decides how the presented value becomes the `contents` entry, and
`mime_type=` is advertised as given, on the read and in both listings, never
inferred or checked:

| `encoding=` | The presenter returns | Served as |
| --- | --- | --- |
| `ResourceEncoding.JSON`, the default | anything JSON-encodable | `text`, the value as JSON |
| `ResourceEncoding.TEXT` | a `str`: Markdown, CSV, HTML, plain text | `text`, the string as written |
| `ResourceEncoding.BLOB` | `bytes`, `bytearray` or a `memoryview` | `blob`, base64, and no `text` |

```python
from django_service_specs import (
    Output,
    Parameter,
    Parameters,
    Presenter,
    SelectorKind,
    SelectorSpec,
)

from django_spec_mcp import ResourceEncoding


class InvoiceMarkdown(Presenter):
    def output(self) -> Output:
        return Output()  # a document, not an object of fields

    def present(self, value):
        return f"# Invoice {value.number}\n\nTotal: {value.total}"


server.register_resource(
    SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=get_invoice,
        reads=Parameters([Parameter("pk", "integer", required=True)]),
        presenter=InvoiceMarkdown(),
        permissions=[IsBillingStaff()],
    ),
    name="invoice-summary",
    uri_template="invoices://{pk}/summary",
    mime_type="text/markdown",
    encoding=ResourceEncoding.TEXT,
)
```

A spec with no presenter serves its selector's value the same way. A `TEXT`
resource under the default `application/json` is still served verbatim, since
the mime type is never checked against the body. A presenter returning the
wrong type for its encoding, a dict under `TEXT` or a `str` under `BLOB`, is
answered `-32603` naming the resource and the code that produced the value, a well-formed error rather than a 500,
because only a read can find it out. The result ceiling and the cache hints
apply to every encoding alike. An HTML view the host renders is
[`register_ui_resource`](#interactive-views-mcp-apps), which sets both.

### Registering a whole `SpecRegistry`

`register_tools(registry, *, overrides=None)` registers every entry of a kernel
[`SpecRegistry`][django_service_specs.SpecRegistry], in order, each under its
registry name, with the knobs `overrides` holds under that name:

```python
from django_service_specs import SpecRegistry

registry = SpecRegistry()
registry.register("invoices.create", create_spec)
registry.register("invoices.list", list_spec)

server.register_tools(
    registry,
    overrides={
        "invoices.create": {"description": "Create an invoice.", "scopes": ("billing",)},
        "invoices.list": {"description": "List invoices.", "paginate": True},
    },
)
```

An entry and a direct `register_tool` of the same spec are the same tool. An
`overrides` key the registry does not hold is a `ValueError`, as a typo rather
than a no-op, and an entry that is not a kernel `ServiceSpec` or `SelectorSpec`
is refused as `register_tool` refuses it. A refusal of any entry registers none
of them: every entry is built and checked first. It returns the bindings in
registration order.

## Per-tool registration kwargs

Beyond `description=`, `scopes=` and [`server_arguments=`](#server-arguments),
`register_tool` takes the facts of the MCP binding rather than of the
operation: `title`, `icons`, `annotations`, `meta`, `ui`,
`content_kind`, `content_mime_type`, `include_structured_content`,
`include_output_schema`, `task_policy`, `invalidates` (service tools),
`paginate` and `max_page_size` (selector tools), `max_result_bytes`,
`dispatch_timeout`, `always_listed`, `rate_limits` and `unknown_arguments`.
`display_name` and `display_description` are labels for in-process consumers,
read off the binding and never sent on the wire. Each is also a key
`register_tools` takes in `overrides`. Two of them decide what a caller is
shown and may send:

- **`unknown_arguments=`** — how `arguments` keys the spec does not declare are
  handled. It takes the kernel's `UnknownArguments`, and the `inputSchema` is
  the kernel's `spec_input_schema` under the same policy, so **the closed
  schema is advertised exactly where the call refuses an undeclared name**.
  - `UnknownArguments.REJECT` (default) — an undeclared key is refused with an
    `isError` `validation_error` result keyed by the key's name under `detail`
    (`{"note": ["Unknown argument."]}`), and the `inputSchema` advertises
    `"additionalProperties": false`.
  - `UnknownArguments.IGNORE` — undeclared keys are dropped before the spec
    sees them, and the `inputSchema` is open.
  - `UnknownArguments.PASSTHROUGH` — refused at registration with
    `ImproperlyConfigured` naming the tool, because the kernel never hands an
    undeclared argument to a callable. Declare the argument as a read and pass
    `IGNORE` or `REJECT`.

  A paginated tool's `page` and `limit` are declared by the server and always
  known.

- **`always_listed=`** — when
  `SPEC_MCP["FILTER_LISTINGS_BY_PERMISSIONS"]` is enabled,
  bindings are dropped from `tools/list` / `resources/list` /
  `prompts/list` when their permissions deny the current caller, and a
  tool is left out of `tools/list` whenever an operation-scope affordance
  refuses it right now (see [below](#a-tool-that-cannot-run-now-is-not-listed)).
  Setting `always_listed=True` keeps the binding visible through both as a
  discovery aid; the permission and the affordance still gate the actual
  invocation. For a tool or a resource the permissions that deny are its spec's
  own checks, asked with no arguments; see
  [Filtering listings by permissions](auth.md#filtering-listings-by-permissions).

### A tool that cannot run now is not listed

A service's `affordances` say when the operation is possible. A condition written
as a callable is answered against the pool's seeds — `user`, `progress`, the
call's `call_context` and any seed the server registers with
[`pool_seeds=`](#pool-seeds) — and never against the call's arguments. So when a
condition is unmet **every** call of the tool is refused, whatever the client
sends. `tools/list` therefore asks each such condition, through the kernel's
`unmet_operation_affordance`, and leaves the tool out while any is unmet:

```python
from django_service_specs import Affordance, ServiceSpec

server.register_tool(
    ServiceSpec(
        service=close_period,
        affordances=[
            Affordance(
                code="books_closed",
                reason="The books for this period are already closed.",
                when=lambda user: user.ledger.is_open,
            ),
        ],
        permissions=[IsBookkeeper()],
    ),
    name="ledger.close_period",
    description="Close the current accounting period.",
)
```

- **Asked on every `tools/list`, with the user a call would use.** A condition
  reads the transport's request from its `call_context`, as a call's condition
  does: the transport's own `HttpRequest`, carrying the token as `mcp_token`,
  with `action` the tool's name. It sees the token's user. It is not gated by
  `FILTER_LISTINGS_BY_PERMISSIONS`: that setting is off by default because a
  permission may read arguments that do not exist at list time, and a condition
  answered against the seeds cannot read arguments at all.
- **A condition on the row is never asked here.** One written as an ORM expression
  (`when=~Q(status="shipped")`) has no row at list time; it is skipped without a
  query and goes on being answered per object at the call. A tool declaring only
  those is always listed.
- **Declaring nothing costs nothing.** A pool is built only for a listed tool that
  declares a condition on the operation, from that tool's own call context, as
  its call builds one, so each registered seed's resolver runs once per such
  tool per listing. Each condition runs once per listing for its tool, so keep
  them and the seeds cheap. One that raises fails the listing, as it would fail
  the call.
- **Selector tools are never left out.** A `SelectorSpec`'s `affordances` names
  *other* operations to project onto its rows; it is not a condition on the read.
- **Resources and prompts are unaffected.** A resource is a read, and a prompt
  holds no spec for an affordance to sit on.
- **The listing is advisory; the call is the authority.** `tools/call` looks the
  tool up by name without consulting any listing, so a client holding an older
  one gets the refusal it would have got anyway: an `isError` result carrying the
  affordance's `code` (see [Dispatch flow](#dispatch-flow)), not an unknown tool.

**Keeping clients current.** Nothing is cached on the server — each `tools/list`
asks again — so the question is when a client asks:

- **A condition that flips on an event** (an admin closes the books): announce it
  once the change has committed, and a client re-lists.

    ```python
    from asgiref.sync import async_to_sync
    from django.db import transaction
    from django_spec_mcp import NotificationKind

    transaction.on_commit(
        lambda: async_to_sync(server.notify_list_changed)(NotificationKind.TOOLS_LIST_CHANGED)
    )
    ```

    This reaches a client only when a
    [subscription broker](#server-pushed-notifications-subscriptions) is configured
    and the client speaks the modern era, the same two conditions `listChanged` is
    advertised on, and has opted in to `toolsListChanged` on its subscription.
    Without a broker it is a no-op, so it is safe to call unconditionally.

- **A condition that flips with the clock** (a period ends at midnight): nothing
  announces it. The client sees it on its next `tools/list`, which one honouring
  the catalog's `ttlMs` may put off for up to `CATALOG_CACHE_TTL_MS`; a call made
  in between is refused with the code.

A listing that asked any condition is served with `cacheScope: private`, because
it was answered for this caller and is no longer identical for every caller.

**Saying why a tool is missing.** A listing only leaves a tool out; nothing on the
wire says which tools were left out or why. An in-process consumer can ask:
`MCPServer.unavailable_tools` names each tool the listing omits with the
`Affordance` that omitted it, from the same pass, so a model can be told the
operation exists and why it cannot run now rather than guessing (see
[Full in-process transport](#full-in-process-transport-acall_tool-list_tools)).

### Tool annotations

Every tool advertises the MCP-standard `ToolAnnotations` hints, derived
from what the server already knows about the tool's mutation profile —
so downstream clients get correct `readOnlyHint` / `destructiveHint`
without a hand-set flag:

- **Selector tools** are reads → `{"readOnlyHint": true}`.
- **Service tools** are mutations → `{"readOnlyHint": false,
  "destructiveHint": true}`.

`destructiveHint` / `idempotentHint` are spec-meaningful only when
`readOnlyHint` is false, so a read-only tool emits neither.

A service tool also reads its spec's declared `ServiceSpec.idempotent`:
`idempotent=True` advertises `"idempotentHint": true`, a declared `False`
advertises `false`, and the default `None` (nothing declared) leaves the hint
off, which a client reads as the MCP default of `false`.

Pass `annotations=` at registration to override or extend the derived hints —
the explicit values win:

```python
server.register_tool(
    mark_paid_spec,
    name="invoices.mark_paid",
    description="Mark an invoice as paid.",
    # A non-destructive mutation. Its idempotency is declared on the spec
    # (ServiceSpec(idempotent=True)), so it needs no hint here:
    annotations={"destructiveHint": False},
)
```

The merged bundle lands on `binding.annotations` and on the `tools/list`
wire payload.

### Generic `_meta`

Separate from `annotations` — which is a closed, spec-defined set of
client hints — the base protocol gives most wire objects a free-form
`_meta` object. It is the extension namespace: each protocol extension
owns a top-level key inside it, and a server may add its own.

Pass `meta=` at any registration surface (`register_tool`, a `register_tools`
override, `register_resource`, `register_ui_resource` and `register_prompt`):

```python
server.register_tool(
    list_invoices_spec,
    name="invoices.list",
    description="List invoices.",
    meta={"example.com/panel": {"href": "panel://invoices"}},
)
```

The bundle lands on `binding.meta` and is emitted verbatim under the
`"_meta"` key of the binding's listing entry — `tools/list`,
`resources/list`, `resources/templates/list`, `prompts/list` — and, for a
resource, on the `contents` block `resources/read` returns. It is
omitted entirely when empty.

Nothing here validates, reserves, or rewrites a key: the whole point of
`_meta` is that its contents are opaque to the transport. On the
`tools/call` result envelope `_meta` is per-call rather than per-binding,
so it is a `build_tool_result(..., meta=...)` argument instead of
something sourced from the binding.

## Transport-neutral invocation: `call_tool`

`server.call_tool(name, arguments, *, user, request=None)` invokes a
registered tool **off the HTTP / JSON-RPC path** and returns the same
`ToolResult` the wire handlers build. An in-process consumer — a bridge, an
agent's toolset, a management command — uses it instead of re-implementing
dispatch:

```python
result = server.call_tool("invoices.create", {"number": "A-1"}, user=request.user)
result.structured_content  # the rendered payload
```

An agent is the common case;
[Connect a Pydantic-AI agent](recipes/pydantic-ai-client.md) covers driving
this server from one.

It runs the kernel's `dispatch`, as the wire does, so the spec-execution core
(the spec's permissions, the argument check, the target lookup, the service or
selector run, queryset shaping including `filter_set`, the retrieve
nullability contract, pagination and the presenter) is the one every route
shares rather than one reproduced here. The spec's `permissions` are asked of
`user` before the arguments are checked, as on the wire, so a caller they
refuse is told that rather than which argument it left out.

What it does **not** apply is the transport around the spec: there is no token
in process, so the `scopes=` gate is not judged, and no rate limit is charged.
Refused input (an undeclared argument under `REJECT`, a missing or invalid
one), a service raising `ServiceValidationError` / `ServiceError`, a missing
row and a presenter refusing a shape the caller sent come back as the `isError`
results the wire serves. A denied permission raises the kernel's `NotPermitted`
or `PrincipalUnavailable`, for the caller to map to its own wire. A service
raising `AdditionalInputRequired` is a `service_error` result here, since there
is no further round to ask in.

**The request.** `request=` is the caller's own Django request when there is
one, often the very request a view is serving, so the call never writes its
user to it. The call works on a copy whose `user` is the `user=` the call runs
as: a view handing its own request keeps serving as its own user after the
call, and two concurrent calls sharing one request each see their own. Apart
from the user, the copy is the handed request for everything a call reads from
it: the session, the body, the stream and the parsed `GET`, `POST` and `FILES`
are the caller's, so a body a spec reads is read for the caller, a stream it
drains is drained for the caller, and a body no spec reads stays unread for the
view. Every other attribute starts as the very object the handed request holds:
one a spec rebinds or deletes on the copy changes the copy alone, and one the
caller sets once the call has its copy is not seen by it, but an object a spec
mutates through one (`META`, the session, the message storage) is the caller's.
Upload handlers Django creates during the call are bound to the handed request,
so a handler reading `request.user` reads the caller's. The copy is an
instance of a subclass of the handed request's class, so `isinstance` holds
where `type(request) is WSGIRequest` does not, and it does not pickle. With no
request handed, a minimal `POST` `HttpRequest`
bearing `user=` is synthesised. The spec finds the copy, or the synthesised
request, as `call_context["request"]`, with no `mcp_token` on it, even when
the handed request carried one: `call_tool` has no token.

### Full in-process transport: `acall_tool` / `list_tools`

`call_tool` is the spec **core**. When an in-process consumer needs the *whole*
transport — exactly what a remote MCP client sees — `MCPServer` exposes two
async-friendly siblings that route through the same wire handlers:

```python
page = server.list_tools(user=request.user, request=request)  # one tools/list page
page["tools"]  # the inputSchema per tool
page["nextCursor"]  # pass back to list_tools(cursor, ...) to paginate

result = await server.acall_tool("invoices.list", {"page": 1}, user=request.user, request=request)
result["structuredContent"]  # the wire's result payload (dict, not ToolResult)
```

- `list_tools(cursor=None, *, user, request=None, scopes=None)` returns one page
  of the tool catalog with the same `inputSchema` (a paginated tool's `page` and
  `limit` and the `additionalProperties` policy included), the same
  `FILTER_LISTINGS_BY_PERMISSIONS` per-caller filter, the same
  [availability filter](#a-tool-that-cannot-run-now-is-not-listed), and the same
  opaque-cursor pagination the HTTP transport uses. `alist_tools` is its twin for
  an event loop, running the listing in Django's thread-sensitive executor.
- `acall_tool(name, arguments=None, *, user, request=None, scopes=None)` invokes
  a tool with the **full** transport applied: the `scopes=` gate and the rate
  limits — everything `call_tool` deliberately omits. It
  returns the wire's `dict` payload (`content` / `structuredContent` /
  `isError`) or a `JsonRpcError` for a protocol fault (unknown tool, malformed
  `arguments` shape, denied permission).

Both build the call context internally from `user` + `request`, handling a
handed request as `call_tool` does: a copy bearing the call's user, or a
minimal request synthesised when `request` is `None`. `acall_tool` carries a
token built from `user=` and `scopes=` on that request as `mcp_token`, so a
tool gated by `scopes=` is invokable in-process just as it is on the wire.
`JsonRpcError` and `JsonRpcErrorCode` are re-exported from the package root so
a consumer can branch on faults. This is the surface an in-process MCP bridge
consumes to run this server's tools with HTTP-equivalent semantics.

**A consumer that lists once and follows availability per step.** An agent loop
offers its model tools on every step, and whether a tool can run may change
between steps. Re-listing each step would rebuild every schema to learn a few
booleans, so the transport splits the two:

```python
page = server.list_tools(user=request.user, request=request, include_unavailable=True)
# ... build the model's tool definitions from page["tools"] once, then each step:
missing = await server.aunavailable_tools(user=request.user, request=request)
offered = [tool for tool in definitions if tool.name not in missing]
for name, affordance in missing.items():
    ...  # tell the model: `name` exists but cannot run now, because affordance.reason
```

- `list_tools(..., include_unavailable=True)` (and `alist_tools`) lists every tool
  the caller may see and asks no condition, so a tool that is unavailable when the
  consumer lists still has a definition when it becomes available. The listing
  asked nothing about the caller, so its `cacheScope` is decided as if no tool
  declared a condition. The wire has no such parameter.
- `unavailable_tools(*, user, request=None, scopes=None)` maps each tool a plain
  `list_tools` would leave out to the `Affordance` that left it out — its `code`
  and its `reason`, the sentence written for people and models alike. It is the
  same pass over the same tools, so a name is reported exactly when the listing
  omits it: never an `always_listed` tool, never one
  `FILTER_LISTINGS_BY_PERMISSIONS` hides from this caller. It asks again on every call; nothing is remembered.
- `aunavailable_tools` is its twin for an event loop. A server none of whose tools
  declares a condition on the operation answers `{}` without leaving the loop,
  since that is decided from the declarations alone and a consumer asks every
  step.

Like the listing, the answer is advisory: `acall_tool` still enforces every
condition, so a tool that became unavailable after the step began is refused with
its `code`.

## Documenting tools

A tool's description and its argument descriptions are the entire contract a
model has to work from. Both have a channel; neither is filled in for you.

### The tool description

`description=` on any `register_*` call. There is **no docstring fallback** for
spec registration — a docstring is written for the next developer, not for a
model choosing between tools, so promoting one silently would ship prose nobody
reviewed for that audience.

Registering without one emits `UndescribedToolWarning`, and
`SPEC_MCP["REQUIRE_TOOL_DESCRIPTIONS"] = True` turns that into an
`ImproperlyConfigured`. This mirrors `REQUIRE_TOOL_PERMISSIONS`: two properties
are equally required for a tool to be usable — something must gate the call, and
something must say what the call does.

### Per-argument descriptions

Do **not** restate argument meaning in the tool description. A `Parameter`'s
`help` becomes its property's `description` in the `inputSchema`, for a read and
a shape alike:

```python
from django_service_specs import Parameter, Parameters, SelectorKind, SelectorSpec

documents_spec = SelectorSpec(
    kind=SelectorKind.LIST,
    selector=list_loan_documents,
    reads=Parameters(
        [
            Parameter(
                "loan_pk",
                "string",
                required=True,
                help="Primary key of the *loan*, not the borrower.",
            )
        ]
    ),
    permissions=[IsLoanOfficer()],
)
server.register_tool(
    documents_spec,
    name="list_loan_documents",
    description="List the documents filed against a loan.",
)
```

Duplicated prose is where
descriptions get longest and go stale, so an argument whose name doesn't match
the entity it identifies belongs in its `help`, written once.

### What the server writes for you: `conventions=` { #agent-conventions }

A few sentences a model reads are not in any registration, because they explain
how this server behaves rather than what one tool does. The server writes them,
and `MCPServer(conventions=...)` changes the words. It takes an
[`AgentConventions`][django_spec_mcp.schema.types.agent_conventions.AgentConventions],
per server, so two servers in one project can speak to different readers:

```python
from django_spec_mcp import AgentConventions, MCPServer

server = MCPServer(
    name="invoicing",
    conventions=AgentConventions(
        handle_line="Pass ids to other tools; never show them to the user.",
    ),
)
```

Each field is one sentence, and lands in exactly these places:

| Field | Where it appears | `None` |
| --- | --- | --- |
| `handle_field_description` | The `outputSchema` description of a handle field that declares none of its own ([Hide plumbing from the model](recipes/agent-audience.md)). | The field is left undescribed. |
| `handle_line` | The line appended to the description of a tool whose output carries a handle. | The line is dropped, with its ``Identify records by `<label>`.`` prefix. |
| `query_param_on_pages` | The end of a shape's description on a paged tool, and the end of the `isError` message when a shape the caller sent is refused while rendering ([On a paged tool](#query-param-per-item)). | Dropped from both. |
| `missing_arguments` | The message of a call refused for leaving out an argument the tool requires, worded with every name the kernel's `InvalidArguments.missing` carries, beside the kernel's per-field detail ([Which parameters a client is asked for](#selector-requiredness)). | Refused with `ImproperlyConfigured`, as is an empty or whitespace-only string: it is the result's whole message. |

`AgentConventions()` is the default, and a server given none uses it. Change one
field and the others keep the package's wording, so a later correction to a
sentence you did not override still reaches you.

The server still decides **whether** a sentence appears: a tool with no handle
gets no handle line, and an unpaginated tool gets no scope sentence, whatever the
conventions say. A field changes **what it says**. The wording reaches every
route a server answers on: both transports in both eras, `call_tool`,
`acall_tool`, `list_tools`, and a task's worker.

Every field is a `str.format` template, rendered wherever it lands whether or
not it has a placeholder, so a literal brace is written twice, `{{` or `}}`, in
every field alike: `handle_line="Ids look like {{this}}."` reaches the model as
`Ids look like {this}.`. Only `missing_arguments` has a placeholder, `{names}`,
filled with the missing names sorted, each in backticks, joined with `", "`;
the default reads ``Missing required argument(s): `pk`.``. A field the server
could not render raises `ImproperlyConfigured` naming it when the conventions
are built, rather than inside the first call that reaches it: a placeholder
the field does not accept, a single brace, a format spec or conversion its
value cannot take (`{names:q}`, `{names!z}`), or a value that is neither a
string nor `None`. This is the rule the agent toolset in `django-spec-agents`
applies to its own `AgentConventions`, so a template one transport accepts reads
the same on the other.

Two of these sentences state facts: that a selection applies to each item and
never to the envelope, and that the envelope's keys are `items`, `page`,
`totalPages` and `hasNext`. Wording you supply owns keeping those facts true.

The default handle description and missing-argument message are word for word
the ones the agent toolset in `django-spec-agents` writes for the same specs, and the handle line gives the same advice as its instructions,
so a spec served both ways tells the model the same thing on both routes until
one side overrides it.

## Tools vs resources

| | Tools | Resources |
| --- | --- | --- |
| MCP capability | `tools` | `resources` |
| Mutation? | Yes (services) | No (selectors) |
| Addressable? | By name (`invoices.create`) | By URI (`invoices://42`) |
| Dispatched via | `tools/call` | `resources/read` |
| Backed by | a `ServiceSpec` or a `SelectorSpec` | a `RETRIEVE` `SelectorSpec` |
| Schema advertised | `inputSchema` + optional `outputSchema` | `mimeType` |
| Body | the presented result | the presented row, encoded per `encoding=` |

Tools are imperative (the client decides when to call them and supplies
arguments). Resources are read-only and addressable by URI; they have a stable
identifier and the client can rely on the same URI returning a consistent shape
over time.

Both run the whole spec through the kernel's `dispatch`. What a resource
refuses at registration, a `LIST` among it, is in
[What a resource carries](#what-a-resource-cannot-carry-and-why-registration-refuses-it).

## URI templates

Resource URIs follow a small subset of [RFC 6570](https://www.rfc-editor.org/rfc/rfc6570).
Each `{var}` placeholder is an argument of the read, so it names one of the
spec's reads (or shapes), and the selector receives it under that name:

```python
from django_service_specs import Parameter, Parameters, SelectorKind, SelectorSpec

server.register_resource(
    SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=get_invoice,  # def get_invoice(*, pk): ...
        reads=Parameters([Parameter("pk", "integer", required=True)]),
        presenter=InvoicePresenter(),
        permissions=[IsBillingStaff()],
    ),
    name="invoice",
    uri_template="invoices://{pk}",
)
```

A variable always arrives as a string, and the kernel's argument check coerces
it to the read's type, so `invoices://7` reaches `get_invoice` as `pk=7` and
`invoices://x` is answered `-32602` "Invalid arguments".

Concrete URIs (no placeholders) appear in `resources/list`; templated ones
appear in `resources/templates/list` so clients can fill them in.

## Non-text tool results

A tool result's `content` array is the same content vocabulary — text, image,
audio, a link to a resource, or an embedded resource. A binding *declares* what
it returns rather than having it guessed, because a
base64 string and a text body are indistinguishable by inspection:

```python
from django_service_specs import ServiceSpec

from django_spec_mcp import ToolContentKind

server.register_tool(
    ServiceSpec(service=render_chart, atomic=False, permissions=[IsAnalyst()]),  # returns bytes
    name="charts.render",
    description="Render a chart as a PNG.",
    content_kind=ToolContentKind.IMAGE,
    content_mime_type="image/png",
)
```

`ToolContentKind.TEXT` (the default) renders the result as JSON and mirrors it
in `structuredContent`. `IMAGE` and `AUDIO` take
the media itself — `bytes`, or a `str` already in base64 — and carry **no**
`structuredContent` or `outputSchema`, since neither can describe a PNG;
registering one alongside those is refused rather than ignored.

`ToolContentKind.RESOURCE_LINK` is usually the better answer for anything
large. The tool returns a mapping with `uri` and `name` (or a list of them),
each becoming a link the client can read through `resources/read` — so no bytes
ride on the tool-result path and the client fetches only what it decides it
wants. `structuredContent` is kept for this kind: the links are ordinary JSON.

```python
server.register_tool(
    SelectorSpec(kind=SelectorKind.LIST, selector=list_attachments, permissions=[IsBillingStaff()]),
    name="invoices.attachments",
    description="List an invoice's attachments.",
    content_kind=ToolContentKind.RESOURCE_LINK,
)
# → [{"uri": "invoices://1.pdf", "name": "Invoice 1", "mimeType": "application/pdf"}, ...]
```

A payload that doesn't match the declared kind comes back as an `isError`
result naming the binding — the same treatment an oversized result or a missed
deadline gets, so the client always has a well-formed response to read.

## Argument completion

Clients offer autocompletion while a user fills in a prompt argument or a URI
template variable. Register a completer per argument and this server answers
`completion/complete`:

```python
server.register_prompt(
    name="code_review",
    render=review_prompt,
    arguments=[PromptArgument(name="language")],
    completions={
        "language": lambda value: Language.objects.filter(name__startswith=value).values_list(
            "name", flat=True
        )
    },
)
```

Completers are dispatched through the same kwarg-pool machinery as everything
else, so declare whichever of `value` (the text typed so far), `arguments`
(siblings the client has already resolved, also spread by name), `request` and
`user` you need. `request` is the transport's Django `HttpRequest`, with the
caller's token at `request.mcp_token`, and `user` is the token's principal,
`AnonymousUser` when the token names no one, never `None`. Return any iterable — a list, a generator, a queryset: the
handler slices it to the spec's cap of 100 and sets `hasMore` rather than
draining it, so a queryset reads 101 rows, not the table.

Resource templates work the same way, keyed by the `{variable}` name:

```python
server.register_resource(
    invoice_spec,
    name="invoice",
    uri_template="invoices://{pk}",
    completions={"pk": recent_invoice_ids},
)
```

A completer keyed to an argument the binding doesn't have is refused at
registration — the failure mode otherwise is an empty dropdown with nothing in
the logs.

!!! warning "A completion is a read"

    Completion runs the binding's gate and `rate_limits` before the completer:
    for a resource, the spec's own checks, asked with no arguments, and its
    `scopes=`; for a prompt, its `scopes=` and its `permissions=`. Without that, a resource a
    caller may not read would still answer "which ids exist?" one keystroke at
    a time.

The `completions` capability is advertised only when something is actually
completable, which is the same rule `tools`, `resources` and `prompts` follow:
a capability is a promise, and a server that declares one and then answers
`-32601` is worse off than one that never declared it.

## Icons

Tools, resources, resource templates, prompts and the server itself can carry
display icons. This package only emits them — fetching, sanitising and
rendering are the client's problem, and the spec puts a long list of MUSTs on
that side.

```python
server.register_tool(
    spec,
    name="invoices.create",
    description="Create an invoice.",
    icons=(Icon(src="https://cdn.example.com/invoice.png", sizes=("48x48",)),),
)

MCPServer(name="billing", website_url="https://example.com", icons=(...,))
```

`src` must be `https:` or a `data:` URI — clients are required to reject
anything else, so a `http://localhost/...` icon is refused at registration
rather than shipped as an icon nobody will ever see. The server's own identity
(`title`, `description`, `websiteUrl`, `icons`) can also come from the
`SERVER_INFO` setting.

## Streaming progress

A long-running tool can report how far it has got, and the client sees it as it
happens. Declare `progress` on the service or selector and call it:

```python
def export_invoices(*, data, progress):
    rows = list(build_rows(data))
    for index, row in enumerate(rows):
        write(row)
        progress(
            index + 1, total=len(rows), message="writing rows", meta={"com.example/file": data.path}
        )
```

Nothing about the registration changes — `progress` is one of the kernel's
reserved pool seeds, so it arrives like `user` does, and the same service runs
unchanged on a transport where nobody is listening: the kernel hands it its
no-op `null_progress` there.

**The client opts in** by putting a `progressToken` in the request's `_meta`:

```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
 "params": {"name": "invoices.export", "arguments": {},
            "_meta": {"progressToken": "abc123"}}}
```

That token is the *only* trigger. With it, the response becomes
`text/event-stream` carrying `notifications/progress` frames followed by the
result; without it, a single JSON object as before — a stream whose only event
is the final response costs a connection and buys nothing.

**Era-independent.** `_meta.progressToken` sits in the same place in
`2025-11-25` and `2026-07-28`, so a legacy client gets streamed progress on
exactly the same terms as a modern one.

**ASGI only, for *streamed* progress.** A sync WSGI view cannot yield while
its dispatch is still running, so `server.urls` keeps answering
`application/json`. That stays spec-legal — a single JSON object is always
permitted. Use `server.async_urls` if you want progress on the wire mid-call.

The same service reports fine under WSGI when it runs as a
[task](#progress-from-inside-a-task): there the reports go to the task record
rather than to a stream, and the client polls for them.

### What the server does with your reports

| | |
|---|---|
| `progress` must increase | A non-increasing report is **dropped**. The spec makes increase a MUST, so forwarding one would put this server in violation on the service's behalf. |
| Frames are capped | `MAX_PROGRESS_NOTIFICATIONS` (default 1000) per request. The spec asks both parties to rate-limit; a per-row reporter over a large table is a flood. Past the cap reports are dropped — **the dispatch is untouched and the result still arrives**. |
| `meta` rides in `_meta` | Structured detail goes where the protocol puts extension data, so `message` stays prose. Namespace your keys. |
| Closing the stream cancels | Client disconnect *is* the cancellation signal in `2026-07-28`. It cancels the await, not the work — a thread parked in a driver's socket read is not interruptible by asyncio, the same caveat `DISPATCH_TIMEOUT` carries. |

!!! warning "Permissions are checked before the stream opens"

    A streaming response commits its HTTP status before the handler runs, so a
    permission denial found inside could only ride as an in-stream error inside
    a `200` — losing the `403` the authorization spec makes normative. The
    permission stack therefore runs once at the transport before the stream is
    opened, so a denied call still gets `403` and a `WWW-Authenticate`
    challenge. That stack is the tool's `scopes=` gate and the spec's own
    checks, through the kernel's `check_call`; the handler asks the spec's
    checks once more before it charges the call. Rate limits stay inside the
    handler: consuming one is not idempotent, and a rate-limit rejection was
    already a `200` with the detail in the body.

## Server-pushed notifications: subscriptions

A client that wants to know when something changes opens `subscriptions/listen`
— a POST whose response stream stays open — and names exactly what it wants:

```json
{"method": "subscriptions/listen",
 "params": {"notifications": {
     "resourceSubscriptions": ["invoices://42"],
     "toolsListChanged": true}}}
```

**Every type is opt-in, and that is a MUST**: the server must not send a
notification type the client did not ask for. So an absent field is a refusal,
not a default — and a subscription that was granted *nothing* is acknowledged
and then **closed**, rather than held open to deliver silence.

The first frame is always `notifications/subscriptions/acknowledged`, carrying
the subset the server **agreed to** honour. That is where a client learns it
will not get something — a resource it may not read, a `promptsListChanged` on a
server with no prompts — instead of waiting for an event that was never coming.
Every frame after it carries the subscription id in `_meta`, so one client can
run several subscriptions and tell them apart.

### Wiring it up

```python
from redis.asyncio import Redis
from django_spec_mcp.subscriptions.redis_subscription_broker import (
    RedisSubscriptionBroker,
)

server = MCPServer(
    name="invoices",
    subscription_broker=RedisSubscriptionBroker(Redis.from_url("redis://…")),
)
```

There is **no default broker**, deliberately. A server that quietly got an
in-process one would advertise support and then deliver nothing the moment a
second worker existed.

!!! danger "The in-memory broker is single-worker only"

    `InMemorySubscriptionBroker` is for development and tests. The write that
    triggers a notification lands on whichever worker served that request, and
    the subscriber's stream is parked on a different one — so past one worker it
    delivers to nobody, and the failure looks exactly like "the resource never
    changed". `RedisSubscriptionBroker` (in the `[redis]` extra) is the
    deployable one.

**A subscription occupies a worker for as long as it is open.** One parked
ASGI task per subscriber — inherent to the wire format, since this method exists
to replace the old GET endpoint. Two settings bound it:

| | |
|---|---|
| `SUBSCRIPTION_MAX_SECONDS` (1 h) | The server closes the stream gracefully and the client re-subscribes. Also the **re-authorization interval**: a subscription's permissions are checked once, when it opens, so this is what stops a principal whose access was revoked from receiving change signals indefinitely. |
| `MAX_CONCURRENT_SUBSCRIPTIONS` (100) | Per worker. Without it an authenticated caller can exhaust the pool by opening streams in a loop. Past the cap a new subscription is refused with `503` / `-32603` rather than queued. |

### Watching a task instead of polling it

A client that holds a task id can subscribe to it and stop calling `tasks/get`:

```json
{"method": "subscriptions/listen",
 "params": {"notifications": {"taskIds": ["786512e2-…"]}}}
```

Every status change pushes `notifications/tasks` carrying the **whole task** —
identical to what `tasks/get` would have returned at that moment — so a missed
notification costs nothing and polling stays genuinely optional rather than a
fallback you have to implement anyway.

A task is watchable only by the principal that created it: its status is as
revealing as its result, since knowing someone else's export finished is knowing
they ran one.

**One exception to "refused entries are dropped":** a client asking for
`taskIds` without declaring the `io.modelcontextprotocol/tasks` extension gets a
JSON-RPC error, not a quiet omission. The spec requires it, and it is not an
existence oracle — the error turns on what the *client* declared, not on
anything about the tasks it named.

`notifications/progress` and `notifications/message` are **MUST NOT** on this
stream. Progress for a task is the task's own status; the progress channel
belongs to the request-scoped stream, which a task by definition outlived.

### Publishing a change

**Declare it on the tool that does the writing** — the server is already in the
call path, so this is the trigger that needs no discipline to remember:

```python
server.register_tool(
    create_invoice_spec,
    name="invoices.create",
    description="Create an invoice.",
    invalidates=("invoices://{pk}", "invoices://"),
)
```

Templates use the same `{var}` syntax as a resource's `uri_template`, rendered
against the result merged with the call's arguments (**result wins** — after a
write it is authoritative; the arguments cover a delete, whose result carries
nothing). The arguments are the ones the tool ran with: on a retry, its
`inputResponses` merged over the arguments as sent, so an answer naming another
`{project_pk}` announces the project the service ran on. Publishing happens **after the transaction commits**, and a call that
came back `isError` publishes nothing.

**Its boundary is real, and is why the explicit trigger exists too.** It fires
for calls that go through this server and for nothing else — a management
command, a Celery job or an admin edit changes the same rows and announces
nothing. For those:

```python
from django.db import transaction

transaction.on_commit(
    lambda: async_to_sync(server.notify_resource_updated)(f"invoices://{invoice.pk}")
)
```

**Publish after the transaction commits.** Inside `transaction.atomic()` you
would be announcing a change that may still roll back, and a subscriber that
re-reads immediately sees the old value — worse than no notification at all.
`invalidates=` does this for you; `notify_resource_updated` is yours to place.

**Matching is exact, not by prefix.** Publish the concrete URI *and* the
collection URI if you want watchers of the collection to hear about it. A prefix
rule would match `invoices://1` against `invoices://11` and would miss a
tenant-scoped scheme entirely, so the publisher says what it means instead.

Calling `notify_resource_updated` on a server with no broker is a no-op rather
than an error, which is what makes it safe to call unconditionally from a
service.

### Who may watch what

Subscribing to a resource requires the same permission as reading it. Otherwise
a subscription would be a side channel around `resources/read`: a caller denied
the body could still learn every time it changed, and *when* something changes
is often the more sensitive signal. For a resource that is the spec's own
permissions as well as its `scopes=`. A task may be watched only by the principal
that created it.

A refused entry is **dropped from the acknowledgement**, not turned into an
error — matching the spec's own handling of unsupported types, and avoiding an
oracle that would let a caller tell "no such resource" from "not yours".

## Long-running work: tasks

Streaming progress keeps the client informed while it waits. **Tasks remove the
waiting.** A task-eligible tool answers `tools/call` with a durable handle
instead of a result; the work happens in a queue worker, and the client polls
`tasks/get` until the status is terminal.

This is the `io.modelcontextprotocol/tasks` extension, and it is **modern-era
only** — the client declares support on every request, which a legacy client has
no way to do.

### Wiring it up

Two pieces. An **executor** says where work goes, and a **store** is where tasks
live between the request that created one and the worker that finishes it:

```python
from celery import shared_task


@shared_task
def run_mcp_task(task_id: str) -> None:
    server.run_task(task_id)


class CeleryExecutor:
    def enqueue(self, task_id: str) -> None:
        run_mcp_task.delay(task_id)


server = MCPServer(name="invoices", task_executor=CeleryExecutor())
```

That is the whole seam: one method taking one string. Nothing in this package
imports Celery, and an RQ or Dramatiq call, or a `ThreadPoolExecutor.submit` in
a test, satisfies it just as well. Passing `task_executor=` also builds a
`DjangoCacheTaskStore` namespaced to the server, so the common case is one
argument; pass `task_store=` to override it.

!!! danger "`InMemoryTaskStore` is not deployable"

    Unlike an in-memory *session* store, which is merely restart-fragile, an
    in-memory *task* store is broken by design: the web worker that creates a
    task and the worker that finishes it are different processes. The result is
    written into a dict the web process cannot see, and every poll answers
    "unknown task" until the client gives up. It fails silently and looks like a
    hung job. Use it for tests and a single-process `runserver`.

### Opting a tool in

The extension makes the **server** the sole decider — a client cannot ask for a
task — so the choice lives on the binding, next to every other per-tool knob:

```python
from django_service_specs import ServiceSpec, Unrestricted

from django_spec_mcp import TaskPolicy

server.register_tool(
    ServiceSpec(service=generate_report, permissions=[Unrestricted()]),
    name="reports.generate",
    task_policy=TaskPolicy.OPTIONAL,
)
```

| `task_policy` | A client that declared the extension | A client that did not |
|---|---|---|
| `FORBIDDEN` (default) | runs inline | runs inline |
| `OPTIONAL` | gets a task handle | runs inline |
| `REQUIRED` | gets a task handle | `-32021`, missing capability |

`FORBIDDEN` is the default, so **a tool that names no policy always runs
inline**. Reach for `OPTIONAL` when a tool is slow but can
still finish inside a request, and `REQUIRED` when it genuinely cannot — where
running it inline would only hit the dispatch deadline.

A service tool and a selector tool take `task_policy=` alike. A caller the
spec's own `permissions` or the tool's `scopes=` refuse is answered `-32006`
and no task is created, and the tool's rate limits are charged once, when the
task is created. The worker runs the call, under either policy, through the
same `dispatch` a direct call takes, under the server's `pool_seeds` and
`conventions`, with the spec's permissions asked again.

### What a client sees

```json
{"resultType": "task", "taskId": "…", "status": "working",
 "ttlMs": 86400000, "pollIntervalMs": 5000}
```

Then `tasks/get` until `completed` / `failed` / `cancelled`, honouring
`pollIntervalMs`. `tasks/cancel` signals intent to stop; `tasks/update` answers
a task that is parked on `input_required`.

**`Mcp-Name` must carry the `taskId`** on all three methods — the extension
requires it so a gateway can route a follow-up to the instance holding the
task's state. A mismatch is `-32020`, exactly as for `tools/call`.

### Progress from inside a task

The same `progress` seed works, and the service body is unchanged:

```python
def export_invoices(*, data, progress):
    for n, row in enumerate(rows, 1):
        progress(n, total=len(rows), message="Exporting")
```

Run inline, that streams `notifications/progress`. Run as a task, there is no
connection to stream on — so the reports land on the task record instead, and
the client reads them from the `statusMessage` it was already polling:

```json
{"taskId": "…", "status": "working", "statusMessage": "Exporting (142/500)"}
```

**This is what makes `progress` worth declaring at all under the task
model.** Without it the seed is live-connection-only, and the operations most
in need of progress — the ones promoted to tasks *because* they run long — are
exactly the ones it would silently do nothing for. Nothing about the service,
the spec, or the registration distinguishes the two paths.

| | |
|---|---|
| `statusMessage` is the whole channel | The protocol `Task` has no numeric field. `progress` / `total` are kept on the record for logs and admin visibility, but a polling client only ever receives the rendered string. |
| `meta` is dropped | On the inline path it rides in the notification's `_meta`. A task has no notification and no free-form slot, so the argument is accepted and discarded rather than given a home no client could read. |
| No `notifications/tasks` per tick | That notification means *the status changed*; progress is movement inside `working`. Subscribers would get a firehose describing a task that has not moved. Poll at `pollIntervalMs`. |
| A finished task is never rewritten | A late report from a worker still unwinding cannot move `lastUpdatedAt` on a completed task or make a cancel look like it did not take. |
| A store that is down does not fail the work | The report describes the operation; it is not the operation. Write failures are swallowed. |

### Things worth knowing

| | |
|---|---|
| Permissions run **twice** | Once before the task is created, so a denied call never reaches the queue and still gets its `403`; once again in the worker, against the same rebuilt token. The task stores the caller's scopes for exactly this reason. |
| Rate limits run **once** | Consuming a quota is a side effect, so it is charged when the client asks and not again on replay. Charging both would halve every configured limit. |
| A tool error is `completed`, not `failed` | The spec is explicit. A `ServiceError` produces a well-formed result carrying `isError: true`, and that is a task that finished. `failed` means the task machinery broke. |
| There is no `tasks/list` | Deliberate in the spec: without sessions there is nothing to scope a listing by. Ids are high-entropy and ownership is checked on top — an id belonging to someone else answers exactly as one that never existed. |
| A queue that is down fails the *task* | The record is already durable when `enqueue` raises, so the handle comes back with `status: "failed"` and the reason in `statusMessage`. The client finds out through the channel it was already using. |
| Redelivery runs the work once | Queues deliver at least once; a task is claimed as the worker starts. Best-effort, not a lock — an idempotent service is still the right thing to write. |

## Asking the user: elicitation

Some calls cannot be decided by the arguments alone. "Delete everything matching
this filter" is safe at three rows and alarming at nine thousand, and the service
only finds out after it has looked.

A service says so by raising, and that is the whole of its involvement:

```python
from django_service_specs import AdditionalInputRequired


def delete_rows(*, data):
    doomed = rows_matching(data)
    if len(doomed) > 100 and not data["confirmed"]:
        raise AdditionalInputRequired(
            f"{len(doomed)} rows match. Confirm to proceed.",
            schema={"confirmed": {"type": "boolean"}},
        )
    ...
```

`confirmed` is an ordinary argument of the tool. The server turns
the raise into a question, the client puts it to the user, and the answer arrives
back as that ordinary argument — so the service reads it exactly as it would read
anything else a caller sent.

A tool admits an answer only under a name it takes from a client: a
parameter the spec declares, less every name the server fills for the call (a
`server_arguments` entry, a pool seed, a reflected `request` or `view` read, or a
key a typed `kwargs=` or `input_data=` provider declares, unless its type admits
`UnsetType`). A question for any other property is a `-32603` naming
the property and the names the tool does take, whether or not the client can be
asked, since the answer would come back as an argument the call refuses, drops
or replaces. The author finds out on the first call, not when a user's answer is
refused.

### What goes over the wire

```json
{"resultType": "input_required",
 "inputRequests": {"additionalInput": {
   "method": "elicitation/create",
   "params": {"mode": "form",
              "message": "9412 rows match. Confirm to proceed.",
              "requestedSchema": {"type": "object",
                                  "properties": {"confirmed": {"type": "boolean"}},
                                  "required": ["confirmed"]}}}},
 "requestState": "…"}
```

**This is a success, not an error.** `input_required` is a second legal shape
for a `tools/call` result, inside a `200`, and a client that treats a non-`complete`
`resultType` as a failure will never retry.

The client collects the input and **retries the original call** — a new request,
a new JSON-RPC id — carrying `inputResponses` and the `requestState` verbatim:

```json
{"name": "rows.delete", "arguments": {"count": 9412},
 "inputResponses": {"additionalInput": {"action": "accept",
                                        "content": {"confirmed": true}}},
 "requestState": "…"}
```

**Nothing is held between the two requests.** That is the point of the pattern
— it replaced server-initiated requests precisely so the retry can land on a
different process, behind a load balancer that knows nothing about the first one.
The service is not resumed; it **runs again from the top**, with the answer
present. A service that did irreversible non-transactional work before raising
will do it twice, which is a reason to raise early and to keep `atomic=True`.

**An answer can change an argument.** It is merged over the arguments as sent,
so it can name an argument the call sent or left out, the one a target lookup
reads included. The spec's checks are asked once, of the merged arguments,
before the rate limit is charged, the target is looked up or the service runs:
a check admitting only project `"7"` refuses `"8"` whether it was sent plainly
or answered over an admitted `"7"`, and admits `"7"` answered over a refused
`"8"`. A caller refused is not charged; a declined or cancelled answer is, as
any other call is. A value the tool's [`server_arguments`](#server-arguments)
bind is never the answer's to replace.

### `requestState` is attacker-controlled

It leaves the server, passes through the client, and comes back. So it is signed
(HMAC, via `django.core.signing`) and carries three things that are checked
before any of it is believed:

| Bound to | Rejects |
|---|---|
| the authenticated principal | a token that leaked and is presented by someone else |
| a digest of the original call | a confirmation given for a harmless call, replayed onto a destructive one |
| a timestamp (`INPUT_REQUEST_TTL_SECONDS`) | anything captured from a log or a proxy and used later |

All four failures — including a bad signature — answer identically: the state is
ignored and the user is asked again. Distinguishing them would turn the endpoint
into an oracle, and an honest client cannot use the distinction anyway, since it
is forbidden from looking inside the token.

Signed is not encrypted: a client can decode it. What is in there is the caller's
own principal id, a digest of the caller's own request, and the answers the user
at that client just typed. Form mode is documented for **non-sensitive** values
for exactly this reason — do not ask for a password.

### More than one question

`requestState` accumulates the answers, so a service that wants a confirmation
and then a reason works: the client sends only the latest round's response, and
the earlier answers ride in the token. `MAX_INPUT_ROUNDS` bounds it, so a service
whose condition an answer never clears fails instead of volleying the same
question at a user forever.

### Clients that cannot be asked

The spec forbids sending an elicitation to a client that did not declare the
capability. Rather than a protocol error, such a call gets an ordinary `isError`
result carrying the service's message **and the schema**:

```json
{"error": {"type": "input_required",
           "message": "9412 rows match. Confirm to proceed.",
           "requestedInput": {"confirmed": {"type": "boolean"}}}}
```

A model reading that can simply pass `confirmed: true` on its next call, which
is the same outcome by a shorter route. This is what a legacy-era client sees, a
URL-only client, and a task worker replaying a call with nobody at the other end.

Which of the two a real client gets is decided by its MCP SDK, not by anything
you configure — [Connect a Pydantic-AI agent](recipes/pydantic-ai-client.md)
names the versions where the question is asked and where it degrades.

### Boundaries

| | |
|---|---|
| Service tools only | A **selector** is a read — one that needs the user to decide something is a tool wearing the wrong registration. |
| Form mode only | The spec's other mode hands the user a URL to complete out of band. Nothing here knows how to mint one; a service that needs it has a redirect to build, not a schema to declare. |
| Top-level fields only | `requestedSchema` is a restricted subset: strings, numbers, booleans and enums, no nesting. A schema outside it raises `ImproperlyConfigured` at the moment it would have been sent, rather than shipping something the client must reject. |
| `tools/call` only | The spec also permits `input_required` on `prompts/get` and `resources/read`. Neither is implemented: a prompt render or a resource read that needs to stop and ask is not a shape this package has a caller for. |
| Sampling and roots | Not built. Both are **Deprecated** as of `2026-07-28`; elicitation is the only input worth asking for. |

## Protocol eras

This server speaks two revisions of MCP at once, on one endpoint, and picks
which by reading the request:

| | Legacy (`2025-11-25`, `2025-06-18`) | Modern (`2026-07-28`) |
|---|---|---|
| Opens with | `initialize` | nothing — every request stands alone |
| State | a session, in `Mcp-Session-Id` | none |
| Version | negotiated once | declared per request |
| Detected by | absence of the marker below | `_meta["io.modelcontextprotocol/protocolVersion"]` |
| Push notifications | none | `subscriptions/listen` |
| Tasks, elicitation | not offered | available |

A modern request carries its version, client identity and client capabilities
in `params._meta`, and mirrors selected body fields into headers so gateways
can route without parsing JSON:

```http
POST /mcp/ HTTP/1.1
Mcp-Protocol-Version: 2026-07-28
Mcp-Method: tools/call
Mcp-Name: invoices.create
```

Those headers are **validated against the body**. A mismatch is `400` with
JSON-RPC `-32020` — not pedantry: a load balancer that routes on the header
while the server executes the body is a confused deputy, and the check is what
closes it. `Mcp-Name` may arrive Base64-wrapped (`=?base64?…?=`) when the value
would not survive as a plain ASCII header; it is decoded before comparison.

Two more modern-only status codes matter, because clients use them to work out
which era they are talking to: an unknown method is `404` with a `-32601` body
(the body is what distinguishes this endpoint from a server that does not host
one), and an unsupported version is `400` with `-32022` listing what *is*
supported. `GET` and `DELETE` return `405` to a caller naming a modern
revision — the SSE stream and session termination were both removed there.

!!! note "Keeping legacy is a deliberate choice"

    Legacy clients have no fall-forward mechanism: drop the era and every
    client that has not migrated is stranded with nothing but an error string.
    The cost of carrying both is one branch at the transport edge — everything
    below it is era-agnostic, with two exceptions.

    **`resources/read`** answers a missing URI with `-32002` for a legacy caller
    and `-32602` for a modern one, because the revision that retired `-32002`
    also told clients to keep recognising it, so neither value is safe to send
    to both. A resource read whose URI variable fails the spec's argument
    check, by a reader the spec's checks admit, is answered `-32602` for both
    callers, with the kernel's detail under `data.detail` where a missing URI
    carries `data.uri`.

    **The advertised capabilities** follow the caller, not the server. A
    capability is a promise, and two of them can only be kept for a modern
    client: every push flag (`subscribe`, and the three `listChanged` fields)
    describes a notification that leaves through `subscriptions/listen`, and
    `extensions` is not a field on the legacy `ServerCapabilities` at all. So a
    legacy `initialize` is told about neither, however the server is configured
    — and `server/discover`, which both eras may call, answers according to the
    version the caller declared.

    **`resources/subscribe` is deliberately not implemented.** It is optional
    in `2025-11-25` and gone from `2026-07-28`, where the schema says
    `SubscriptionFilter.resourceSubscriptions` *"replaces the former `resources/subscribe`
    RPC"* — which this server does implement. Building the legacy RPC would mean
    a cross-process session→URI registry serving only the era being carried for
    compatibility rather than grown.

## Interactive views (MCP Apps)

A tool can declare an HTML view that an MCP **host** renders inline in the
chat, under the [MCP Apps](https://github.com/modelcontextprotocol/ext-apps)
extension. It layers over the base protocol this package already speaks, so
there is no protocol bump and no transport change.

The host/server split is the whole shape of it. This package **declares**:

```python
server.register_ui_resource(
    name="invoices_table",
    uri="ui://invoices/table.html",
    template_name="mcp/invoices_table.html",
    ui=UIResourceMeta(
        csp=UICsp(connect_domains=["https://api.example.com"]),
        prefers_border=True,
    ),
)
```

The **host** renders: it builds the sandboxed iframe and constructs and
enforces the CSP from what you declared. Neither is implemented here, and
neither should be.

The `ui/*` postMessage bridge is **not** on that list, and reading it as though
it were is the single most expensive misunderstanding this extension produces.
The bridge has two ends. The host runs its end; the *document* runs the other,
and the document is yours. That second end is a mandatory startup handshake
whose failure modes are all silent — see
[Writing the bridge yourself](#writing-the-bridge-yourself).

So the package ships the view end, inlined into a document it composes for you:

```python
server.register_ui_resource(
    name="invoices_table",
    uri="ui://invoices/table.html",
    body_template_name="mcp/invoices_table.html",  # markup only
)
```

`body_template_name=` takes a template holding the view's markup, styles and
scripts, and wraps it in a document carrying
[`build_app_document`][django_spec_mcp.ui.build_app_document.build_app_document]'s
shell and bridge. The other three sources — `template_name=`, `html=`,
`render=` — hand you a whole document to fill, bridge included.

A view is an ordinary resource — one URI namespace with your data resources,
listed in `resources/list`, and guardable with `permissions=` and `scopes=` — with three
things fixed for you: the `text/html;profile=mcp-app` mime type, `TEXT` body
encoding, and a `_meta` bundle under the extension's key. Give it exactly one
content source: `template_name=` (a Django template, the idiomatic choice),
`html=` (a literal document), or `render=` (a callable returning one, which
**must take no arguments** — one that declares a parameter is refused at
registration, because the read path would fill it from a pool carrying
`request` and `user`, and a view that varies by caller is exactly what the
exemption below assumes cannot exist).

Views are **unguarded by default**. The MCP session is already authenticated,
a view is a static asset rather than tenant data, and hosts may prefetch one
before any tool call. Pass `permissions=` or `scopes=` if your project wants otherwise.

!!! warning "Keep tenant data out of the view"

    Hosts may prefetch and cache a view, so it is a *shell* that hydrates
    itself at runtime from tool results — which is why the template renders
    with no context. This is a house rule, not a spec rule, and it is the one
    thing a Django author's instinct gets wrong: rendering the queryset into
    the template is normally the right answer, and here it would leak data
    across the host's cache.

A tool then points at the view, and the host renders that tool's result inside
it instead of showing raw JSON:

```python
server.register_tool(
    list_invoices_spec,
    name="list_invoices",
    description="List invoices.",
    ui=UIToolMeta(resource_uri="ui://invoices/table.html"),
)
```

The render payload is the `structuredContent` the tool **already emits** — no
second serialisation path — and a `tools/call` the view makes comes back
through the ordinary endpoint, inheriting your auth backend, the spec's
permissions, the tool's `scopes=` and its rate limits unchanged.

Three ways a link can be wrong all fail the same way at runtime — a view that
silently never renders — so all three are refused at registration:

| Mistake | Why it's caught |
| --- | --- |
| `resource_uri` names no view on this server | The host resolves it against the same server, so a typo reaches it as a dangling reference. Register the view **before** the tool that links to it. |
| The tool has `include_structured_content=False` | That *is* the render payload; the view would come up blank. Checked against the effective value, so a project that turned it off globally is caught too. |
| Both `ui=` and a `"ui"` key in `meta=` | Both write the same `_meta` key, so one would quietly overwrite the other. |

`visibility` declares who may call the tool — `UIVisibility.MODEL`, `APP`, or
both. It is **host-enforced**: a host is required not to offer the model a tool
whose visibility omits `MODEL`, which makes an `APP`-only tool a useful shape
for a fine-grained operation that exists to serve the view rather than the
conversation. This server declares the field and does not filter `tools/list`
on it — a client that doesn't implement the extension wouldn't honour the rule
anyway.

A client advertises Apps support as `capabilities.extensions` on `initialize`,
which is parsed onto `ClientCapabilities.extensions`. **Advertisement is
one-directional, client → server** — the spec defines no matching server
capability, so nothing is sent back, and `_meta.ui` is emitted unconditionally.
Unknown `_meta` keys are ignorable by design, so a client that doesn't
implement Apps is unaffected.

### Writing the bridge yourself

`body_template_name=` exists so you do not have to read this. Everything below
is what `template_name=`, `html=` and `render=` hand back to you along with
the whole document, and it is written as a checklist because every item on it
has cost somebody at least one debugging round.

The thing that makes this expensive is not difficulty, it is **silence**. A
broken bridge raises nothing, logs nothing, and leaves a clean `resources/read`
in the server log. The host keeps the frame hidden until the view says it is
ready — so an error message the view writes into the document is itself
invisible. There is no diagnostic surface until the handshake completes, which
makes the first rule the one that matters.

1. **The view MUST send `ui/notifications/initialized` — on *any* reply to
   `ui/initialize`, including an error, and even if the host never replies at
   all.** The spec requires that "the Host MUST NOT send any request or
   notification to the View before it receives an initialized notification",
   and says nothing about what a view should do when `ui/initialize` fails. The
   natural shape — treat the error as fatal, explain in the document, return —
   produces a frame that stays hidden forever with the explanation sealed
   inside it. Send the notification first, then read the result. A timeout that
   sends it anyway costs four lines and covers the silent host.

    !!! warning "The extension's own SDK does not do this"

        `App.connect()` awaits `ui/initialize` inside a `try` and sends
        `initialized` only after a successful result, so an error reply jumps
        past it to the `catch`. A view built on the SDK inherits this.

2. **The view MUST push `ui/notifications/size-changed`.** The host never asks.
   A silent view is left at whatever the host guessed. Measure an element that
   is an **ancestor of the overflow**, not `documentElement`: content inside an
   `overflow-x: auto` container does not widen its scrolling ancestor, so
   anything above that container measures as wide as the frame it already has
   and the view asks for the width it was just given. A wrapper element is not
   enough on its own — a view that scrolls a wide table internally has to
   measure the content inside the scroller. Note that a host may fix the width
   and flex only the height, and refuse the width silently.
3. **`ui/initialize` params are `appInfo`, `appCapabilities` and
   `protocolVersion`.** The spec's prose and its worked example disagree here —
   the pinned `2026-01-26` revision's example sends `capabilities` against
   normative text requiring `appCapabilities`. Follow the generated schema,
   which is what hosts are built from.
4. **The view MUST answer host requests it does not implement**, with a
   JSON-RPC `-32601` rather than silence. An unanswered request leaves the host
   waiting on a promise that never settles.
5. **Write `<html>`, `<head>` and `<body>` out.** HTML5 infers all three and
   browsers do not care, but the sandbox loads this document as raw HTML and
   applies a CSP to it, and a sandbox injecting anything into `<head>` has
   nowhere to put it when the element is implied. Every reference view does it.
6. **Inline everything.** A view that imports its runtime from a CDN needs that
   origin in `csp.resource_domains` before it will boot, on every host — and
   the extension's own advice is to inline.
7. **End the URI in `.html`.** Not a spec requirement; every reference
   implementation does it, and the one public report of a host resolving a view
   and rendering nothing also used an extensionless URI.

A last one that is not about the bridge: **a host may prefetch and cache a
`ui://` document, and is not obliged to honour `ttlMs: 0`** — which is what
`RESOURCE_CACHE_TTL_MS` defaults to. A stale view will disprove a fix you
actually made. The
[recipe](recipes/interactive-view.md#caching-and-why-a-negative-result-can-lie-to-you)
carries the hashed-URI workaround.

## Dispatch flow

The MCP package owns its transport and hands each spec to the kernel's
`dispatch` / `adispatch`: it never runs a spec's stages itself.

`tools/call`:

1. Look up the binding by name; an unknown name is JSON-RPC `-32602`.
2. Apply the tool's [`server_arguments`](#server-arguments): a bound value
   replaces whatever the caller sent under its name, and a withheld name is
   dropped.
3. Judge the `scopes=` gate against the token. A token missing a scope is
   `-32006`, and over HTTP a `403` whose `WWW-Authenticate` challenge names
   every declared scope.
4. On a retry, merge the `inputResponses` answers over the arguments
   ([elicitation](#asking-the-user-elicitation)).
5. Ask the spec's own checks, through the kernel, of the token's principal
   and the call context, before any rate limit is charged. A refused caller is
   `-32006` "Insufficient permission", uncharged, and is not told which
   argument it left out
   ([How a tool is called](#how-a-tool-is-called)).
6. Charge the tool's rate limits.
7. `dispatch(spec, principal=..., arguments=..., pool_seeds=..., call_context=...,
   progress=...)`. The kernel runs the spec: its argument check under the
   tool's `unknown_arguments`, the target lookup a service declares, its
   validator, its preconditions and affordances, the service or selector
   (inside `transaction.atomic()` when the spec says `atomic=True`), and the
   output re-read. A paginated `LIST` tool takes `page` and `limit` out of the
   arguments first and pages the selector's result.
8. Map a refusal along the MCP protocol-vs-tool boundary:

    | The kernel raises | The caller gets |
    | --- | --- |
    | `NotPermitted`, `PrincipalUnavailable` | JSON-RPC `-32006` "Insufficient permission" |
    | `InvalidArguments`, `ServiceValidationError`, a bare `DispatchError` | an `isError` result of type `validation_error` |
    | `AdditionalInputRequired` | MRTR's `input_required` ([elicitation](#asking-the-user-elicitation)) |
    | any other `ServiceError` | an `isError` result of type `service_error` |

    An `isError` result carries a JSON
    `{"error": {"type": "validation_error" | "service_error", "message": ...,
    "detail": ...}}` payload in `content[0]` (and no `structuredContent`, which
    is tied to the success schema). An argument refusal carries the message
    `"Invalid arguments"` and the kernel's field-keyed errors under `detail`; a
    call that left arguments out carries
    ``"Missing required argument(s): `pk`."`` instead, the server's
    [`conventions.missing_arguments`](#agent-conventions), over the same keyed
    `detail`. The MCP spec files input validation under tool execution errors
    in every protocol version this server serves, and keeps JSON-RPC `-32602`
    for an unknown tool and a request that fails the `CallToolRequest` schema (a
    non-object `arguments`, say), which is all `tools/call` answers with it. A
    call refused by one of the spec's declared `affordances` raises the kernel's
    `ActionUnavailable`, and its error object also carries that affordance's
    `code` — `{"type": "service_error", "message": "The books are closed.",
    "code": "books_closed"}` — so a client branches on the stable name while
    the model reads the sentence. `type` is still `"service_error"`, and any
    other `ServiceError` (a `ServiceConflict` raised by hand, say) carries no
    `code` key at all. Setting `SPEC_MCP["INCLUDE_VALIDATION_VALUE"] = True`
    additionally echoes the offending `arguments` dict back under `value` —
    handy for debugging schema mismatches against opaque client SDKs, off by
    default because the dict can carry sensitive payloads.
9. A `not_found` dispatch result, a lookup that found no row, is an `isError`
   result of type `not_found` (`"<tool>: no matching instance found"`).
10. Present the result through the spec's presenter, with the call's
    [shapes](#read-shaping-values-shapes), and wrap it as a `ToolResult` whose
    human-readable `content[0]` block is the result encoded as JSON, beside it
    in `structuredContent`.

A presenter that refuses while rendering joins step 8's `validation_error` arm
only when the caller sent one of the spec's shapes
([On a paged tool](#query-param-per-item)). Anything a dispatch raises that no
arm maps is a server fault, and the transport answers it as one: a JSON-RPC
`-32603` whose message is `Internal error`, carrying the request's `id`, under
HTTP `500` so proxies and error-rate monitoring still count it. The
exception's own text never reaches the client; it is logged at `ERROR` with its
traceback ([Observability](observability.md#what-lands-where)). A
progress-carrying stream has already committed its `200`, so there the same
error arrives as the stream's last frame.

LIST tools advertise a kind-aware `outputSchema`: a bare array schema
unpaginated, the `{items, page, totalPages, hasNext}` envelope with
`paginate=True` (enable pagination for a fully spec-compliant *object*-shaped
`structuredContent`). The array schema is not special to selector tools:
wherever a result presents as an unpaginated list — a `many=True` service, or a
service whose output re-read is a `LIST` — the tool advertises the same bare
array, because that is what it serves.

**A result with nothing to present is `{}`, and the schema admits it.** Three
successful calls present nothing: an `allow_none` RETRIEVE selector tool that
finds no row, a single-row service tool whose output re-read selector finds
none, and a single-row service declaring `ServiceSpec(allow_none=True)` that
returns `None`. MCP requires `structuredContent` to be an object, so all three
are served as `"structuredContent": {}` with a text block of `{}`, on every
tool kind and every entry point (the wire, `call_tool` / `acall_tool`, a task).
And because a server advertising an `outputSchema` must return structured
content that conforms to it, the schema of a tool that can present nothing
keeps its object root and its `properties` while its `required` list moves
beside the empty object. Which tools can is the kernel's `can_present_nothing`,
the question its own output schema asks before admitting `null`, so this server
admits `{}` exactly where the kernel admits `null`:

```json
{
  "type": "object",
  "properties": {"id": {"type": "integer"}, "title": {"type": "string"}},
  "anyOf": [{"required": ["id", "title"]}, {"maxProperties": 0}]
}
```

A full row satisfies the first branch and `{}` the second, while a non-empty
row missing a required field satisfies neither. Every other tool keeps a strict
root `required`: a RETRIEVE without `allow_none`, a `LIST` result (`allow_none`
or not, because a list is empty rather than absent), a `many=True` service, and
a single-row service that declares neither a re-read nor `allow_none=True`.
That last one is most service tools, and loosening their schemas would turn
every row field optional for each client generating types from them. Leaving
`structuredContent` off the result instead was ruled out: a client is entitled
to reject a successful result that has an `outputSchema` and no structured
content, and the TypeScript SDK does.

!!! warning "A service returning `None` with no re-read must declare it"
    A service with no re-read selector whose function returns `None` is served
    `"structuredContent": {}`. Declare `ServiceSpec(allow_none=True)` and the
    schema admits it. Undeclared, the `{}` is still served, but against a schema
    that requires the row's fields, so a client validating the result rejects
    it: nothing else the spec declares tells this service from one that always
    returns a row. Declare it, or return the row the service acted on.

`resources/read`:

1. Resolve the URI through the `ResourceRegistry`, which returns the binding
   and the URI-template variables. A URI no binding matches is `-32002` for a
   legacy caller and `-32602` for a modern one ([Protocol eras](#protocol-eras)).
2. Judge the `scopes=` gate, ask the spec's own checks of the reader, then
   charge the rate limits. A refused reader is `-32006`, uncharged.
3. `dispatch` the spec with the variables as its arguments. A variable the
   argument check refuses is `-32602` "Invalid arguments", with the kernel's
   detail under `data.detail`; a lookup that finds no row is answered as a
   missing URI.
4. Present the row through the spec's presenter and encode it into the
   `contents` block per the resource's `encoding=`: JSON by default, the
   presented `str` verbatim under `TEXT`, the presented bytes base64-encoded
   under `BLOB`. A presenter refusing a shape the URI carried is `-32602`
   keyed by the shape ([Read-shaping values](#read-shaping-values-shapes)).

## Sessions, headers, origins

The MCP 2025-11-25 transport requires:

- **`MCP-Protocol-Version`** — the version the client speaks. Validated against
  `SPEC_MCP["PROTOCOL_VERSIONS"]`. Missing → 400 except on
  `initialize`, which is allowed to omit it for the initial handshake. Some
  clients omit the header on every request; set
  `SPEC_MCP["REQUIRE_PROTOCOL_VERSION_HEADER"] = False` to accept
  those by falling back to the first supported version. A present-but-
  unsupported version is still rejected either way.
- **`MCP-Session-Id`** — issued by the server in the response to `initialize`.
  Required on every subsequent call. Unknown id → 404 (forces the client to
  re-initialize). Every session is **bound to the authenticated principal**
  that initialized it: a session presented by a different
  principal renders the same 404 as an unknown id (deliberately
  indistinguishable, so ownership cannot be probed). Sessions are stored in
  a pluggable [`SessionStore`](reference/registries.md) — by default the
  Django cache.
- **`Origin`** — strict allowlist. Empty allowlist means "no cross-origin
  requests"; an empty `Origin` header is treated as same-origin and allowed.
  Configure via `SPEC_MCP["ALLOWED_ORIGINS"]`. Use `["*"]` only for
  dev.

All three verbs authenticate through the configured `MCPAuthBackend`
**before** any session lookup, so an unauthenticated caller always sees
401 — session validity is never revealed without a credential.

`DELETE /mcp/` with a session id terminates that session immediately —
only for the principal that owns it. `GET /mcp/` opens a server-initiated
SSE stream for the caller's own session — available on `async_urls` only
(WSGI's `server.urls` returns 405 on GET because SSE requires the event
loop). See [Async deployment](async.md) for the wire details and
`MCPServer.notify(...)` for pushing frames.

## Tool results

Per the MCP tools spec, a tool result has both a `content` block list and an
optional `structuredContent`:

- `structuredContent` is always JSON-shaped — clients parse it directly.
- `content[0]` is a text block carrying the same payload encoded as JSON.

### Omitting `structuredContent` and `outputSchema`

`structuredContent` and `outputSchema` are independently toggleable. The MCP
spec (2025-06-18, SEP-1624) imposes one asymmetric rule: a tool that
advertises `outputSchema` **must** return conforming `structuredContent`. The
reverse — emitting `structuredContent` without an `outputSchema` — is
allowed.

Two server-wide settings, both default `True`:

- `SPEC_MCP["INCLUDE_STRUCTURED_CONTENT"]` — gates the
  `structuredContent` field on `tools/call` results.
- `SPEC_MCP["INCLUDE_OUTPUT_SCHEMA"]` — gates the `outputSchema`
  field on `tools/list` entries.

Per-tool overrides mirror them: `include_structured_content` and
`include_output_schema` on `register_tool`, or as a `register_tools` override. Each is tri-state — `None` (default) inherits the
global, `True`/`False` force the behaviour regardless of the setting.

Common patterns:

- **Default**: both `True`. Tools advertise their schema and return matching
  structured content. Spec-compliant and easiest for typed clients.
- **Drop only `outputSchema`**: useful when the schema bloats `tools/list`
  responses but you still want machine-parsable results. Set
  `INCLUDE_OUTPUT_SCHEMA=False`; leave `INCLUDE_STRUCTURED_CONTENT=True`.
- **Drop both**: useful when a downstream client echoes both fields back to
  the LLM (doubling token usage) or chokes on `structuredContent`. Set both
  to `False`. The text payload in `content[0]` still carries the full result
  (JSON-encoded).

The fourth combination — advertising `outputSchema` while suppressing
`structuredContent` — violates the spec. It is rejected with
`ImproperlyConfigured` at construction time (for explicit per-binding
conflicts) or at request time (for setting-level conflicts), so the misconfig
surfaces immediately rather than producing a non-compliant response.

!!! tip "If you turned `outputSchema` off because your presenter returns a subset"

    That is the right instinct against the wrong cause, and it costs the model
    the schema for every other tool too. A presenter that drops fields at
    runtime does advertise a shape its results do not match — but only when the
    pruning happens somewhere this server cannot see.

    Prune with [field markings](recipes/agent-audience.md) instead. The
    advertised schema is generated from the **same declaration** the payload is
    projected through, so the two cannot disagree: a field marked hidden leaves
    both. Then verify it rather than trusting it —
    [`assert_tool_result_conforms`](reference/testing.md#does-a-result-match-the-schema-the-server-advertised)
    checks a real result against the schema its own `tools/list` entry
    advertised, types and formats included.

## Auth model

Authorization is the project's: this package verifies nothing about a token
itself, and ships the seams a project puts its own token verification and its
own authorization server behind.

- **Backend** (`MCPAuthBackend` Protocol). Authenticates a request and produces
  a `TokenInfo`, the principal, its proven scopes, the token's audience and the
  backend's own token object. The transport calls `authenticate(request)` on
  every call; a backend returns `None` for a caller it cannot authenticate, and
  that produces a spec-mandated 401 with a `WWW-Authenticate` header built from
  `www_authenticate_challenge(...)`. The
  `/.well-known/oauth-protected-resource` view delegates its payload to the
  backend's `protected_resource_metadata()`.
- **The spec's own `permissions`.** A tool or a resource is guarded by its
  kernel spec's `PermissionCheck`s, asked of the token's principal
  ([How a tool is called](#how-a-tool-is-called)). A refusal is
  `-32006` "Insufficient permission".
- **The scope gate** (`scopes=` on every `register_*`). A
  transport check against the token's scopes: a token missing one is `-32006`,
  and over HTTP a `403` whose challenge carries every declared scope, so a
  client can step up. It does not count as a tool's guard for
  `REQUIRE_TOOL_PERMISSIONS`, though it does count as a prompt's.
- **Per-binding `permissions=` on `register_prompt` and
  `register_ui_resource`**: kernel `PermissionCheck`s, each asked
  `has_permission(principal, binding)` after the scope gate, the first refusal
  ending it, and a deactivated principal refused before any. A prompt and a
  view are not specs, so the binding stands where a spec would.

**From authentication on, `request.user` is the token's principal.** The
transport sets it, and what `request.auser()` returns, the moment a request
authenticates, so everything judged on that request afterwards (a
prompt's or view's check, a streamed call's pre-flight, a handler, a
spec's check reading `call_context["request"]`) sees the bearer token's
principal: never a user `AuthenticationMiddleware` read off a session cookie,
and an `AnonymousUser` when the token names no one. The backend itself sees an
anonymous request, so a backend that takes `request.user` for the caller cannot
be handed a session cookie's user by any page that user visits.

**The endpoints are plain Django views.** `server.urls` and `server.async_urls`
mount views of their own, exempt from CSRF and from `LoginRequiredMiddleware`,
since a bearer-token client can satisfy neither. No `REST_FRAMEWORK` setting
reaches `/mcp/`: not its throttles, its authentication classes, its renderers
or its `EXCEPTION_HANDLER`. What applies is this package's own: the
`auth_backend=` the server was given, each binding's `rate_limits=`, and the
`SPEC_MCP` settings.

[Authentication](auth.md) walks through the backends, the gates and audience
binding.
