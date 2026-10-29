# Repo conventions for `django-spec-mcp`

This file is the single source of truth for how to write code in this package.
Rules are non-negotiable unless flagged as a heuristic.

## Structural rules

1. **One exported class or function per file.** File name = `snake_case` of the symbol.
   `MCPServer` → `mcp_server.py`; `runs_on_kernel` → `runs_on_kernel.py`,
   or live in a sibling `utils.py` if used by several files in the same package.
   There is no exception. The package's enums and shared constants (`OutputFormat`,
   `JsonRpcErrorCode`, `JSONRPC_VERSION`, the reserved-key sets and the rest) live
   one per file in the root `types/` package, `OutputFormat` in
   `types/output_format.py`, and a new one goes there the same way. **`types/`
   imports nothing else in the package**, so every subpackage can read it without
   a cycle, and `tests/types/test_types_imports.py` holds that.
2. **Private helpers used in only one file** stay there with a leading `_`.
3. **Non-exported helpers shared across files** go into a sibling `utils.py`. Classes
   are allowed in `utils.py` if they are internal infrastructure.
4. **Top-level imports only.** No function-local / lazy imports unless a circular import
   is genuine and documented inline at the import site, or the dependency is optional
   (e.g. `python-toon`) — those imports go inside the function body
   with a clear `ImportError` message.
5. **Full type annotations on every function and method signature.** `Any` is allowed
   only at Django ORM and wire boundaries (e.g. `request.user`, `queryset` items,
   raw `arguments` off the wire, a spec's opaque collaborators) where the type
   genuinely is `Any`.
6. **`__init__.py` is the only re-export point.** Each `__init__.py` lists the public
   surface in `__all__`. Internal modules import from leaf paths, never from the
   package's `__init__`. The top-level `__init__.py` re-exports the user-facing API.
7. **Always `from __future__ import annotations`** at the top of any file with type
   annotations. We support Python 3.10+, so no `match` statements and no PEP 695
   `type` statements.
8. **Absolute imports only.** Imports are ordered stdlib → third-party → first-party
   (`django_spec_mcp`). Within each block, alphabetical.
9. **NEVER use relative imports.** `from . import x`, `from .foo import bar`, and any
   other dotted-relative form is forbidden everywhere in the package, including
   `__init__.py`. Always write the full absolute path (`from django_spec_mcp.foo
   import bar`).
10. **Types and functionality live in separate sub-packages.** When a directory
    contains *both* type declarations (dataclasses, Protocols, frozen wire-shape
    records) *and* functionality (callables, registries, dispatch helpers), the
    types move into a `types/` sibling and are re-exported from the package's
    `__init__.py` for ergonomic imports. Example: `registry/types/kernel_tool_binding.py`
    holds the dataclass; `registry/tool_registry.py` holds the functional class.
    The root `types/` re-exports its whole surface from `types/__init__.py`, and the
    package root re-exports only the public subset of it.

## API style rules

11. **Always dataclasses over `dict[str, Any]` for structured data.** Every
    wire-shape payload, response envelope, configuration record, and binding
    field is a frozen `@dataclass` with explicit field types. `dict[str, Any]`
    survives only at genuine serialisation boundaries (e.g. `JsonResponse` body
    construction, the JSON-RPC envelope's free-form `data` field, raw
    `arguments` straight off the wire). Every `dict[str, Any]` that lives more
    than a few lines deep should be converted to a dataclass, or explained at
    the site with a one-line comment justifying why it isn't.
12. **Endpoints are plain Django views, never DRF views or viewsets.** The MCP
    endpoint (`StreamableHttpViewSet`, `AsyncStreamableHttpViewSet`) and the
    protected-resource metadata view subclass `django.views.View`, and nothing
    in the package imports DRF: it serves kernel specs, has no DRF in its
    dependencies, and a DRF view would let a project's
    `REST_FRAMEWORK` settings (throttles, authentication classes, renderers,
    the exception handler) decide what the endpoint answers, where the package's
    own auth backend, rate limits and JSON-RPC backstop are what must. Each
    keeps its `as_view(actions, **initkwargs)` routing map, which is what
    `MCPServer` passes, and does for itself what a DRF view would: the `406` for
    an `Accept` it cannot answer, `Allow` on every `405`, the CSRF and
    login-required opt-outs, and the token's principal on `request.user` from
    the moment the request authenticates (`transport/utils.py`). The class
    names keep their `ViewSet` suffix only because the server and the API
    reference name them.

## No view-layer coupling

The MCP server **does not** wrap, walk, or otherwise reach into a project's views,
URLconf or routers, DRF's or Django's. Consumers register kernel specs directly: a
`django_service_specs` `ServiceSpec` or `SelectorSpec` as a tool through
`register_tool` / `register_tools`, and a `RETRIEVE` `SelectorSpec` as a resource
through `register_resource`. The unit of registration is the kernel spec, not a view
— a project with no views at all must still be able to expose its services over MCP.
A resource that is not a spec is a view: an MCP Apps UI resource
`register_ui_resource` builds, or a `ResourceBinding` built by hand over a plain
callable. Each holds a bare selector this package calls itself.

This is non-negotiable. The MCP package owns its own discovery, and the kernel's
`dispatch` owns the call; neither reads a project's view layer to find a spec.

## No module-level or class-level mutable state

State lives on instances. Module-level constants (lookup tables, regexes, frozen
settings defaults, dispatch tables) are fine — module-level **mutable** state is not.
Specifically:

- No module-level mutable singletons (`_some_warned_flag`, registries, caches).
- No class-level mutable attributes declared on the class body (`_sessions: set = set()`
  is a bug — every instance shares it). Initialise mutables in `__init__`.
- No "warn-once" state stored at module scope. If a warning genuinely belongs
  once-per-process, accept the cost of always-emit and let the consumer filter via
  `warnings.filterwarnings`, or thread the flag through an instance.

This rule exists because module/class-level mutables make tests interfere with each
other and make the package unsafe in multi-tenant or worker-pool deployments. Putting
state on an instance keeps lifetime explicit and ownership obvious.

A corollary: views never look up collaborators (auth backend, session store, registries)
through module-level loaders. They receive them via `as_view(...)` from `MCPServer`.

## Reuse, don't reimplement

The MCP layer is a transport over the `django-service-specs` kernel. Import its
primitives — do not parallel them.

**Every kernel name is imported from the root of `django_service_specs`**, which
owns it: `ServiceError`, `ServiceValidationError`, `UnknownArguments`,
`SelectorKind`, `PoolSeeds`, `UNSET`, `SpecRegistry` and the rest. Never from a
module below the root: the root is the kernel's stable surface, and a path under
it is the kernel's to move. There is no exception list. A name this package needs
that the root does not export is asked for upstream, not imported from where it
happens to live.

- `ServiceSpec` / `SelectorSpec` — the unit of registration, and the only one a
  tool or a kernel resource is built from.
- `dispatch` / `adispatch` — the call: the spec's checks, the argument check, the
  service or selector, the presenter. Every tool and every kernel resource read
  runs through it, and every refusal it raises is answered through
  `KERNEL_REFUSALS` (`handlers/kernel_refusals.py`), one row per refusal class.
- `authorize`, `authorize_scope` and `check_call` — a spec's checks asked outside a
  call: before the rate limit is charged, by the listing filter, and by a
  subscription's grant. They are the checks `dispatch` asks.
- `check_seeds` and `check_server_arguments` — the registration checks: whether a
  spec can be called at all under the server's `pool_seeds`, and whether a tool's
  `server_arguments` can hold for its spec.
- `resolve_callable_kwargs` — kwarg-pool dispatch for a bare callable. Picks the
  subset of a kwarg pool matching the callable's declared parameters; if the
  callable declares `**kwargs` the entire pool is passed. `base_pool` builds the
  pool's reserved half (the principal, the request and the server's seeds).
- `run_selector` / `arun_selector` — a view resource's bare selector, with
  sync/async transparency (`_compat/utils.arun_selector_sync_safe` keeps a sync
  selector off the event loop).
- `spec_input_schema`, `spec_output_schema`, `server_owned_keys` and
  `provider_reached` — the readers of what a spec admits and presents: the schema
  of its arguments and its result, which keys the server fills so a caller never
  sends them, and which keys a `kwargs=` or `input_data=` provider fills. The
  schema and the registry read these rather than deriving their own answer,
  because every local copy of what dispatch does drifts from it: a provider
  annotated `list[str | UnsetType]`, one whose types exist only under
  `TYPE_CHECKING`, and a service declaring `allow_none` each got an answer
  dispatch did not give.
- `paginate_output` + `OutputPage.envelope` — the page a `LIST` selector tool serves and
  the `{items, page, totalPages, hasNext}` envelope around it. Only the coercion of the
  untyped `page` / `limit` arguments into ints stays here (`_coerce_int` in
  `handlers/kernel_utils.py`), because answering a malformed argument is a
  transport's policy while what a page *is* is not.
- `django_service_specs.ServiceError` and `django_service_specs.ServiceValidationError`
  — caught at the MCP boundary and
  mapped to `isError: true` tool results via `build_error_tool_result(...)`
  (`error_type="validation_error"` for `ServiceValidationError`, `"service_error"`
  for `ServiceError`), not to JSON-RPC error envelopes. A dispatch failure surfaces
  as a failed *tool result*, so the caller still gets a well-formed `tools/call`
  response. Every `ServiceError` arm builds its result through
  `handlers.utils.service_error_result`, which adds the `code` an
  `ActionUnavailable` carries and no key for any other member; a new arm calling
  `build_error_tool_result` directly would drop the code again. The kernel's
  argument refusals (`InvalidArguments`, any other `DispatchError`) share the
  `validation_error` arm, because the MCP spec files input validation under tool
  execution errors. On `tools/call`, `-32602` is left to an unknown tool and a
  request failing the `CallToolRequest` schema.

Validation, presentation and kwarg-pool construction are **not** reproduced
locally. The `handlers/` layer delegates to the kernel's transport-neutral
`dispatch`, so one implementation of the argument check and of presentation
serves this transport and every other the kernel has.

**Which paths call `dispatch`, and what the rest do instead.** This list is the
contract; keep it accurate, because it is what a maintainer adding a spec-level
behaviour upstream reads to decide where to wire it. Getting it wrong once already
cost four separate authorization gaps, each on a path this paragraph claimed was
covered.

| Path | Dispatch |
| --- | --- |
| `tools/call` (`handle_tools_call*.py` → `run_kernel_tool*.py`) | `dispatch` / `adispatch` |
| In-process `call_tool` (`call_kernel_tool.py`) | `dispatch` |
| In-process `acall_tool` (`handle_tools_call_async.py` → `run_kernel_tool_async.py`) | `adispatch` |
| A task's worker (`tasks/run_task.py`, re-entering `handle_tools_call` below the task decision) | `dispatch` |
| `resources/read` of a kernel resource (`read_kernel_resource*.py`) | `dispatch` / `adispatch` |
| `resources/read` of a view (`handle_resources_read*.py`) | **direct** `run_selector` |
| `prompts/get` (`handle_prompts_get*.py`) | **direct** call of `render` |
| `completion/complete` (`handle_completion_complete.py`) | **direct** call of the completer |

`handlers/dispatch.py` and `handlers/async_dispatch.py` are the JSON-RPC method
routers, not a spec path. `runs_on_kernel` (`handlers/runs_on_kernel.py`) is what
every caller taking a resource or any listable binding asks to pick a path, by the
binding's class.

The direct paths are direct for structural reasons, not by neglect:

- **`resources/read` of a view.** A `ResourceBinding` that is not a
  `KernelResourceBinding` (an MCP Apps UI resource, or one built by hand over a
  plain callable) holds a bare selector, not a spec, so there is nothing to
  dispatch. It reads on the kernel's leaves: `carry_token`, then the URI-template
  variables with `base_pool` laid over them (so no variable shadows a seed),
  `resolve_callable_kwargs`, `run_selector` (`arun_selector_sync_safe` on the
  async transport), then `build_resource_contents` to encode. A kernel resource
  encodes through the same `encode_resource_contents` step, so the two cannot
  drift.
- **`prompts/get` and `completion/complete`.** Neither target is a spec: a prompt's
  `render` and a completer are bare callables. They dispatch through the kernel's
  `resolve_callable_kwargs`, with `request` the Django `HttpRequest` `carry_token`
  returns (the token at `request.mcp_token`, its principal as `request.user`). A
  completer's pool drops the kernel's `RESERVED_POOL_SEEDS` from the client's
  siblings, then lays `request`, `user`, `value` and `arguments` over them —
  transport seeds outrank client-supplied names, always. A completion of a kernel
  resource still asks that resource's spec's checks on no arguments, as the
  listing asks them, before its rate limit.

**Anything a direct path re-implements must be named at the call site**, with the
kernel helper it stands in for. A direct path that quietly grows a private copy of
something `dispatch` does is the drift this section exists to prevent.

Delegating is load-bearing, not tidiness: a transport-local renderer or context
resolver "for transport-shaped equivalence" drifts from the one dispatch uses, and
the drift reaches consumers as a `TypeError` or a `KeyError` no test here wrote. If
a path needs something presentation doesn't give it, extend the kernel's `present`
upstream rather than growing a second copy here.

## Tests

- `make test` runs pytest with `--cov=django_spec_mcp --cov-fail-under=100` (line +
  branch). Restructure rather than reach for `# pragma: no cover`.
- Test layout mirrors the source tree under `tests/`. `django_spec_mcp/foo/bar.py`
  → `tests/foo/test_bar.py`.
- `tests/testapp/` houses the minimal Django app + sample `MCPServer` factory used by
  the end-to-end suite. Tests must build a fresh server per scenario when they need
  custom configuration — never rely on hidden test-suite globals.
- Async tests: `async def test_...` with pytest-asyncio (`asyncio_mode = "auto"`).
- DB tests: `@pytest.mark.django_db` for sync; `@pytest.mark.django_db(transaction=True)`
  for async.

## Lint and types

- `make lint` runs `ruff check .` + `ty check django_spec_mcp`. CI fails on either.
- `ruff format` is the source of truth for layout. `make format` writes, `make
  format-check` verifies.
- Pre-commit runs `make lint-fix`, `make format`, `make type-check`. Commits must be
  clean before push — never `--no-verify`.
- `ty` is scoped to `django_spec_mcp/` only (not tests). An attribute ty cannot see
  takes `# ty: ignore[<rule>]` at the site, with the rule named: the flags Django
  reads off the function `as_view` returns (`csrf_exempt`, `login_required`, and
  the `actions` and `cls` the endpoints keep) are `unresolved-attribute`, and the
  async endpoint's `async def dispatch` over `View.dispatch` is
  `invalid-method-override`. Say why beside one that is not one of these.

**Four of these rules are enforced, not remembered.** `ruff` carries the
future-annotations import (`isort.required-imports`, with `__init__.py` and
`migrations/` exempt) and the ban on every relative form including single-dot
(`ban-relative-imports = "all"` — `TID` alone defaults to `parents`, so
`from .foo import x` slipped through). A pygrep pre-commit hook carries the
mypy-pragma rule, scoped to the package because that is exactly what `ty`
checks: under `tests/` no checker reads either pragma, while an editor running
mypy or pyright does honour `# type: ignore`. The `UP040/046/047` ignores stop
`ruff --fix` rewriting code *into* the PEP 695 syntax these conventions forbid.
Change a rule here and change the config with it, or they drift apart again.


## Boundaries

- The TOON encoder imports `python-toon` lazily and falls back to JSON with a warning
  if the extra is missing — a tool call must never fail because the optional extra is
  absent.
- The `transport/` layer must not import from `server/` (one-way dependency: server
  composes transport, never the other way).
- Nothing in the package imports `rest_framework`, `rest_framework_services` or
  `rest_framework_dataclasses`, and none is in `pyproject.toml` or `uv.lock`. A
  denial is Django's `PermissionDenied`; a DRF one is a fault like any other
  exception (`is_raised_denial` in `transport/utils.py`).

## Branching

When working on a new feature or a version bump, **ALWAYS** switch to a new branch
first (`git checkout -b feat/...` or `release/vX.Y.Z`) and push to that branch.
Never commit feature work or version bumps directly to `main`, and never push to
`main` from the local checkout — `main` only advances via merged PRs (or, for
releases, the tagged commit produced on the release branch).

## Releases

The release pipeline is **merge-to-main triggered**, not tag-triggered.
`.github/workflows/release.yml` runs on every push to `main` and calls
`make release-publish-prepare`. The script in
[`scripts/release-publish.sh`](scripts/release-publish.sh) is the single source
of truth for the flow and behaves as follows:

1. Extract the version from `django_spec_mcp/version.py` (the
   single source of truth; `pyproject.toml` declares `dynamic = ["version"]`
   and hatchling reads the value from `version.py` at build time).
2. Check whether `vX.Y.Z` already exists locally or on origin. **If yes →
   short-circuit:** emit `released=false` to `$GITHUB_OUTPUT` and exit 0.
   That is what makes every-merge-to-main safe: non-bumping merges are no-ops.
3. Run `uv run pytest` as a final gate.
4. `uv build` into `dist/`.
5. Extract the `## [X.Y.Z]` section from `CHANGELOG.md` into
   `dist/release-notes.md`.
6. Emit `released=true` so the downstream steps run.

If `released=true`, the workflow then:

- Publishes to PyPI via **OIDC trusted publishing**
  (`pypa/gh-action-pypi-publish`, no token in repo).
- Calls `make release-publish-finalize`, which tags `vX.Y.Z`, pushes the tag,
  and runs `gh release create vX.Y.Z --notes-file dist/release-notes.md
  dist/*.whl dist/*.tar.gz`.
- `mkdocs gh-deploy --force --clean` to `gh-pages` (skipped if `mkdocs.yml`
  is missing).

The previous tag-trigger flow has been removed.

### Cutting a release

The bump itself is driven by
[bump-my-version](https://github.com/callowayproject/bump-my-version) via `uvx`;
configuration lives in `[tool.bumpversion]` in `pyproject.toml`.

```bash
# 1. On a release branch, make sure CHANGELOG.md has the entries you want
#    to ship under ## [Unreleased]. Then bump:
make release-bump VERSION=0.3.0
# This rewrites django_spec_mcp/version.py (the single source of
# truth — pyproject.toml is dynamic and reads from it at build time),
# promotes the [Unreleased] block under a dated [0.3.0] section, and
# rewrites the link footer — all driven by the [[tool.bumpversion.files]]
# entries in pyproject.toml.

# 2. Review the diff, commit, open a PR, get it reviewed.
git diff
git commit -am "Release 0.3.0"
git push -u origin release/0.3.0
gh pr create

# 3. Merge to main. release.yml fires on the merge commit, detects the
#    bumped version, runs the full flow, and tags/publishes vX.Y.Z.
```

`release-bump` refuses to run on a dirty tree (bump-my-version's
`allow_dirty = false`), so you don't fold unrelated changes into the
release commit.

For an end-to-end workstation release (e.g. publishing from a developer
machine when CI is broken), `make release-publish` runs prepare → `uv publish`
→ finalize in one shot. Set `DRY_RUN=1` to rehearse without uploading or
pushing.

### One-time setup (manual, by the repo owner)

These steps need to happen once before the first release will succeed:

1. **PyPI Trusted Publisher** — sign in to PyPI, open the project settings,
   add a "Pending" publisher pointing at `Artui/django-spec-mcp`,
   workflow `release.yml`, environment `pypi`. Promote it to a real publisher
   after the project exists on PyPI.
2. **GitHub Environment** — create a `pypi` environment under
   `Settings → Environments` (no secrets needed; OIDC handles auth).
3. **GitHub Pages** — under `Settings → Pages`, set "Build and deployment"
   source to "Deploy from a branch", branch `gh-pages`, folder `/`. The first
   release to ship with a `mkdocs.yml` creates that branch; the
   `coverage-badge` job in `tests.yml` is also fine creating it as an
   orphan if docs haven't shipped yet.

If the bumpversion config drifts (e.g. a previous release was hand-edited),
fix it before bumping: set `current_version` in `pyproject.toml` to the
*last actually released* version and back-fill any missing
`[X.Y.Z]: …compare/v…` rows in the CHANGELOG footer. `bump-my-version`'s
search-replace is exact-match; missing or stale strings make it fail or
emit wrong compare links.

## Common pitfalls

- `async def` Django views require ASGI; the JSON-only POST path must keep working
  under WSGI. Don't reach for `async` unless SSE actually requires it.
- `Origin` validation is mandatory per the MCP spec — do not silently allow
  missing/empty origin against an empty allowlist.
- `MCP-Session-Id` and `MCP-Protocol-Version` are required headers post-`initialize`;
  missing → 400/404 as per spec, not silent acceptance.
- `structuredContent` is always JSON; only the `content[0]` text payload varies by
  `OutputFormat`. TOON output is wrapped in a fenced code block with a leading
  `# format: toon` marker.
