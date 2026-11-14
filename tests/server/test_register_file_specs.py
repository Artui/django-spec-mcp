from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import (
    DataclassValidator,
    FilePresenter,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import ResourceEncoding, ToolContentKind
from django_spec_mcp.registry.types.kernel_resource_binding import KernelResourceBinding
from django_spec_mcp.registry.types.kernel_tool_binding import KernelToolBinding
from django_spec_mcp.registry.types.utils import file_content_kind
from tests.testapp.kernel_shapes import InvoiceInput
from tests.utils import make_server

OPEN = [Unrestricted()]
PDF = FilePresenter("application/pdf", filename="invoice.pdf")
PNG = FilePresenter("image/png", filename="chart.png")


def _row(presenter: Any = PDF) -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda *, pk: b"%PDF",
        reads=Parameters([Parameter("pk", "integer", required=True)]),
        presenter=presenter,
        permissions=OPEN,
    )


def _service(presenter: Any = PDF, **fields: Any) -> ServiceSpec:
    return ServiceSpec(service=lambda: b"%PDF", permissions=OPEN, presenter=presenter, **fields)


def _resource(spec: SelectorSpec, **knobs: Any) -> KernelResourceBinding:
    return make_server().register_resource(spec, name="pdf", uri_template="pdf://{pk}", **knobs)


def _tool(spec: Any, **knobs: Any) -> KernelToolBinding:
    return make_server().register_tool(spec, name="t", description="d", **knobs)


# ----- a resource takes its encoding and mime type from the spec's file -----


def test_a_resource_over_a_file_is_a_blob_of_the_presenters_type() -> None:
    binding = _resource(_row())

    assert (binding.encoding, binding.mime_type) == (ResourceEncoding.BLOB, "application/pdf")


def test_a_resource_declaring_what_its_file_derives_registers() -> None:
    binding = _resource(_row(), encoding=ResourceEncoding.BLOB, mime_type="application/pdf")

    assert binding.encoding is ResourceEncoding.BLOB


@pytest.mark.parametrize("encoding", [ResourceEncoding.JSON, ResourceEncoding.TEXT])
def test_a_resource_encoding_disagreeing_with_its_file_is_refused(
    encoding: ResourceEncoding,
) -> None:
    with pytest.raises(ImproperlyConfigured, match=f"Resource 'pdf': encoding={encoding.name}"):
        _resource(_row(), encoding=encoding)


def test_a_resource_mime_type_disagreeing_with_its_file_is_refused() -> None:
    with pytest.raises(ImproperlyConfigured, match="mime_type='image/png' disagrees"):
        _resource(_row(), mime_type="image/png")


def test_a_blob_resource_over_a_spec_presenting_no_file_is_refused() -> None:
    # The documented bytes-returning presenter: a file is now declared on the spec.
    with pytest.raises(ImproperlyConfigured, match="encoding=BLOB serves a file"):
        _resource(_row(presenter=None), encoding=ResourceEncoding.BLOB)


def test_a_handbuilt_resource_binding_is_held_to_its_specs_file() -> None:
    spec = _row()
    with pytest.raises(ImproperlyConfigured, match="Resource 'hand': encoding=JSON disagrees"):
        KernelResourceBinding(
            name="hand",
            uri_template="h://{pk}",
            description=None,
            selector=spec.selector,
            spec=spec,
        )


# ----- a tool takes its content kind and mime type from the spec's file -----


@pytest.mark.parametrize(
    ("content_type", "kind"),
    [
        ("application/pdf", ToolContentKind.RESOURCE),
        ("text/csv", ToolContentKind.RESOURCE),
        ("image/png", ToolContentKind.IMAGE),
        ("Image/SVG+XML; charset=utf-8", ToolContentKind.IMAGE),
        ("audio/wav", ToolContentKind.AUDIO),
    ],
)
def test_a_service_tool_takes_its_kind_from_its_files_type(
    content_type: str, kind: ToolContentKind
) -> None:
    binding = _tool(_service(FilePresenter(content_type, filename="f")))

    assert (binding.content_kind, binding.content_mime_type) == (kind, content_type)
    assert file_content_kind(content_type) is kind


def test_a_file_tool_advertises_no_output_schema() -> None:
    server = make_server()
    server.register_tool(_service(), name="t", description="d")
    listed: Any = server.list_tools(user=None)

    assert "outputSchema" not in listed["tools"][0]


def test_a_tool_content_kind_disagreeing_with_its_file_is_refused() -> None:
    with pytest.raises(ImproperlyConfigured, match="content_kind=TEXT disagrees"):
        _tool(_service(PNG), content_kind=ToolContentKind.TEXT)


def test_a_tool_mime_type_disagreeing_with_its_file_is_refused() -> None:
    with pytest.raises(ImproperlyConfigured, match="content_mime_type='image/jpeg' disagrees"):
        _tool(_service(PNG), content_mime_type="image/jpeg")


@pytest.mark.parametrize("kind", [ToolContentKind.IMAGE, ToolContentKind.RESOURCE])
def test_a_file_kind_over_a_spec_presenting_no_file_is_refused(kind: ToolContentKind) -> None:
    # The documented bytes-returning chart: a file is now declared on the spec.
    with pytest.raises(ImproperlyConfigured, match=f"content_kind={kind.name} serves a file"):
        _tool(_service(None), content_kind=kind, content_mime_type="image/png")


def test_an_embedded_file_without_a_mime_type_is_refused() -> None:
    with pytest.raises(ImproperlyConfigured, match="RESOURCE requires content_mime_type"):
        _tool(_service(), content_mime_type=None)


def test_a_selector_tool_presenting_a_non_media_file_is_refused() -> None:
    with pytest.raises(ImproperlyConfigured, match="register_resource"):
        _tool(_row())


def test_a_selector_tool_presenting_an_image_is_an_image_tool() -> None:
    assert _tool(_row(PNG)).content_kind is ToolContentKind.IMAGE


def test_a_many_spec_presenting_a_file_is_refused_naming_the_tool() -> None:
    spec = _service(many=True, validator=DataclassValidator(InvoiceInput))

    with pytest.raises(ImproperlyConfigured, match="Tool 't': ServiceSpec presents a file"):
        _tool(spec)


def test_a_handbuilt_tool_binding_is_held_to_its_specs_file() -> None:
    with pytest.raises(ImproperlyConfigured, match="Tool 'hand': content_kind=TEXT disagrees"):
        KernelToolBinding(name="hand", description=None, spec=_service())
