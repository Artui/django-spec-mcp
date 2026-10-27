"""A kernel selector tool's ``inputSchema`` asks for what a call needs and nothing the server fills.

A kernel spec declares its arguments as ``reads``, each ``Parameter`` saying
whether it is required, so nothing is inferred from a signature: requiredness
is the declaration. What this transport still decides is what to leave out of
that declaration, because the kernel's ``spec_input_schema`` lists every read
as declared, ``required`` meaning the call needs the value from *someone*. Two
things answer for a read instead of the caller:

- a key a ``kwargs=`` provider is annotated to fill always reaches the
  selector as the provider's, so it is not advertised at all
  (``server_owned_keys``);
- a key a provider may decline with ``UNSET``, and every read beside a
  provider whose keys cannot be read, is offered and not required, because
  dispatch does not demand it of the caller.

The drf-services original asked the same of a reflected signature, a
``UrlKwarg``, ``spec_kwargs_provides=`` and an ``input_serializer``'s
defaults, none of which a kernel spec has; it is kept beside this file as
``test_selector_tool_requiredness_drfs.py``. Its run of the concepts page's
example is not carried, because that example declares a drf-services spec;
the kernel example the page owes gets the same test when it is written. Built through
``MCPServer.list_tools``, so each test reads what a client is served.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar, Union

import jsonschema
import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    DataclassPresenter,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    Unrestricted,
    UnsetType,
)
from typing_extensions import TypedDict

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend

_T = TypeVar("_T")


@dataclass
class _Row:
    pk: int
    tenant: str


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _schema(server: MCPServer, name: str = "get") -> dict[str, Any]:
    listed: Any = server.list_tools(user=None)
    return next(tool for tool in listed["tools"] if tool["name"] == name)["inputSchema"]


def _rows(*, pk: int, tenant: str = "unscoped") -> list[_Row]:
    return [_Row(pk=pk, tenant=tenant)]


_PK = Parameter("pk", "integer", required=True)
_TENANT = Parameter("tenant", "string", required=True)
_REGION = Parameter("region", "string", required=True)


def _register(server: MCPServer, *reads: Parameter, kwargs: Any = None) -> None:
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_rows,
            reads=Parameters(list(reads)),
            kwargs=kwargs,
            presenter=DataclassPresenter(_Row),
            permissions=[Unrestricted()],
        ),
        name="get",
        description="Read rows.",
    )


def test_a_read_declared_required_is_required() -> None:
    server = _server()
    _register(server, _PK)

    schema = _schema(server)

    assert schema["properties"] == {"pk": {"type": "integer"}}
    assert schema["required"] == ["pk"]


def test_a_read_declared_optional_stays_optional() -> None:
    server = _server()
    _register(server, _PK, Parameter("tenant", "string"))

    schema = _schema(server)

    assert set(schema["properties"]) == {"pk", "tenant"}
    assert schema["required"] == ["pk"]


def test_a_selector_parameter_no_read_declares_is_not_advertised() -> None:
    # Changed by design: the drf-services reflection advertised every parameter
    # of the selector's signature. A kernel spec takes the arguments it
    # declares and no others, so ``tenant``, which ``_rows`` takes with a
    # default, is never offered and the closed set refuses it.
    server = _server()
    _register(server, _PK)

    assert set(_schema(server)["properties"]) == {"pk"}
    refused = server.call_tool("get", {"pk": 1, "tenant": "acme"}, user=None).to_dict()
    assert refused["isError"] is True


def test_a_read_named_like_a_dispatcher_seed_is_refused_when_the_spec_is_built() -> None:
    # Changed by design: the drf-services reflection skipped a seed named in a
    # signature. A kernel read named like one is a declaration dispatch could
    # never honour, so the spec refuses it before any tool is registered.
    with pytest.raises(ImproperlyConfigured, match="user"):
        SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_rows,
            reads=Parameters([Parameter("user", "string", required=True)]),
            permissions=[Unrestricted()],
        )


# ---------- what a ``kwargs=`` provider answers for ----------


class _Scope(TypedDict):
    tenant: str


class _GenericScope(TypedDict, Generic[_T]):
    tenant: _T


class _MaybeScope(TypedDict):
    tenant: str | UnsetType


class _MaybeUnionScope(TypedDict):
    tenant: Union[str, UnsetType]  # noqa: UP007 - the spelling under test


class _NoneableScope(TypedDict):
    tenant: str | None


def _typed_provider() -> _Scope:
    return {"tenant": "acme"}


def _generic_provider() -> _GenericScope[str]:
    return {"tenant": "acme"}


def _generic_declining_provider() -> _GenericScope[str | UnsetType]:
    return {"tenant": "acme"}


def _untyped_provider() -> dict[str, Any]:
    return {"tenant": "acme"}


def _unresolvable_provider() -> _NotDefinedAnywhere:  # noqa: F821
    return {"tenant": "acme"}


def _maybe_provider() -> _MaybeScope:
    return {"tenant": "acme"}


def _maybe_union_provider() -> _MaybeUnionScope:
    return {"tenant": "acme"}


def _noneable_provider() -> _NoneableScope:
    return {"tenant": "acme"}


class _ContainerScope(TypedDict):
    # ``UnsetType`` inside a container: the key is always a list or a dict, and
    # only an item or a value of it may be ``UNSET``.
    regions: list[str | UnsetType]
    labels: dict[str, str | UnsetType]


def _container_provider() -> _ContainerScope:
    return {"regions": ["eu"], "labels": {}}


class _UnresolvableFieldScope(TypedDict):
    # The return annotation resolves; one of the class's own fields does not,
    # and the other does.
    tenant: NoSuchType  # noqa: F821
    region: str


def _unresolvable_field_provider() -> _UnresolvableFieldScope:
    return {"tenant": "acme", "region": "eu"}


class _MaybeTenantScope(TypedDict):
    tenant: str | UnsetType
    region: str


def _unresolvable_parameter_provider(*, request: NoSuchRequest) -> _MaybeTenantScope:  # noqa: F821
    # The return annotation resolves at runtime; the parameter's does not, as
    # one imported only for type checking would not.
    return {"tenant": "acme", "region": "eu"}


@pytest.mark.django_db
@pytest.mark.parametrize("provider", [_typed_provider, _generic_provider])
def test_a_name_a_typed_provider_returns_is_not_asked_for(provider: Any) -> None:
    # The kernel lists ``tenant`` as declared and required, since the call
    # needs a value for it; the provider is who supplies it, and replaces
    # whatever a caller sends, so asking for it would demand a value the call
    # then discards. A parameterised TypedDict is read off its origin.
    server = _server()
    _register(server, _PK, _TENANT, kwargs=provider)

    schema = _schema(server)

    assert set(schema["properties"]) == {"pk"}
    assert schema["required"] == ["pk"]
    jsonschema.validate({"pk": 1}, schema)


@pytest.mark.django_db
def test_the_call_serves_the_providers_value_whatever_the_caller_sends() -> None:
    # Why the name is left out: a caller's value never reaches the selector.
    server = _server()
    _register(server, _PK, _TENANT, kwargs=_typed_provider)

    bare = server.call_tool("get", {"pk": 1}, user=None).to_dict()
    sent = server.call_tool("get", {"pk": 1, "tenant": "caller"}, user=None).to_dict()

    assert bare["structuredContent"] == [{"pk": 1, "tenant": "acme"}]
    assert sent["structuredContent"] == [{"pk": 1, "tenant": "acme"}]


@pytest.mark.parametrize(
    "provider", [_maybe_provider, _maybe_union_provider, _generic_declining_provider]
)
def test_a_key_the_provider_may_decline_is_offered_but_not_required(provider: Any) -> None:
    # Changed by design: drf-services kept a key its selector marked
    # ``InputRequired`` required beside a provider that may decline it. A
    # kernel read declared ``required=True`` is relaxed at dispatch when a
    # provider may fill it, so a call without it is not refused for it, and
    # the schema says what dispatch demands, as django-spec-agents' does.
    server = _server()
    _register(server, _PK, _TENANT, kwargs=provider)

    schema = _schema(server)

    assert set(schema["properties"]) == {"pk", "tenant"}
    assert schema["required"] == ["pk"]


def test_a_union_that_does_not_admit_unset_is_still_filled() -> None:
    # ``None`` is a value the provider answers with, not a decline.
    server = _server()
    _register(server, _PK, _TENANT, kwargs=_noneable_provider)

    assert set(_schema(server)["properties"]) == {"pk"}


@pytest.mark.parametrize(
    "provider",
    [_untyped_provider, _unresolvable_provider, lambda: {"tenant": "acme"}],
    ids=["dict", "unresolvable", "lambda"],
)
def test_an_untyped_provider_leaves_every_read_offered_and_none_required(provider: Any) -> None:
    # A provider whose return says nothing about its keys may fill any read,
    # so dispatch demands none of them of the caller and none is required.
    # Every one is still offered, since the provider fills none for certain.
    server = _server()
    _register(server, _PK, _TENANT, kwargs=provider)

    schema = _schema(server)

    assert set(schema["properties"]) == {"pk", "tenant"}
    assert "required" not in schema


@pytest.mark.django_db
def test_a_read_left_out_that_the_untyped_provider_does_not_fill_is_handed_back() -> None:
    # Not required is not unchecked: the kernel refuses the call where the
    # provider's answer is merged, and the refusal names the read.
    server = _server()
    _register(server, _PK, _TENANT, kwargs=lambda: {})

    out = server.call_tool("get", {"tenant": "acme"}, user=None).to_dict()

    assert out["isError"] is True
    assert "pk" in out["content"][0]["text"]


def test_a_key_holding_unset_inside_a_container_is_filled() -> None:
    # The provider cannot decline ``regions`` or ``labels``: each comes back as
    # a list or a dict, and only a value that is ``UNSET`` itself is dropped
    # from the pool. So both are filled and not advertised, and walking every
    # argument of every generic would find ``UnsetType`` inside them and offer both.
    server = _server()
    _register(
        server,
        _PK,
        Parameter("regions", "array", required=True, items="string"),
        Parameter("labels", "object", required=True),
        kwargs=_container_provider,
    )

    schema = _schema(server)

    assert set(schema["properties"]) == {"pk"}
    assert schema["required"] == ["pk"]


@pytest.mark.parametrize(
    "provider",
    [_unresolvable_field_provider, _unresolvable_parameter_provider],
    ids=["value-type", "parameter-type"],
)
def test_an_unresolvable_annotation_beside_the_return_costs_only_what_it_names(
    provider: Any,
) -> None:
    # A ``TypedDict`` value type that cannot be read leaves only its own key
    # undecided, offered and not required as a declinable key is; a parameter
    # typed with a name absent at runtime costs nothing, since only the return
    # annotation says which keys are filled. Either way ``region`` is filled,
    # ``tenant`` is offered, and ``pk`` stays required, where reading the whole
    # provider as untyped would offer ``region`` and require nothing.
    server = _server()
    _register(server, _PK, _TENANT, _REGION, kwargs=provider)

    schema = _schema(server)

    assert set(schema["properties"]) == {"pk", "tenant"}
    assert schema["required"] == ["pk"]


def test_a_spec_without_a_provider_keeps_every_declared_requirement() -> None:
    server = _server()
    _register(server, _PK, _TENANT)

    assert _schema(server)["required"] == ["pk", "tenant"]
