# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This package continues `djangorestframework-mcp-server`, whose history it
carries; releases up to 0.54.0 are in
[that package's changelog](https://github.com/Artui/djangorestframework-mcp-server/blob/main/CHANGELOG.md).

## [Unreleased]

### Added

- **`bearer_challenge`.** `django_spec_mcp.auth.bearer_challenge(*,
  resource_metadata_url, scopes=None, error=None)` builds the `WWW-Authenticate`
  value of a 401 or an insufficient-scope 403: the realm, then `resource_metadata`,
  `error` and `scope`, in the order a client reads them. A backend of a project's
  own returns it from `www_authenticate_challenge`, and `AllowAnyBackend` does.
  `resource_metadata_url` has no default, because it is the pointer a client
  follows to find the authorization server and a challenge without it fails
  with no error on either side; `None` says a backend has no metadata to point at.

- **Kernel specs register as tools and resources.** `MCPServer.register_tool(spec, *, name, ...)`
  takes a `django_service_specs` `ServiceSpec` or `SelectorSpec`, and the spec's
  class decides whether it is a service tool or a selector tool;
  `MCPServer.register_tools(registry, *, overrides=)` registers every entry of a
  kernel `SpecRegistry`; and `register_resource` takes a kernel `RETRIEVE`
  `SelectorSpec` positionally. The call runs through the kernel's
  `dispatch` / `adispatch` with the token's user as the principal and the request,
  carrying the token as `request.mcp_token`, in `call_context`; the advertised
  schemas are the kernel's, so a service tool's arguments sit at the top level
  with no `data` key. A refusal the kernel raises on a tool call is answered:
  `-32006` for a principal its checks deny (`NotPermitted`) or that is
  deactivated (`PrincipalUnavailable`); an `isError` `validation_error` result
  for arguments it refuses (`InvalidArguments`, or any other `DispatchError`) and
  for a service's own `ServiceValidationError`; MRTR's `input_required` for an
  `AdditionalInputRequired`; and an `isError` `service_error` result for any
  other `ServiceError`, carrying the `code` an `ActionUnavailable` declares.
  `scopes=` gates a tool or resource on every scope it
  names, answering `-32006` with `requiredScopes`; a `KernelToolBinding` or
  `KernelResourceBinding` built by hand derives the same gate from its own
  `scopes`, and refuses `scopes=()`. Registration refuses a spec
  that is not a kernel spec, a knob of the other kind of tool, `scopes=()`,
  `UnknownArguments.PASSTHROUGH`, a resource's `uri_template` variable that no
  read or shape of the spec declares or that the server fills (a reflected read, or
  a key a typed `kwargs=` provider declares, unless its type admits `UnsetType`), each named in the error.
  A spec declaring `permissions=None` is refused under
  `REQUIRE_TOOL_PERMISSIONS`; with the setting off it registers with an
  `UnguardedToolWarning` and is called without a permission check, under a
  kernel `Grant` minted per call for that spec and principal. Only
  `register_tool` and `register_resource` grant, since a kernel `SpecRegistry`
  refuses such a spec before `register_tools` sees it; a kernel binding built
  by hand and passed to
  `server.tools.register` or `server.resources.register` is never granted, so
  the kernel refuses its unguarded spec at the call.
  A kernel tool's `inputSchema` leaves out every key the server fills, through
  its own pool seeds or a typed `kwargs=` or `input_data=` provider that always
  fills it, and offers without requiring a key a provider may fill; a paged
  tool's shapes say they apply to each item in the server's
  `AgentConventions.query_param_on_pages` wording. A `many` or `returns_many`
  service declared `allow_none` advertises an `outputSchema` of
  type `"array"` and serves `[]` when it presents nothing.
- **Listings are filtered by a kernel spec's checks.** With
  `FILTER_LISTINGS_BY_PERMISSIONS` on, a kernel binding is listed only to a
  caller its spec's checks admit, asked with no arguments through `authorize`
  and, for a create, through `authorize_scope` of `None`. A check that cannot
  answer without the call's arguments hides that binding alone, and
  `always_listed=True` keeps it visible.
- **A kernel spec's checks answer before the charge.** They are asked before
  the rate limit is charged, on `tools/call`, `acall_tool` and
  `resources/read`, so a caller they refuse is answered `-32006` uncharged;
  `call_tool` raises the denial (`NotPermitted`, or `PrincipalUnavailable` for a
  deactivated principal) before it checks the arguments. A hand-built binding
  that declares a task policy asks them before it creates a task, and its
  worker asks them again. They are shown the arguments as the kernel's argument
  check reads them, `"8"` for an `integer` as `8`, and are asked once per call
  whose arguments pass that check, twice when the call streams progress (only
  the async transport streams).
- **A call failing the argument check is judged as a listing judges the
  spec.** Its checks are asked on no arguments. Refused, it is `-32006` before
  the charge; admitted, a tool call is told which argument is wrong after the
  charge. A `many` call whose rows alone are refused keeps the answer its
  checks gave the rest of its arguments; admitted, its checks are asked twice,
  or three times when it streams. A `many` call whose Validator refuses some rows
  and also the list itself, a length cap say, is answered the `validation_error`
  result: the rows keyed by index, the list's own message under
  `non_field_errors`.
- **A streamed denial is a `403`.** A streamed `tools/call` whose spec's
  checks deny the caller is answered `403` with a challenge, as the same call
  without a progress token is, and a `ServiceError` a check raises on a streamed tool call is
  an `isError` result inside the stream, as on a plain call.
- **Completion is judged as the listing judges.** `completion/complete` of a
  kernel resource asks the spec's checks on no arguments before its rate limit.
  A check that refuses or raises on no arguments, a `ServiceError` included,
  refuses it with `-32006`; a configuration error a check raises
  (`ImproperlyConfigured`) fails the completion, as it fails the listing.
- **Resource reads answer an invalid URI variable.** An admitted reader of a
  URI whose variable fails the argument check is answered `-32602` with message
  `Invalid arguments` and the kernel's detail in `data`; `INCLUDE_VALIDATION_VALUE`
  adds the variables sent under `data.value`. The checks are shown the
  variables read as the spec's types, `true` for a `boolean` as `True`.
- **A kernel resource is watchable only by a caller who may read it.** Its
  spec's checks are asked as the read asks them.
- **A kernel tool's question is checked against the names it takes.** An
  `AdditionalInputRequired` asking for a property that the spec does not declare,
  or that the server fills for the call (a `server_arguments` name, a pool seed or
  a key a provider always fills), is answered `-32603` naming the property and the
  names the tool does take, instead of being put to the user whose answer the call
  would then refuse, drop or replace. `build_requested_schema` takes the names as
  `admitted=`; `None`, the default, asks nothing of them.
- **Kernel tool registration rules.** A kernel `LIST` tool registered without
  `paginate=True` warns `UnboundedListWarning`, and `REQUIRE_LIST_PAGINATION` refuses it. A paginated
  `LIST` whose spec declares a read named `page` or `limit` is refused, since
  pagination takes both names from the arguments before the selector runs. A
  presenter whose audience markings cannot be projected is refused, naming the
  tool. `display_name` and `display_description` are labels for in-process
  consumers, never sent on the wire. `UndescribedToolWarning` and
  `UnboundedListWarning` raised while registering a kernel spec with
  `register_tool` or `register_tools` name the line that registered it, not a
  line inside django-spec-mcp, so a warnings filter keyed on the registering
  module matches them.
- **A kernel spec whose declaration cannot hold is refused at registration, and
  a kernel tool names the arguments a call left out.** `register_tool`,
  `register_tools` and `register_resource` ask the kernel's `check_seeds` with
  the server's `pool_seeds` and refuse, naming the tool or resource, a spec no
  call could run as written: a parameter named like a registered seed or the
  call's own `request` or `view`, a positional-only parameter with no default, a
  keyword the callable requires that nothing fills, and an optional read over a
  keyword the callable requires, rather than listing a tool or resource every
  call to which fails, or every call leaving the read out. A read always sent
  is checked as required and never refused as optional: one the tool's
  `server_arguments` binds a value to, and one a resource's URI template
  names, which every read fills. A kernel tool
  call refused for leaving arguments out is answered in the server's
  `AgentConventions.missing_arguments`, ``Missing required argument(s): `pk`.``
  by default, read off the kernel's `InvalidArguments.missing`, with the per-field
  detail as before. That includes a field the spec's Validator declares.
- **An unpaginated kernel `LIST` tool is held to the row ceiling.** Its own
  `max_page_size`, deferring to the server's `MAX_PAGE_SIZE`, is the most rows
  one result carries. A result over it is refused as `result_too_large` rather
  than truncated, since an unpaginated payload carries nothing to say rows were
  dropped. One row past the ceiling is read, as a `LIMIT` on a QuerySet and a
  slice of any other iterable, so a table is not fetched whole to be refused.
  A disabled ceiling serves the result whole.
- **A kernel tool's operation conditions are asked at list time with the seeds
  its call resolves.** `tools/list` resolves the server's `pool_seeds` per
  conditioned kernel tool against that tool's own `call_context`, as the call
  does, so a seed whose resolver declares `request`, `view` or `call_context`
  reads what the call hands it. A registered seed's resolver runs once per
  conditioned kernel tool per listing, as it runs once per call, so a seed that
  queries is paid for per tool.
- **A presenter refusing a caller's shape is the caller's `validation_error`.** A
  `ServiceValidationError` a kernel tool's presenter raises while rendering is
  answered as an `isError` `validation_error` naming the `shapes` the caller sent,
  with the server's `query_param_on_pages` sentence on a paged tool, on the wire
  and from `call_tool` alike. With no shape sent, a `null` one or one the server's
  `server_arguments` answers for, the error is the server's and is raised as before:
  a `-32603` on the wire. Anything else a presenter raises stays the server's.
  A kernel resource answers the same refusal of a shape its URI carried as the
  `-32602` `Invalid arguments` its shape check gives that variable, keyed by it,
  or by `non_field_errors` when the URI carries several; with no shape in the
  URI it is raised.

- **A kernel tool can run as a task.** `register_tool` and `register_tools`'
  `overrides` take `task_policy=` for a kernel `ServiceSpec` or `SelectorSpec`:
  `FORBIDDEN`, the default, never runs it as one; `OPTIONAL` answers a
  client that declared the tasks extension with a task handle and runs inline for
  one that did not, and `REQUIRED` refuses the latter `-32021`, or `-32603` on a
  server with no task store and executor. The `scopes=` gate and the spec's own
  `permissions` are asked before the task is created, so a refused caller queues
  nothing, and the tool's rate limits are charged once, there. The worker runs
  the call, under either policy, through the kernel's `dispatch`, under the server's `pool_seeds` and
  `conventions`, asking the spec's permissions again and handing a service that
  declares `progress` the task's reporter, so its reports reach `tasks/get`.
- **A kernel resource can serve a document or bytes.** `register_resource`
  takes `encoding=` and `mime_type=` and applies them to
  what the spec's presenter returns, or its selector's value when it declares
  none. `ResourceEncoding.JSON` stays the default.
  `ResourceEncoding.TEXT` serves a presented `str` verbatim, so a presenter
  rendering the row as Markdown pairs with `mime_type="text/markdown"`, and
  `ResourceEncoding.BLOB` base64-encodes presented `bytes`, `bytearray` or
  `memoryview`, a strided one included, into the `contents` entry's `blob`, with
  no `text`. The mime type is advertised as given in `resources/read`,
  `resources/list` and `resources/templates/list`, and never inferred from or
  checked against the encoding, so `TEXT` under the default `application/json` serves a `str` that
  is already JSON as it is, as it does on a view resource. `TEXT` and `BLOB` are
  refused at registration, naming both, beside a spec declaring `affordances` or
  `allow_none`, since neither an object carrying affordances nor the `None` a
  missing row presents as is a document or bytes. A presented value of the
  wrong type is a `-32603` naming the presenter, the result ceiling and the cache
  hints apply as they do to JSON, and a presenter refusing a shape the URI
  carried, or a missing row, are answered as on a JSON resource. A
  `KernelResourceBinding` built by hand is encoded by its own `encoding` and
  `mime_type`, as a registered one is. A kernel resource and a view encode
  through one step, so the two cannot drift.

### Changed

- **A mounted server needs an `auth_backend=`.** A server built without one used to
  get `DjangoOAuthToolkitBackend`; no backend ships that verifies tokens now, and
  falling back to `AllowAnyBackend` would answer every caller. `MCPServer(...)`
  still builds without one, and so does the in-process route, which never
  authenticates, but `urls` and `async_urls` raise `ImproperlyConfigured` naming
  the server, so `manage.py check` reaches it. `MCPServer.auth_backend` is `None`
  for a server built without one.

- **The MCP endpoint and the protected-resource metadata view are plain Django
  views**, no longer DRF viewsets, so **no `REST_FRAMEWORK` setting reaches them**:
  a project's `DEFAULT_THROTTLE_CLASSES`, `DEFAULT_AUTHENTICATION_CLASSES`,
  renderers and `EXCEPTION_HANDLER` stop applying to the endpoint. The package's
  own rate limits, `rate_limits=` on each tool, resource and prompt, are what
  limit a caller, and `MCPAuthBackend` is the only authentication. An auth backend
  answers an unauthenticated caller by returning `None`, which is the `401` with
  its challenge: an exception it raises from `authenticate` is no longer rendered
  by DRF, and nothing maps it in DRF's place. Django's `PermissionDenied` and
  `Http404` raised there render as Django's own HTML `403` and `404` pages, and
  any other exception, an `AuthenticationFailed` included (a `403` reading
  `{"detail": ...}` until now), is a `500`. What a client
  sees changes in a few places: `OPTIONS` answers `200` with `Allow` and an empty
  body, where it served DRF's metadata, the class docstring included; `Allow` is
  sent on a `405` and an `OPTIONS` answer only; a method the endpoint does not
  route is a `405` with an empty body rather than a JSON `detail`; a `?format=`
  query is ignored rather than answered `404`; and the async endpoint serves an
  `Accept` naming only `text/event-stream`, the header the spec tells a client to
  send for the GET stream, which was refused with a `406`. The sync endpoint,
  which never streams, answers a `GET` or `HEAD` with its bodyless `405` whatever
  the `Accept`, where one naming only `text/event-stream` was a `406`. The `406`
  for an `Accept` admitting nothing the endpoint answers with is kept, as are the
  CSRF and login-required opt-outs, and it now follows Django's reading of the
  header. So an `Accept` of `application/json;q=0, text/event-stream`, which
  was served a `200`, is a `406` from the sync endpoint wherever Django reads a
  `q` of `0` as declaring JSON unacceptable, as Django 5.2 does, since JSON is
  all that endpoint answers with; Django 4.2 ignores the `q` and still serves it. The `406` body and the
  metadata document are serialized with `json.dumps` defaults, so they carry
  `", "` and `": "` separators and escape non-ASCII characters, where DRF wrote
  them compact and in UTF-8. `request.user` and `request.auser()` are the
  bearer token's principal from the moment the request authenticates, so no
  check ever judges the user a session cookie names, and the auth
  backend is handed an anonymous request whatever the cookie says.
  The classes are `StreamableHttpView`, `AsyncStreamableHttpView` and
  `ProtectedResourceMetadataView`, in `transport.streamable_http_view`,
  `transport.async_streamable_http_view` and
  `auth.protected_resource_metadata_view`, and they carry no routing map:
  `as_view` takes only the collaborators as keyword arguments, each view
  answers `post`, `get` and `delete` (`get` alone on the metadata view) by its
  own method, and the function `as_view` returns carries Django's `view_class`
  and no `actions` or `cls`.
- **`django-service-specs` 0.20.0 is the floor.** Its root holds every kernel name
  this package reads, among them the `check_seeds` registration asks, the
  `InvalidArguments.missing` a missing-argument message is worded from, and the
  `check_permissions` and `sync_answer` a prompt's or a view's checks are held
  to. It
  reserves `shapes` as a pool name, so a `PoolSeeds` entry named `shapes` is
  refused when it is built, as one named `user` is.
- **The package is `django-spec-mcp`, imported as `django_spec_mcp`.** Every
  import of `rest_framework_mcp` becomes `django_spec_mcp`, with the same module
  paths below it apart from `constants` and the modules renamed or removed in the
  entries below. The settings key is `SPEC_MCP`; a `REST_FRAMEWORK_MCP` setting
  is no longer read, and nothing warns about it, so rename the key. The
  `serverInfo.name` default is `django-spec-mcp`, and the logger and tracer names
  follow the import package.
- **`rest_framework_mcp.constants` has no counterpart.** Its enums and constants
  are importable from `django_spec_mcp.types`, each from a module of its own
  (`django_spec_mcp.types.json_rpc_error_code` for `JsonRpcErrorCode`), and the names the
  root exported from it still are. `UnknownArguments` and `RESERVED_POOL_SEEDS`
  were re-exports of names `django_service_specs` defines: import both from it.
  `ArgumentBinding` has no counterpart, as **Removed** says.
- **Every name stored outside the process is new**: the cache and Redis
  prefixes are `spec-mcp:session:`, `spec-mcp:task:`, `spec-mcp:sse`,
  `spec-mcp:sse-replay`, `spec-mcp:sub`, `spec-mcp:rl`, `spec-mcp:rl-sw` and
  `spec-mcp:rl-tb`, and elicitation state is signed with a new salt. **The first
  deploy therefore drops every live session, in-flight task, rate-limit window and
  pending elicitation**: clients start a new session, and a pending elicitation is
  asked again.
- **A prompt's `render` and a completer declaring `request` receive the
  transport's Django `HttpRequest`**, the one a kernel tool's spec finds in its
  `call_context`, with the caller's token on it as `request.mcp_token`; they
  received a DRF `Request` before. A callable reading `request.data` reads the
  prompt's arguments by name instead, and a completer reads what was typed from
  `value` and the client's resolved siblings from `arguments`. `request.GET` is
  the MCP endpoint's own query string, and there is no `request.query_params`.
  `request.user` is the token's principal, as before, and never a session
  cookie's user: `AnonymousUser` when the token names no one. Declare `user` or
  read `request.user`, either works: a `user` seed is that same principal, so
  for a token naming no one it is now `AnonymousUser` rather than `None`, and a
  render testing `user is None` reads `user.is_authenticated` instead. The same
  holds for a kernel tool's and a kernel resource's
  `call_context["request"].user`. A pool seed declaring `request` is handed the
  same `HttpRequest` on `resources/read` of a view, one registered with
  `register_ui_resource` or a `ResourceBinding` built by hand. Such a read's
  answer is otherwise unchanged.
- **An in-process call never writes its user to a request it is handed, and
  shares everything of the body with it.** `call_tool` and `acall_tool` work
  on a copy of `request=` whose `user` is the `user=` the call runs as, so a
  view handing its own request keeps serving as its user after the call, and
  concurrent calls sharing one request each see their own. The copy's body,
  its stream and whatever has been parsed from them (`POST` and `FILES`, the
  upload handlers they are parsed with, and the encoding they and `GET` are
  decoded with) are read and written on the handed request itself, so a spec
  reading `request.body` caches it for the caller, a spec draining the stream
  drains it for the caller, and a body no spec reads is left unread, exactly as
  if the request had been handed over. Upload handlers Django initialises
  during a call are bound to the handed request, so a handler reading
  `self.request.user` reads the caller's. Every other attribute starts as the
  very object the handed request holds: one a spec sets or deletes is rebound
  on the copy alone, and one the caller sets afterwards is not seen by it, but
  an object a spec mutates through one, such as `META` or the session, is the
  caller's. The copy is an instance of a
  subclass of the handed request's class under the same name, so `isinstance`
  holds where `type(request) is WSGIRequest` does not, and it does not pickle.
  A DRF `Request` is copied with the `HttpRequest` it wraps, whose `user` its
  setter would otherwise write through to, and shares its parse cache with the
  handed one, so `request.data` parsed by a spec is the caller's `data` rather
  than a consumed stream to parse again. `acall_tool` used to hand the request
  itself through, and sync `call_tool` handed a kernel tool's spec a request
  whose `user` could name someone other than `user=`, or `None` when no request
  was handed, where it now synthesises one bearing `user=` as `acall_tool`
  does.
- **Only Django's `PermissionDenied` is a denial.** A
  `django.core.exceptions.PermissionDenied` raised from inside a permission
  check is answered `-32006` under a `403` with its challenge, as before. DRF's
  `rest_framework.exceptions.PermissionDenied`, which is not a subclass of it,
  is no longer recognised: it reaches the transport's backstop as any other
  exception does, and is answered as a server error, `-32603`, logged at
  `ERROR`, where it was a `403` and `-32006`. A check that raised DRF's class
  returns `False` or raises Django's instead.
- **A completer receives a sibling argument named `serializer`.** The arguments
  a client resolved reach a completer's keyword pool under their own names, less
  the kernel's `RESERVED_POOL_SEEDS`, and `serializer` is no longer stripped
  beside them, so a completer declaring `serializer` is handed the client's value
  of that name like any other. The transport's own `request`, `user`, `value`
  and `arguments` are still written over a sibling of the same name.
- **A prompt or a view is guarded by kernel checks and a scope gate.**
  `register_prompt`, `@server.prompt` and `register_ui_resource` take
  `permissions=`, a list of kernel `PermissionCheck`s each asked
  `has_permission(principal, binding)`, and `scopes=`, the scope gate a tool
  takes, asked first. A deactivated principal is refused as a spec's dispatch
  refuses one, and the same judgement answers the listings, `prompts/get`,
  `resources/read`, `completion/complete` and a subscription. In a filtered
  listing a check that raises hides only its own prompt or view, as a kernel
  spec's check that raises hides only its tool, while an `ImproperlyConfigured`
  fails the listing as it fails the call. A prompt
  registered with neither is refused, or warned about with
  `REQUIRE_TOOL_PERMISSIONS` off, in a message that names both and no DRF
  permission classes. Registration holds `permissions=` to the kernel's own rule
  for a spec's, through its `check_permissions`: a string or other non-sequence,
  a class, an entry that is not a `PermissionCheck`, an `async def` on any of the
  three check methods, and an empty list, which admits everyone, are each
  refused; leave `permissions` out for no checks, or declare
  `[Unrestricted()]`. Each answer is read through the kernel's `sync_answer`, so
  a plain `def` handing back a coroutine is refused in the kernel's words, and
  one handing back an async generator, which used to be admitted because it is
  truthy and not awaitable, is refused too. A
  warnings filter or a test matching the old refusals' wording matches none of
  these.
- **A view's callable source is `render=`.** `register_ui_resource(render=...)`
  takes the zero-argument callable returning the document, where it took
  `selector=`, a name every other registration here uses for a kernel
  `SelectorSpec`'s selector. The rule is unchanged: a callable declaring a
  parameter is refused, now in a message that names it a render callable.

### Removed

- **The drf-services registrars.** `MCPServer.register_service_tool`,
  `register_selector_tool` and `register_specs`, the decorators
  `MCPServer.service_tool`, `selector_tool` and `resource`, and the `selector=`
  form of `register_resource` are gone, and so is the root function
  `register_tools(server, definitions, *, selector_defaults=,
  service_defaults=)` with the `ToolDefinition`, `ToolKind`, `ServiceDefaults`
  and `SelectorDefaults` it took. Declare each operation as a kernel
  `ServiceSpec` or `SelectorSpec` and register it with
  `MCPServer.register_tool(spec, *, name, ...)`, or a kernel `SpecRegistry` of
  them with `MCPServer.register_tools(registry, *, overrides=)`: the method
  keeps the removed function's name with a different shape, and the knobs a
  defaults record held are passed per tool, under its name in `overrides`. A
  resource is a kernel `RETRIEVE` `SelectorSpec` registered with
  `register_resource(spec, /, *, name, uri_template, ...)`, a bare callable
  included, wrapped in a `SelectorSpec` with its permissions. A
  `ResourceBinding` built by hand and passed to `server.resources.register` is
  registered unchecked, so it carries its own `permissions`.
  `django_spec_mcp.adapters` no longer exports `service_spec_to_tool` or
  `selector_to_resource`, and `django_spec_mcp.schema` no longer exports
  `build_input_schema` or `build_output_schema`: a kernel tool's schemas are the
  kernel's.

- **The drf-services bindings and the declarations they carried.** `ToolBinding`
  and `SelectorToolBinding` are gone, so every tool is a `KernelToolBinding`.
  `ResourceBinding` loses `kind`, `output_serializer` and `kwargs_provider`, and
  `QueryParam` and `UrlKwarg` go with the tool bindings' `query_params` and
  `url_kwargs`. Each is now declared on the spec: its `reads` are the arguments
  a call takes, a URL lookup among them; its `shapes` are the read-shaping
  arguments a caller may send; its presenter is what a result is rendered as;
  its `kwargs=` provider fills keys from the server's side; and a resource's
  kind is its spec's `RETRIEVE`.

- **`DRFPermissionAdapter`, `MCPPermission`, `ScopeRequired` and
  `DjangoPermRequired`.** A DRF permission class does not gate anything here,
  and a prompt or a view no longer takes a transport-only permission object
  asked `has_permission(request, token)`, nor its optional `is_listable(token)`.
  Every guard is a kernel `PermissionCheck` in `permissions=`, on the spec or on
  the prompt or view, with `scopes=` at registration for an OAuth scope gate:
  `scopes=[...]` replaces `ScopeRequired([...])`, and a check reading
  `principal.has_perm(...)` replaces `DjangoPermRequired`.

- **`ArgumentBinding`, and the root re-export of `UnknownArguments`.** A kernel
  tool's arguments always sit at the top level, so there is no binding to
  choose and no `argument_binding` to pass. Import `UnknownArguments` from
  `django_service_specs`, which defines it.

- **`TOOL_EDGE_SEEDS`, `COMPLETION_EDGE_SEEDS` and `RESOURCE_EDGE_SEEDS`.** The
  first two named what the DRF edge filled itself (`request`, `view` and
  `serializer`), and the third the one key a resource read seeds beyond the
  kernel's reserved set, `request`. A kernel spec's reads are checked against
  the kernel's `RESERVED_POOL_SEEDS` and the server's `pool_seeds` instead, and
  the kernel refuses a read named `request` or `view` when the spec is built.
  `register_resource` reserves the kernel's `CALL_SEEDS` (`request` and `view`,
  the call's own) beside them, so a URI-template variable named `request` or
  `view` is refused as a reserved transport key, in the message a `{user}`
  variable gets. Import `CALL_SEEDS` from `django_service_specs`.

- **TOON output, `OutputFormat` and the `toon` extra.** A tool result's
  `content[0]` text is always the payload as JSON, beside the same payload in
  `structuredContent`. With one format left there is nothing to choose, so
  `OutputFormat`, `encode_toon`, `register_tool(output_format=)` (and its
  `register_tools` override), `KernelToolBinding.output_format`,
  `build_tool_result(output_format=)` and the `DEFAULT_OUTPUT_FORMAT` setting go
  with it. A `tools/call` carrying `outputFormat` is no longer refused for a
  value other than `json`, `toon` or `auto`: the parameter is not read, and the
  call is answered in JSON. Nothing reads a `SPEC_MCP["DEFAULT_OUTPUT_FORMAT"]`
  left in a project's settings and nothing warns about it, so delete the key.

- **The dependencies on djangorestframework, djangorestframework-services and
  djangorestframework-dataclasses.** None is in the package's requirements or
  its lock, and nothing in it imports them, so a project that listed
  `"rest_framework"` in `INSTALLED_APPS` only for this package can drop it.

- **The bundled authorization-server pieces, `rest_framework_mcp.contrib`.** Dynamic
  Client Registration (`DynamicClientRegistrationViewSet` and its serializer and
  types), the authorization-server metadata and OpenID discovery views,
  `build_oauth_urlpatterns` with its `mcp-oauth-*` URL names,
  `check_oauth_url_shadowing`, `build_authorize_passthrough_view`,
  `AuthUserAdapter` and `SimpleJWTCookieAdapter` are gone, and so are the settings
  only they read: `DCR_ENABLED`, `DCR_INITIAL_ACCESS_TOKEN` and
  `SIMPLEJWT_ACCESS_COOKIE`. So is what fed the metadata view:
  `authorization_server_metadata()` on `MCPAuthBackend` and `AllowAnyBackend`,
  and the `AuthorizationServerMetadata` dataclass it returned, since an
  authorization server's metadata is that server's to serve. OAuth is the
  project's: run the authorization server
  you choose (django-oauth-toolkit serves its own registration and discovery
  endpoints from 3.4.0, and so does any outside identity provider), sign users in
  at its own authorize endpoint, and verify its tokens through an
  `MCPAuthBackend`. This package keeps the resource-server side: the backend
  protocol, the protected-resource metadata view and the challenge responses.

- **The django-oauth-toolkit backend.** `DjangoOAuthToolkitBackend`,
  `UnenforcedAudienceWarning` and `MountedAuthorizationServerWarning` are gone,
  with `MCPServer(resource_url=)`, the settings `RESOURCE_URL` and
  `ENFORCE_AUDIENCE` that only the backend read, and the `oauth` extra. The
  backend verified tokens by reading one authorization server's models, which is
  an OAuth implementation of the kind removed above. A project writes the backend
  for the authorization server it runs: the authentication page walks through one
  that checks a token's audience with `audience_matches` and answers its 401 with
  `bearer_challenge`. The `SERVER_INFO` keys the backend read for its metadata
  (`resource`, `authorization_servers`, `scopes_supported`,
  `resource_metadata_url` and `documentation`) are read by nothing now: a
  backend of a project's own publishes its metadata itself.

- **The refusal of settings keys drf-mcp removed in its 0.12.0.**
  `SPEC_MCP["AUTH_BACKEND"]`, `["SESSION_STORE"]` and `["AUTH_USER_ADAPTER"]` no
  longer raise `ImproperlyConfigured` at `MCPServer(...)`: no project ever set
  them under this package's settings key, so there is nothing to catch. Pass the
  collaborators to `MCPServer(auth_backend=..., session_store=...)`, as before.

- **Chain tools.** `MCPServer.register_chain_tool`, `ChainStep`, `ChainContext`
  and `ChainToolBinding` are gone, with the chain branches of `tools/list`,
  `tools/call` and the availability check. A chain ran its steps outside the
  spec dispatch every other tool goes through, so each spec-level behaviour had
  to be re-implemented for it. To run several operations as one tool, write one
  kernel `ServiceSpec` whose service calls the others, and register it with
  `register_tool`: it is judged, validated and presented like any other
  service tool, and its `atomic` flag owns the transaction.

- **The `jwt`, `spectacular`, `filter` and `oauth` extras.** `jwt` only installed
  djangorestframework-simplejwt for the cookie adapter removed above.
  `spectacular` installed drf-spectacular, which nothing here reads: a tool's
  `inputSchema` is the kernel's schema of its spec, and `@extend_schema_*`
  annotations do not reach it. `filter` installed django-filter, which the
  kernel reads: a `SelectorSpec`'s `filter_set=` runs a `FilterSet`, installed
  with `pip install "django-service-specs[filter]"`.
  `oauth` installed django-oauth-toolkit for the backend removed above.

### Fixed

- **A `REQUIRED` tool's task runs.** The worker's run of a task went back
  through the task decision, met a context declaring no extension, and was
  refused `-32021`, so every task of a `REQUIRED` tool ended `failed` without
  running. The worker's run now enters below that decision. A client that did
  not declare the extension is still refused `-32021` by a `REQUIRED` tool.

[Unreleased]: https://github.com/Artui/django-spec-mcp/compare/v0.0.0...HEAD
