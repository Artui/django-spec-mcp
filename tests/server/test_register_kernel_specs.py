"""``register_tool``, ``register_tools`` and the kernel form of ``register_resource``.

The spine's file, asked of kernel specs throughout. Its one drf-services question,
whether ``register_tool`` refuses a drf-services spec, becomes whether it refuses
anything that is not a kernel spec, which is what holds once drf-services is gone.
Its check that ``scopes=`` is refused on the drf-services ``selector=`` form of
``register_resource`` is not carried, because that form goes with drf-services.
The original is kept beside this file as ``test_register_kernel_specs_drfs.py``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    Parameter,
    Parameters,
    RegisteredSpec,
    SelectorKind,
    SelectorSpec,
    ServerArguments,
    ServiceSpec,
    SpecRegistry,
    UnknownArguments,
    Unrestricted,
)

from django_spec_mcp import MCPServer
from django_spec_mcp.auth.permissions.scope_required import ScopeRequired
from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.types.output_format import OutputFormat
from django_spec_mcp.types.task_policy import TaskPolicy
from django_spec_mcp.types.tool_content_kind import ToolContentKind
from tests.testapp.auth_backend import HeaderAuthBackend
from tests.testapp.services import invoice_by_pk, list_invoices, record_invoice

OPEN = [Unrestricted()]


def _server(**config: Any) -> MCPServer:
    return MCPServer(
        name="t", auth_backend=HeaderAuthBackend(), config=replace(build_mcp_config(), **config)
    )


def _service(**kwargs: Any) -> ServiceSpec:
    return ServiceSpec(service=record_invoice, permissions=OPEN, **kwargs)


def _selector(kind: SelectorKind = SelectorKind.LIST) -> SelectorSpec:
    if kind is SelectorKind.LIST:
        return SelectorSpec(kind=kind, selector=list_invoices, permissions=OPEN)
    return SelectorSpec(
        kind=kind,
        selector=invoice_by_pk,
        reads=Parameters([Parameter("pk", "integer", required=True)]),
        permissions=OPEN,
    )


@pytest.mark.parametrize(
    "spec",
    [
        record_invoice,
        object(),
        RegisteredSpec(name="t", spec=ServiceSpec(service=record_invoice, permissions=OPEN)),
    ],
    ids=["callable", "object", "registry-entry"],
)
def test_register_tool_refuses_anything_but_a_kernel_spec(spec: Any) -> None:
    with pytest.raises(TypeError, match="takes a django_service_specs ServiceSpec or SelectorSpec"):
        _server().register_tool(spec, name="t")


@pytest.mark.parametrize("knob", [{"paginate": True}, {"max_page_size": 5}])
def test_a_service_tool_refuses_each_selector_knob(knob: dict[str, Any]) -> None:
    with pytest.raises(ImproperlyConfigured, match="a service tool's result is never paginated"):
        _server().register_tool(_service(), name="t", **knob)


def test_a_selector_tool_refuses_invalidates() -> None:
    with pytest.raises(ImproperlyConfigured, match="invalidates belongs to a service tool"):
        _server().register_tool(_selector(), name="t", invalidates=("invoices://",))


@pytest.mark.parametrize("knob", [{"paginate": True}, {"max_page_size": 5}])
def test_a_retrieve_tool_refuses_each_page_knob(knob: dict[str, Any]) -> None:
    with pytest.raises(ImproperlyConfigured, match="a RETRIEVE answers one row"):
        _server().register_tool(_selector(SelectorKind.RETRIEVE), name="t", **knob)


def test_a_list_tool_takes_the_page_knobs() -> None:
    binding = _server().register_tool(_selector(), name="t", paginate=True, max_page_size=5)
    assert (binding.paginate, binding.max_page_size, binding.kind) == (True, 5, SelectorKind.LIST)
    assert binding.is_selector is True


@pytest.mark.parametrize("policy", [TaskPolicy.OPTIONAL, TaskPolicy.REQUIRED])
def test_a_task_policy_is_not_yet_supported(policy: TaskPolicy) -> None:
    with pytest.raises(ImproperlyConfigured, match="is not yet supported for a kernel spec"):
        _server().register_tool(_service(), name="t", task_policy=policy)


def test_an_empty_scope_tuple_is_refused() -> None:
    with pytest.raises(ImproperlyConfigured, match="scopes=\\(\\) permits every caller"):
        _server().register_tool(_service(), name="t", scopes=())


def test_scopes_become_the_scope_gate_and_none_is_no_gate() -> None:
    gated = _server().register_tool(_service(), name="t", scopes=("invoices:write", "ledger"))
    (gate,) = gated.permissions
    assert isinstance(gate, ScopeRequired)
    assert gate.required_scopes() == ["invoices:write", "ledger"]
    assert _server().register_tool(_service(), name="t").permissions == ()


def test_passthrough_is_refused() -> None:
    with pytest.raises(ImproperlyConfigured, match="PASSTHROUGH is not honoured"):
        _server().register_tool(
            _service(), name="t", unknown_arguments=UnknownArguments.PASSTHROUGH
        )


def test_an_unguarded_spec_is_refused_and_a_scope_gate_does_not_guard_it() -> None:
    server = _server(require_tool_permissions=True)
    with pytest.raises(ImproperlyConfigured):
        server.register_tool(
            ServiceSpec(service=record_invoice), name="t", scopes=("invoices:write",)
        )


def test_the_hints_are_derived_and_an_explicit_one_wins() -> None:
    server = _server()
    assert server.register_tool(_service(idempotent=True), name="a").annotations == {
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
    }
    assert server.register_tool(
        _service(), name="b", annotations={"destructiveHint": False}
    ).annotations == {"readOnlyHint": False, "destructiveHint": False}
    retrieve = server.register_tool(_selector(SelectorKind.RETRIEVE), name="c")
    assert retrieve.annotations == {"readOnlyHint": True}
    assert retrieve.is_selector is True
    assert retrieve.kind is SelectorKind.RETRIEVE


def test_the_binding_refuses_an_output_schema_without_structured_content() -> None:
    with pytest.raises(ImproperlyConfigured, match="include_output_schema=True is incompatible"):
        _server().register_tool(
            _service(), name="t", include_output_schema=True, include_structured_content=False
        )


@pytest.mark.parametrize(
    ("output_schema", "structured"), [(True, True), (True, None), (False, False)]
)
def test_the_binding_takes_either_half_of_the_refused_pair_alone(
    output_schema: bool | None, structured: bool | None
) -> None:
    binding = _server().register_tool(
        _service(),
        name="t",
        include_output_schema=output_schema,
        include_structured_content=structured,
    )
    assert binding.include_output_schema is output_schema
    assert binding.include_structured_content is structured


def test_register_tools_registers_every_entry_with_its_overrides() -> None:
    registry = SpecRegistry(
        [
            RegisteredSpec(name="invoices.create", spec=_service()),
            RegisteredSpec(name="invoices.list", spec=_selector()),
        ]
    )
    server = _server()
    bindings = server.register_tools(
        registry, overrides={"invoices.list": {"paginate": True, "title": "Invoices"}}
    )
    assert [(b.name, b.paginate, b.title) for b in bindings] == [
        ("invoices.create", False, None),
        ("invoices.list", True, "Invoices"),
    ]
    assert all(isinstance(b, KernelToolBinding) for b in bindings)


def test_a_registry_entry_and_a_direct_registration_are_the_same_tool() -> None:
    """What ``tests/registry/test_register_tools_drfs.py`` asked of ``ToolDefinition``:
    bulk registration is less boilerplate, never a different binding."""
    knobs: dict[str, Any] = {"paginate": True, "title": "Invoices", "description": "All."}
    registry = SpecRegistry([RegisteredSpec(name="invoices.list", spec=_selector())])
    (bulk,) = _server().register_tools(registry, overrides={"invoices.list": knobs})
    direct = _server().register_tool(
        registry.get("invoices.list").spec, name="invoices.list", **knobs
    )
    assert bulk == direct


def test_an_empty_registry_registers_nothing() -> None:
    server = _server()
    assert server.register_tools(SpecRegistry([])) == ()
    assert len(server.tools) == 0


def test_register_tools_refuses_a_name_the_server_already_holds() -> None:
    server = _server()
    server.register_tool(_service(), name="invoices.create", description="d")
    registry = SpecRegistry([RegisteredSpec(name="invoices.create", spec=_service())])
    with pytest.raises(ValueError, match=r"Duplicate MCP tool name\(s\): \['invoices.create'\]"):
        server.register_tools(registry)


def test_register_tools_refuses_an_override_for_a_name_it_does_not_hold() -> None:
    registry = SpecRegistry([RegisteredSpec(name="invoices.create", spec=_service())])
    with pytest.raises(ValueError, match="overrides name specs not in this SpecRegistry"):
        _server().register_tools(registry, overrides={"invoices.creat": {}})


def test_the_output_format_and_content_knobs_reach_the_binding() -> None:
    binding = _server().register_tool(
        _service(),
        name="t",
        output_format="toon",
        content_kind=ToolContentKind.IMAGE,
        content_mime_type="image/png",
    )
    assert (binding.output_format, binding.content_kind) == (
        OutputFormat.TOON,
        ToolContentKind.IMAGE,
    )


def test_register_resource_refuses_anything_but_a_kernel_selector() -> None:
    with pytest.raises(
        TypeError, match="register_resource takes a django_service_specs SelectorSpec"
    ):
        _server().register_resource(_service(), name="r", uri_template="r://{pk}")


def test_register_resource_refuses_a_list() -> None:
    with pytest.raises(ImproperlyConfigured, match="its SelectorSpec is a RETRIEVE, not a LIST"):
        _server().register_resource(_selector(), name="r", uri_template="r://")


def test_register_resource_refuses_an_empty_scope_tuple() -> None:
    with pytest.raises(ImproperlyConfigured, match="scopes=\\(\\) permits every caller"):
        _server().register_resource(
            _selector(SelectorKind.RETRIEVE), name="r", uri_template="r://{pk}", scopes=()
        )


@pytest.mark.parametrize(
    ("knob", "value"),
    [
        ("selector", object()),
        ("output_serializer", object),
        ("permissions", []),
        ("mime_type", "text/csv"),
        ("encoding", __import__("django_spec_mcp").types.ResourceEncoding.TEXT),
    ],
)
def test_the_kernel_form_refuses_each_knob_of_the_selector_form(knob: str, value: Any) -> None:
    with pytest.raises(TypeError, match=f"{knob} belong to the selector= form"):
        _server().register_resource(
            _selector(SelectorKind.RETRIEVE), name="r", uri_template="r://{pk}", **{knob: value}
        )


def test_register_resource_needs_a_spec_or_a_selector() -> None:
    with pytest.raises(TypeError, match="it was given neither"):
        _server().register_resource(name="r", uri_template="r://{pk}")


def test_a_kernel_resource_carries_its_scope_gate() -> None:
    binding = _server().register_resource(
        _selector(SelectorKind.RETRIEVE), name="r", uri_template="r://{pk}", scopes=("read",)
    )
    (gate,) = binding.permissions
    assert gate.required_scopes() == ["read"]


def test_a_bare_string_scope_is_one_scope() -> None:
    server = _server()
    tool = server.register_tool(_service(), name="t", scopes="billing")
    resource = server.register_resource(
        _selector(SelectorKind.RETRIEVE), name="r", uri_template="r://{pk}", scopes="billing"
    )
    assert tool.scopes == resource.scopes == ("billing",)
    assert [p.required_scopes() for p in (*tool.permissions, *resource.permissions)] == [
        ["billing"],
        ["billing"],
    ]


def test_a_guarded_kernel_spec_registers_under_require_tool_permissions() -> None:
    """The spec's own permissions are what the guard check reads: a guarded
    tool and resource are accepted, unguarded ones refused."""
    server = _server(require_tool_permissions=True)
    assert server.register_tool(_service(), name="guarded").name == "guarded"
    assert (
        server.register_resource(
            _selector(SelectorKind.RETRIEVE), name="guarded", uri_template="g://{pk}"
        ).name
        == "guarded"
    )
    with pytest.raises(ImproperlyConfigured, match="declares no permissions"):
        server.register_resource(
            SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=invoice_by_pk,
                reads=Parameters([Parameter("pk", "integer", required=True)]),
            ),
            name="unguarded",
            uri_template="u://{pk}",
        )


def test_a_kernel_resource_refuses_a_completion_for_no_variable() -> None:
    with pytest.raises(ImproperlyConfigured, match="completions name argument"):
        _server().register_resource(
            _selector(SelectorKind.RETRIEVE),
            name="r",
            uri_template="r://{pk}",
            completions={"nope": lambda value: ["1"]},
        )


class _Source:
    """A ``SpecSource``: compiles to a kernel spec, but is not one."""

    def as_kernel_spec(self) -> ServiceSpec:
        return _service()


@pytest.mark.parametrize(
    ("late", "overrides", "refusal", "match"),
    [
        pytest.param(_Source(), {}, TypeError, "takes a django_service_specs", id="spec-source"),
        pytest.param(
            _service(),
            {"late": {"paginate": True}},
            ImproperlyConfigured,
            "paginate",
            id="knob",
        ),
        # Refused by the tool registry's own check rather than the binding's, so
        # only the trial registration of every entry keeps ``early`` out.
        pytest.param(
            _service(),
            {"late": {"server_arguments": ServerArguments(values={"tenant": 1})}},
            ImproperlyConfigured,
            "ServerArguments names tenant, which the spec does not declare",
            id="server-arguments",
        ),
        pytest.param(_service(), {}, ValueError, "Duplicate MCP tool name", id="name-taken"),
        # A declaration no call can run, refused by the kernel's ``check_seeds``.
        pytest.param(
            ServiceSpec(service=lambda *, other: other, permissions=OPEN),
            {},
            ImproperlyConfigured,
            r"MCP tool 'late': .*\['other'\], which nothing fills",
            id="a-keyword-nothing-fills",
        ),
    ],
)
def test_register_tools_registers_nothing_when_an_entry_is_refused(
    late: Any, overrides: dict[str, Any], refusal: type[Exception], match: str
) -> None:
    server = _server()
    server.register_tool(_service(), name="taken")
    registry = SpecRegistry(
        [
            RegisteredSpec(name="early", spec=_service()),
            RegisteredSpec(name="taken" if refusal is ValueError else "late", spec=late),
        ]
    )
    with pytest.raises(refusal, match=match):
        server.register_tools(registry, overrides=overrides)
    assert [binding.name for binding in server.tools.all()] == ["taken"]


def test_the_kernel_bindings_are_exported_beside_the_others() -> None:
    from django_spec_mcp.registry import types
    from django_spec_mcp.registry.types.kernel_resource_binding import KernelResourceBinding

    assert types.KernelToolBinding is KernelToolBinding
    assert types.KernelResourceBinding is KernelResourceBinding
    assert {"KernelToolBinding", "KernelResourceBinding"} <= set(types.__all__)


def test_a_binding_built_by_hand_reads_a_bare_string_scope_as_one() -> None:
    binding = KernelToolBinding(name="t", description="d", spec=_service(), scopes="billing")
    assert binding.scopes == ("billing",)
    assert [p.required_scopes() for p in binding.permissions] == [["billing"]]
